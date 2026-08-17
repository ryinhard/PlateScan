"""Telegram Bot API 客戶端（M6：透過 sendMessage 送出回覆文字；M11：setMyCommands 註冊指令選單）。"""

import logging
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger("app.core.telegram_client")

_SEND_MESSAGE_URL = "https://api.telegram.org/bot{token}/sendMessage"
_SEND_PHOTO_URL = "https://api.telegram.org/bot{token}/sendPhoto"
_SET_MY_COMMANDS_URL = "https://api.telegram.org/bot{token}/setMyCommands"

# Telegram 指令名稱僅接受英文小寫/數字/底線，對應 app.core.dispatcher 的英文別名
# （中文指令詞如「今日」不受 Telegram 選單支援，僅列出英文別名供選單顯示）。
_BOT_COMMANDS = [
    {"command": "start", "description": "查看完整設定教學（首次使用推薦）"},
    {"command": "ok", "description": "結束目前餐次，觸發辨識並寫入紀錄"},
    {"command": "today", "description": "查詢今日累計營養素"},
    {"command": "chart", "description": "取得個人 PWA 儀表板連結"},
    {"command": "link", "description": "取得個人 Google Sheet 編輯連結"},
    {"command": "fix", "description": "修正最近一筆紀錄，例：/fix 熱量 700"},
    {"command": "fixhelp", "description": "顯示 /fix 指令的使用範例"},
    {"command": "setdate", "description": "修改最近一筆紀錄的日期，例：/setdate 2026/08/17"},
    {"command": "delete", "description": "刪除最近一筆紀錄"},
    {"command": "set", "description": "綁定/更換個人 Google Sheet，例：/set {Sheet ID}"},
    {"command": "setgoal", "description": "設定每日營養目標，例：/setgoal 熱量 2000"},
    {"command": "goal", "description": "查詢每日營養目標"},
    {"command": "setmeal", "description": "設定各餐次的開始時間，例：/setmeal 晚餐 17:30"},
    {"command": "meal", "description": "查詢目前餐次時段"},
    {"command": "cancel", "description": "清空目前緩衝區"},
    {"command": "help", "description": "顯示指令說明"},
]


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


async def send_photo(chat_id: int, photo_url: str, caption: Optional[str] = None) -> None:
    """呼叫 Telegram sendPhoto 送出圖片訊息，失敗僅記錄警告不中斷流程。"""
    url = _SEND_PHOTO_URL.format(token=settings.telegram_bot_token)
    body: dict = {"chat_id": chat_id, "photo": photo_url}
    if caption:
        body["caption"] = caption
    try:
        await _post(url, body)
    except Exception as exc:
        logger.warning("Telegram sendPhoto 送出失敗 chat_id=%s：%s", chat_id, exc)


async def set_my_commands() -> None:
    """向 Telegram 註冊指令選單（setMyCommands），供使用者於輸入框點出指令清單。

    冪等操作，可安全於每次應用程式啟動時呼叫；失敗僅記錄警告不中斷啟動流程。
    """
    url = _SET_MY_COMMANDS_URL.format(token=settings.telegram_bot_token)
    try:
        await _post(url, {"commands": _BOT_COMMANDS})
    except Exception as exc:
        logger.warning("Telegram setMyCommands 註冊失敗：%s", exc)
