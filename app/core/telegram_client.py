"""Telegram Bot API 客戶端（M6：實際透過 sendMessage 送出回覆文字）。"""

import logging

import httpx

from app.config import settings

logger = logging.getLogger("app.core.telegram_client")

_SEND_MESSAGE_URL = "https://api.telegram.org/bot{token}/sendMessage"


async def _post(url: str, json_body: dict) -> None:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(url, json=json_body)
        response.raise_for_status()


async def send_message(chat_id: int, text: str) -> None:
    """呼叫 Telegram sendMessage 送出回覆文字，失敗僅記錄警告不中斷流程。"""
    url = _SEND_MESSAGE_URL.format(token=settings.telegram_bot_token)
    try:
        await _post(url, {"chat_id": chat_id, "text": text})
    except Exception as exc:
        logger.warning("Telegram sendMessage 送出失敗 chat_id=%s：%s", chat_id, exc)
