"""Telegram Webhook adapter：secret_token 驗證、照片/文字事件分發（M3 階段）。

驗證來源後，將 photo 訊息中解析度最高的 file_id 與 text 訊息內容轉交
app.core.dispatcher 統一處理（暫存至緩衝區 / 觸發 ok 指令）。
真正的 sendMessage 回覆邏輯屬於 DESIGN-v6.md M6 里程碑範疇，M3 階段不呼叫 Telegram API。
"""

import hmac
import logging

from fastapi import APIRouter, HTTPException, Request

from app.adapters.base import WebhookAck
from app.config import settings
from app.core import dispatcher

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
    await _dispatch_update(update)

    return WebhookAck(status="received", platform="telegram")


async def _dispatch_update(update: dict) -> None:
    """將單一 Telegram update 轉交 dispatcher；非 message update 或缺少 chat.id 時忽略。"""
    message = update.get("message")
    if not message:
        return

    chat_id = message.get("chat", {}).get("id")
    if chat_id is None:
        logger.warning("Telegram message 缺少 chat.id，略過：%s", message)
        return

    user_key = f"tg:{chat_id}"
    photos = message.get("photo")
    text = message.get("text")

    if photos:
        # Telegram 依解析度由小到大排序 PhotoSize 陣列，取最後一張（最高解析度）的 file_id。
        await dispatcher.handle_photo(user_key, photos[-1].get("file_id", ""))
    elif text is not None:
        await dispatcher.handle_text(user_key, text)
