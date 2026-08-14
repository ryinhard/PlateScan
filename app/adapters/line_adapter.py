"""LINE Webhook adapter：簽章驗證與 echo（M1 骨架階段）。

M1 僅驗證來源、記錄收到的內容並回傳 ack，不呼叫 LINE Reply/Push API——
真正的回覆/推播邏輯屬於 DESIGN-v6.md M6 里程碑範疇。
"""

import base64
import hashlib
import hmac
import logging

from fastapi import APIRouter, HTTPException, Request

from app.adapters.base import WebhookAck
from app.config import settings

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

    return WebhookAck(status="received", platform="line")
