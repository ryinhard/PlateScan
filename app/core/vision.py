"""Gemini Vision 多模態辨識模組（對應 docs/architecture.md 架構圖 Gemini 節點）。

輸入使用者暫存的照片二進位內容與文字描述，呼叫 Gemini 產生結構化的
餐點品項與營養素估算結果，供 M5 寫入使用者專屬 Sheet 的 daily_log 使用。
google-genai 為同步（blocking）SDK，因此以 asyncio.to_thread() 轉為非同步介面。
"""

import asyncio
import functools
import json
import logging
from typing import Any

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from app.config import settings

logger = logging.getLogger("app.core.vision")

_PROMPT = """你是專業營養師。請分析以下餐點照片與文字描述，估算每個食物品項的營養素。

僅回傳一個 JSON 陣列（不要有任何額外文字或 Markdown 標記），每個元素格式為：
{"name": "食物名稱", "calories": 數字, "carbs_g": 數字, "protein_g": 數字, "fat_g": 數字}

若完全無法辨識出任何食物，回傳空陣列 []。"""


@functools.lru_cache(maxsize=1)
def _get_client() -> genai.Client:
    """建立並快取 Gemini Client（僅在首次呼叫時完成金鑰設定）。"""
    return genai.Client(api_key=settings.gemini_api_key)


def _build_contents(images: list[bytes], captions: list[str]) -> list[Any]:
    contents: list[Any] = [_PROMPT]
    if captions:
        contents.append("文字描述：" + "、".join(captions))
    for image in images:
        contents.append(types.Part.from_bytes(data=image, mime_type="image/jpeg"))
    return contents


def _parse_response(text: str) -> list[dict[str, Any]]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if "\n" in cleaned:
            cleaned = cleaned.split("\n", 1)[1]

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("Gemini 回傳內容非合法 JSON，視為辨識失敗：%s", text)
        return []

    return parsed if isinstance(parsed, list) else []


async def analyze_meal(images: list[bytes], captions: list[str]) -> list[dict[str, Any]]:
    """呼叫 Gemini Vision 辨識餐點照片與文字描述，回傳結構化品項清單。

    主要模型（settings.gemini_model）回傳 503（服務過載）時，自動改用
    settings.gemini_fallback_model 重試一次，避免單一模型撞尖峰流量導致整次辨識失敗。
    """
    if not images and not captions:
        return []

    contents = _build_contents(images, captions)

    def _generate(model_name: str) -> str:
        client = _get_client()
        response = client.models.generate_content(model=model_name, contents=contents)
        return response.text

    try:
        text = await asyncio.to_thread(_generate, settings.gemini_model)
    except genai_errors.ServerError:
        if not settings.gemini_fallback_model or settings.gemini_fallback_model == settings.gemini_model:
            raise
        logger.warning(
            "主模型 %s 回應 503，改用備用模型 %s 重試", settings.gemini_model, settings.gemini_fallback_model
        )
        text = await asyncio.to_thread(_generate, settings.gemini_fallback_model)

    return _parse_response(text)
