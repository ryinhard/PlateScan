"""LINE Messaging API 客戶端（對應 docs/architecture.md 第 5 節：Loading Animation / Reply / Push）。

僅封裝 Display Loading Animation、Reply Message（0 成本）、Push Message（降級 fallback，
計入額度）三支 API 呼叫，實際依處理耗時決定使用哪一支交由 app.adapters.line_adapter 判斷。
"""

import logging

import httpx

from app.config import settings

logger = logging.getLogger("app.core.line_client")

_LOADING_URL = "https://api.line.me/v2/bot/chat/loading/start"
_REPLY_URL = "https://api.line.me/v2/bot/message/reply"
_PUSH_URL = "https://api.line.me/v2/bot/message/push"

_DEFAULT_LOADING_SECONDS = 60


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.line_channel_access_token}",
        "Content-Type": "application/json",
    }


async def _post(url: str, json_body: dict) -> None:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(url, headers=_headers(), json=json_body)
        response.raise_for_status()


async def start_loading_animation(
    user_id: str, seconds: int = _DEFAULT_LOADING_SECONDS
) -> None:
    """觸發「AI 輸入中...」載入動畫，失敗僅記錄警告不中斷後續處理流程。"""
    try:
        await _post(_LOADING_URL, {"chatId": user_id, "loadingSeconds": seconds})
    except Exception as exc:
        logger.warning("啟動 LINE Loading Animation 失敗 user_id=%s：%s", user_id, exc)


async def reply_message(reply_token: str, text: str) -> None:
    """以 replyToken 回覆訊息（0 成本）。reply token 過期／已使用時會拋出例外，
    由呼叫端捕捉後改用 push_message() 降級 fallback。
    """
    await _post(
        _REPLY_URL,
        {"replyToken": reply_token, "messages": [{"type": "text", "text": text}]},
    )


async def push_message(user_id: str, text: str) -> None:
    """以 Push Message 送出訊息（降級 fallback，計入額度），失敗僅記錄警告。"""
    try:
        await _post(
            _PUSH_URL, {"to": user_id, "messages": [{"type": "text", "text": text}]}
        )
    except Exception as exc:
        logger.warning("LINE Push Message 送出失敗 user_id=%s：%s", user_id, exc)
