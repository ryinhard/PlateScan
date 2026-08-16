"""Gemini Vision 多模態辨識模組（對應 docs/architecture.md 架構圖 Gemini 節點）。

輸入使用者暫存的照片二進位內容與文字描述，呼叫 Gemini 產生結構化的
餐點品項與營養素估算結果，供 M5 寫入使用者專屬 Sheet 的 daily_log 使用。
google-genai 為同步（blocking）SDK，因此以 asyncio.to_thread() 轉為非同步介面。
"""

import asyncio
import functools
import json
import logging
from typing import Any, Optional

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from app.config import settings

logger = logging.getLogger("app.core.vision")

_PROMPT = """你是一位專業臨床營養師，請依序執行以下步驟分析餐點照片與文字描述：

1. 品項拆解：列出照片中所有可見的食物品項。
2. 烹調與醬料評估：判斷烹調方式（清蒸、水煮、煎炸、勾芡等）。若為快炒或炸物，須將隱藏油脂計入 fat_g（不要低估）。
3. 份量估計：以常見容器（碗、盤、掌心）大小為基準，推估每個品項的重量。
4. 營養計算：依品項與份量分別估算 calories/carbs_g/protein_g/fat_g。

若使用者有提供文字描述（例如「半碗飯」「不加糖」），該描述以使用者輸入為準，優先於單純視覺估計。

僅回傳符合指定 schema 的 JSON，cot_reasoning 欄位請用繁體中文簡短說明（1-2 句）烹調方式與估重依據。
若完全無法從照片與文字中辨識出任何食物，items 回傳空陣列 []。"""

_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "cot_reasoning": {"type": "STRING"},
        "confidence_score": {"type": "NUMBER"},
        "items": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "name": {"type": "STRING"},
                    "calories": {"type": "INTEGER"},
                    "carbs_g": {"type": "INTEGER"},
                    "protein_g": {"type": "INTEGER"},
                    "fat_g": {"type": "INTEGER"},
                },
                "required": ["name", "calories", "carbs_g", "protein_g", "fat_g"],
            },
        },
    },
    "required": ["cot_reasoning", "confidence_score", "items"],
}


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


def _log_usage(model_name: str, image_count: int, response: Any) -> None:
    """記錄本次呼叫的 token 用量，供日後對照帳單回推「單張照片實際成本」，
    據以調整 settings.daily_ok_limit_per_user／max_photos_per_ok。

    usage_metadata 屬於觀測用途，取不到時（SDK 版本差異或欄位缺漏）僅略過不記，
    絕不可讓它影響辨識結果本身。
    """
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return

    logger.info(
        "Gemini 用量 model=%s images=%d prompt_tokens=%s output_tokens=%s total_tokens=%s",
        model_name,
        image_count,
        getattr(usage, "prompt_token_count", None),
        getattr(usage, "candidates_token_count", None),
        getattr(usage, "total_token_count", None),
    )


def _parse_response(text: str) -> Optional[dict[str, Any]]:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Gemini 回傳內容非合法 JSON，視為辨識失敗：%s", text)
        return None

    if not isinstance(parsed, dict) or not parsed.get("items"):
        return None

    return parsed


async def analyze_meal(images: list[bytes], captions: list[str]) -> Optional[dict[str, Any]]:
    """呼叫 Gemini Vision 辨識餐點照片與文字描述。

    回傳 {"cot_reasoning": str, "confidence_score": float, "items": [{"name", "calories",
    "carbs_g", "protein_g", "fat_g"}, ...]}；完全無法辨識出食物時回傳 None。
    最終的營養素加總刻意留給呼叫端（app/core/dispatcher.py）用 sum() 計算，
    不假手 Gemini 做多位數加總——圖像辨識與烹調方式判斷才是模型該做的事，
    確定性運算交給 Python 保證正確。

    主要模型（settings.gemini_model）回傳 503（服務過載）時，自動改用
    settings.gemini_fallback_model 重試一次，避免單一模型撞尖峰流量導致整次辨識失敗。
    """
    if not images and not captions:
        return None

    contents = _build_contents(images, captions)
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=_RESPONSE_SCHEMA,
    )

    def _generate(model_name: str) -> str:
        client = _get_client()
        response = client.models.generate_content(model=model_name, contents=contents, config=config)
        _log_usage(model_name, len(images), response)
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
