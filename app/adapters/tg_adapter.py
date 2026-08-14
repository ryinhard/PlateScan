"""Telegram Webhook adapter：secret_token 驗證與 echo（M1 骨架階段）。

M1 僅驗證來源、記錄收到的內容並回傳 ack，不呼叫 Telegram sendMessage API——
真正的回覆/推播邏輯屬於 DESIGN-v6.md M6 里程碑範疇。
"""

import hmac
import logging

from fastapi import APIRouter, HTTPException, Request

from app.adapters.base import WebhookAck
from app.config import settings

logger = logging.getLogger("app.adapters.telegram")

router = APIRouter(prefix="/webhook", tags=["telegram"])


@router.post("/telegram", response_model=WebhookAck)
async def telegram_webhook(request: Request) -> WebhookAck:
    """接收 Telegram webhook update：驗證 secret_token 後記錄內容並回傳 ack。

    此驗證機制對應 Telegram 呼叫 setWebhook 時帶入的 secret_token 參數，
    之後每次 webhook 請求會帶上 X-Telegram-Bot-Api-Secret-Token header。
    """
    token = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not token or not hmac.compare_digest(token, settings.telegram_webhook_secret):
        raise HTTPException(
            status_code=401, detail="Invalid X-Telegram-Bot-Api-Secret-Token"
        )

    update = await request.json()
    logger.info(
        "收到 Telegram webhook：update_id=%s, has_message=%s",
        update.get("update_id"),
        "message" in update,
    )

    return WebhookAck(status="received", platform="telegram")
