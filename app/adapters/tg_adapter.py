"""Telegram Webhook adapter：secret_token 驗證、照片/文字事件分發，並串接 M6 回覆機制。

驗證來源後，photo 訊息中最大解析度的 file_id 直接同步暫存至緩衝區（處理快速、
無需回覆）；text 訊息交由 BackgroundTasks 背景處理（避免 ok 指令觸發的 Gemini
辨識等耗時流程拖慢 webhook 回應），處理完成後以 sendMessage 送出回覆文字。
"""

import hmac
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from app.adapters.base import WebhookAck
from app.config import settings
from app.core import dispatcher, telegram_client

logger = logging.getLogger("app.adapters.telegram")

router = APIRouter(prefix="/webhook", tags=["telegram"])


@router.post("/telegram", response_model=WebhookAck)
async def telegram_webhook(
    request: Request, background_tasks: BackgroundTasks
) -> WebhookAck:
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
    await _dispatch_update(update, background_tasks)

    return WebhookAck(status="received", platform="telegram")


async def _dispatch_update(update: dict, background_tasks: BackgroundTasks) -> None:
    """將單一 Telegram update 轉交處理；非 message update 或缺少 chat.id 時忽略。"""
    message = update.get("message")
    if not message:
        return

    chat_id = message.get("chat", {}).get("id")
    if chat_id is None:
        logger.warning("Telegram message 缺少 chat.id，略過：%s", message)
        return

    user_key = f"tg:{chat_id}"
    logger.info("Telegram 訊息來自 user_key=%s", user_key)
    photos = message.get("photo")
    text = message.get("text")

    if photos:
        # Telegram 依解析度由小到大排序 PhotoSize 陣列，取最後一張（最高解析度）的 file_id。
        await dispatcher.handle_photo(user_key, photos[-1].get("file_id", ""))
        return

    if text is not None:
        background_tasks.add_task(_process_text_reply, user_key, text, chat_id)


async def _process_text_reply(user_key: str, text: str, chat_id: int) -> None:
    """背景執行文字指令處理，完成後以 Telegram sendMessage 送出回覆文字。"""
    reply_text = await dispatcher.handle_text(user_key, text)
    if reply_text:
        await telegram_client.send_message(chat_id, reply_text)
