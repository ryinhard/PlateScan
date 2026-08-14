"""LINE Webhook adapter：簽章驗證、照片/文字事件分發（M3 階段）。

驗證來源後，將 image 訊息的 message_id 與 text 訊息內容轉交
app.core.dispatcher 統一處理（暫存至緩衝區 / 觸發 ok 指令）。
真正的 Reply/Push 回覆邏輯屬於 DESIGN-v6.md M6 里程碑範疇，M3 階段不呼叫 LINE API。
"""

import base64
import hashlib
import hmac
import logging

from fastapi import APIRouter, HTTPException, Request

from app.adapters.base import WebhookAck
from app.config import settings
from app.core import dispatcher

logger = logging.getLogger("app.adapters.line")

router = APIRouter(prefix="/webhook", tags=["line"])


def _verify_signature(body: bytes, signature: str) -> bool:
    """依 LINE 官方規格，以 channel secret 對原始 body 計算 HMAC-SHA256 並比對簽章。"""
    digest = hmac.new(
        settings.line_channel_secret.encode("utf-8"), body, hashlib.sha256
    ).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected, signature)


@router.post("/line", response_model=WebhookAck)
async def line_webhook(request: Request) -> WebhookAck:
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
        await _dispatch_event(event)

    return WebhookAck(status="received", platform="line")


async def _dispatch_event(event: dict) -> None:
    """將單一 LINE message 事件轉交 dispatcher；非 message 事件或缺少 userId 時忽略。"""
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
    elif message_type == "text":
        await dispatcher.handle_text(user_key, message.get("text", ""))
