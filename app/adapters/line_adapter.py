"""LINE Webhook adapter：簽章驗證、照片/文字事件分發，並串接 M6 回覆機制。

驗證來源後，image 訊息的 message_id 直接同步暫存至緩衝區（處理快速、無需回覆）；
text 訊息交由 BackgroundTasks 背景處理（避免 Gemini 辨識等耗時流程拖慢 webhook
回應），若為 `ok` 指令會先同步呼叫 Loading Animation API 顯示「AI 輸入中...」。
背景任務完成後，於 replyToken 有效期內（設計文件訂為 18 秒）以 Reply Message
（0 成本）回覆，超時或 Reply 失敗則降級改用 Push Message 送出。
"""

import base64
import hashlib
import hmac
import logging
import time

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from app.adapters.base import WebhookAck
from app.config import settings
from app.core import dispatcher, line_client

logger = logging.getLogger("app.adapters.line")

router = APIRouter(prefix="/webhook", tags=["line"])

_REPLY_TOKEN_LIMIT_SECONDS = 18


def _verify_signature(body: bytes, signature: str) -> bool:
    """依 LINE 官方規格，以 channel secret 對原始 body 計算 HMAC-SHA256 並比對簽章。"""
    digest = hmac.new(
        settings.line_channel_secret.encode("utf-8"), body, hashlib.sha256
    ).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected, signature)


@router.post("/line", response_model=WebhookAck)
async def line_webhook(request: Request, background_tasks: BackgroundTasks) -> WebhookAck:
    """接收 LINE webhook 事件：驗證簽章後記錄事件數量並回傳 ack。"""
    body = await request.body()
    signature = request.headers.get("X-Line-Signature", "")

    if not signature or not _verify_signature(body, signature):
        raise HTTPException(status_code=401, detail="Invalid X-Line-Signature")

    payload = await request.json()
    events = payload.get("events", [])
    logger.info("收到 LINE webhook：%d 筆事件", len(events))
    for event in events:
        logger.info("LINE event type=%s", event.get("type"))
        await _dispatch_event(event, background_tasks)

    return WebhookAck(status="received", platform="line")


async def _dispatch_event(event: dict, background_tasks: BackgroundTasks) -> None:
    """將單一 LINE message 事件轉交處理；非 message 事件或缺少 userId 時忽略。"""
    if event.get("type") != "message":
        return

    user_id = event.get("source", {}).get("userId")
    if not user_id:
        logger.warning("LINE message 事件缺少 source.userId，略過：%s", event)
        return

    user_key = f"line:{user_id}"
    message = event.get("message", {})
    message_type = message.get("type")

    if message_type == "image":
        await dispatcher.handle_photo(user_key, message.get("id", ""))
        return

    if message_type != "text":
        return

    text = message.get("text", "")
    reply_token = event.get("replyToken", "")

    if text.strip().lower() == dispatcher.OK_COMMAND:
        await line_client.start_loading_animation(user_id)

    background_tasks.add_task(
        _process_text_reply, user_key, text, user_id, reply_token, time.monotonic()
    )


async def _process_text_reply(
    user_key: str, text: str, user_id: str, reply_token: str, received_at: float
) -> None:
    """背景執行文字指令處理，並依耗時決定以 Reply（0 成本）或 Push（降級 fallback）回覆。"""
    reply_text = await dispatcher.handle_text(user_key, text)
    if not reply_text:
        return

    elapsed = time.monotonic() - received_at
    if reply_token and elapsed <= _REPLY_TOKEN_LIMIT_SECONDS:
        try:
            await line_client.reply_message(reply_token, reply_text)
            return
        except Exception as exc:
            logger.warning(
                "LINE Reply 失敗（耗時 %.1fs），改用 Push user_key=%s：%s",
                elapsed,
                user_key,
                exc,
            )

    await line_client.push_message(user_id, reply_text)
