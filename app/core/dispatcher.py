"""Core Handler：訊息與指令分發（對應 docs/architecture.md 系統架構圖的 Core Handler 層）。

LINE / Telegram adapter 皆呼叫本模組的 handle_photo() / handle_text()，
統一轉換為 app.core.sheets 的 buffer / daily_log 讀寫操作，避免兩個 adapter
各自重複實作。handle_text() 回傳值為欲回覆使用者的文字（無需回覆則為 None），
實際透過 LINE Reply/Push（app.core.line_client）或 Telegram sendMessage
（app.core.telegram_client）送出由各自 adapter 的 BackgroundTasks 負責。
"""

import logging
import re
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from app.config import settings
from app.core import downloader, sheets, vision

logger = logging.getLogger("app.core.dispatcher")

OK_COMMAND = "ok"
TODAY_COMMAND = "今日"
CORRECT_COMMAND = "修正"
CHART_COMMANDS = {"圖表", "分析"}
SET_COMMAND = "設定"
HELP_COMMAND = "說明"
CANCEL_COMMAND = "取消"
GOAL_COMMAND = "目標"

_HELP_TEXT = (
    "支援的指令：\n"
    "傳送照片/文字 → 追加至當前餐次緩衝區\n"
    "ok → 結束當前餐次，觸發辨識並寫入紀錄\n"
    "今日 → 查詢今日累計營養素\n"
    "圖表 / 分析 → 取得個人 PWA 儀表板連結\n"
    "修正 熱量 700 → 修正最近一筆紀錄的數值或餐次\n"
    "設定 {Sheet ID} → 綁定/更換個人 Google Sheet\n"
    "取消 → 清空目前緩衝區\n"
    "目標 → 查詢每日營養目標\n"
    "說明 → 顯示本列表"
)

_SHEET_URL_ID_PATTERN = re.compile(r"/d/([a-zA-Z0-9_-]+)")

_CORRECT_FIELD_ALIASES = {
    "熱量": "calories",
    "碳水": "carbs_g",
    "蛋白質": "protein_g",
    "脂肪": "fat_g",
    "餐次": "meal",
}
_VALID_MEAL_NAMES = {"早餐", "午餐", "晚餐", "宵夜"}

_TAIPEI_TZ = ZoneInfo("Asia/Taipei")

# 依觸發 ok 指令當下的時段判斷餐次名稱（含端點時分別為 05:00~10:59 早餐、
# 11:00~16:59 午餐、17:00~21:59 晚餐，其餘時段歸類為宵夜）。
_MEAL_TIME_RANGES = [
    (5, 10, "早餐"),
    (11, 16, "午餐"),
    (17, 21, "晚餐"),
]


def _determine_meal(now: datetime) -> str:
    for start_hour, end_hour, meal_name in _MEAL_TIME_RANGES:
        if start_hour <= now.hour <= end_hour:
            return meal_name
    return "宵夜"


async def handle_photo(user_key: str, photo_id: str) -> None:
    """將照片代碼（LINE message_id 或 Telegram file_id）追加至當前餐次緩衝區。"""
    await sheets.append_buffer_item(user_key, "photo", photo_id)


async def handle_text(user_key: str, text: str) -> Optional[str]:
    """處理文字訊息，並確保任何未預期錯誤都會轉換成使用者看得懂的回覆文字。

    背景任務（LINE/Telegram adapter 的 BackgroundTasks）若拋出例外會被框架吃掉、
    不會送出任何 Reply/Push，使用者將完全收不到回應；因此在此統一攔截，
    避免呼叫端（adapter）需要各自重複處理例外。
    """
    try:
        return await _dispatch_text(user_key, text)
    except Exception:
        logger.exception("user_key=%s 處理文字訊息時發生未預期錯誤", user_key)
        return "處理時發生錯誤，請稍後再試一次"


async def _dispatch_text(user_key: str, text: str) -> Optional[str]:
    """依指令分派文字訊息：`ok` 觸發辨識與寫入、`今日` 查詢累計，其餘文字視為餐點描述追加至緩衝區。"""
    stripped = text.strip()

    if stripped.lower() == OK_COMMAND:
        return await _handle_ok(user_key)

    if stripped == TODAY_COMMAND:
        return await _handle_today(user_key)

    if stripped in CHART_COMMANDS:
        return await _handle_chart(user_key)

    if stripped == HELP_COMMAND:
        return _HELP_TEXT

    if stripped == CANCEL_COMMAND:
        return await _handle_cancel(user_key)

    if stripped == GOAL_COMMAND:
        return await _handle_goal(user_key)

    parts = stripped.split()
    if parts and parts[0] == CORRECT_COMMAND:
        return await _handle_correct(user_key, parts[1:])

    if parts and parts[0] == SET_COMMAND:
        return await _handle_set(user_key, parts[1:])

    await sheets.append_buffer_item(user_key, "text", stripped)
    return None


async def _handle_ok(user_key: str) -> Optional[str]:
    items = await sheets.get_buffer_items(user_key)
    photo_ids = [item["content"] for item in items if item["item_type"] == "photo"]
    captions = [item["content"] for item in items if item["item_type"] == "text"]

    if not photo_ids and not captions:
        logger.info("user_key=%s 觸發 ok 指令，但緩衝區為空，略過辨識", user_key)
        return None

    user = await sheets.get_user(user_key)
    if not user or not user.get("google_sheet_id"):
        logger.warning("user_key=%s 尚未綁定個人 Google Sheet，略過寫入 daily_log", user_key)
        return "尚未綁定個人 Google Sheet，請先輸入「設定 {Sheet ID}」完成綁定"

    images = await downloader.download_photos(user_key, photo_ids)
    result = await vision.analyze_meal(images, captions)
    logger.info(
        "user_key=%s 觸發 ok 指令，緩衝區 %d 張照片（成功下載 %d 張）+ %d 則文字，Gemini 辨識出 %d 筆品項",
        user_key,
        len(photo_ids),
        len(images),
        len(captions),
        len(result["items"]) if result else 0,
    )
    if result:
        logger.info(
            "user_key=%s Gemini cot_reasoning=%s confidence_score=%s",
            user_key,
            result.get("cot_reasoning"),
            result.get("confidence_score"),
        )

    if not result:
        await sheets.clear_buffer(user_key)
        return "無法辨識出餐點內容，請重新拍照或加上文字描述後再試一次"

    result_items = result["items"]
    now = datetime.now(_TAIPEI_TZ)
    date = now.strftime("%Y/%m/%d")
    meal = _determine_meal(now)
    item_names = "、".join(item.get("name", "") for item in result_items)
    calories = sum(item.get("calories", 0) for item in result_items)
    carbs_g = sum(item.get("carbs_g", 0) for item in result_items)
    protein_g = sum(item.get("protein_g", 0) for item in result_items)
    fat_g = sum(item.get("fat_g", 0) for item in result_items)
    confidence = result.get("confidence_score", 0)

    await sheets.append_daily_log(
        user["google_sheet_id"], date, meal, item_names, calories, carbs_g, protein_g, fat_g, confidence
    )
    await sheets.clear_buffer(user_key)

    return (
        f"已記錄「{meal}」：{item_names}\n"
        f"熱量 {calories} kcal ｜ 碳水 {carbs_g}g ｜ 蛋白質 {protein_g}g ｜ 脂肪 {fat_g}g"
    )


async def _handle_today(user_key: str) -> str:
    user = await sheets.get_user(user_key)
    if not user or not user.get("google_sheet_id"):
        return "尚未綁定個人 Google Sheet，請先輸入「設定 {Sheet ID}」完成綁定"

    date = datetime.now(_TAIPEI_TZ).strftime("%Y/%m/%d")
    rows = await sheets.get_daily_log_rows(user["google_sheet_id"], date)

    if not rows:
        return f"{date} 尚無飲食紀錄"

    calories = sum(row["calories"] for row in rows)
    carbs_g = sum(row["carbs_g"] for row in rows)
    protein_g = sum(row["protein_g"] for row in rows)
    fat_g = sum(row["fat_g"] for row in rows)

    return (
        f"{date} 累計（共 {len(rows)} 筆紀錄）：\n"
        f"熱量 {calories} kcal ｜ 碳水 {carbs_g}g ｜ 蛋白質 {protein_g}g ｜ 脂肪 {fat_g}g"
    )


async def _handle_chart(user_key: str) -> str:
    user = await sheets.get_user(user_key)
    if not user or not user.get("google_sheet_id"):
        return "尚未綁定個人 Google Sheet，請先輸入「設定 {Sheet ID}」完成綁定"

    if not settings.web_base_url:
        logger.warning("WEB_BASE_URL 尚未設定，無法組出 PWA 儀表板連結")
        return "PWA 儀表板尚未部署，請聯絡管理員"

    return f"{settings.web_base_url}/?sheet_id={user['google_sheet_id']}"


async def _handle_correct(user_key: str, args: list[str]) -> str:
    if len(args) != 2:
        return "指令格式錯誤，請使用「修正 熱量 700」或「修正 餐次 午餐」"

    field_label, raw_value = args
    field = _CORRECT_FIELD_ALIASES.get(field_label)
    if field is None:
        return f"不支援的欄位「{field_label}」，可用欄位：熱量、碳水、蛋白質、脂肪、餐次"

    value: Any
    if field == "meal":
        if raw_value not in _VALID_MEAL_NAMES:
            return f"餐次僅能為：{'、'.join(_VALID_MEAL_NAMES)}"
        value = raw_value
    else:
        try:
            value = int(raw_value)
        except ValueError:
            try:
                value = float(raw_value)
            except ValueError:
                return f"「{field_label}」需要輸入數字，例如「修正 {field_label} 700」"

    user = await sheets.get_user(user_key)
    if not user or not user.get("google_sheet_id"):
        return "尚未綁定個人 Google Sheet，請先輸入「設定 {Sheet ID}」完成綁定"

    updated = await sheets.update_latest_daily_log_field(user["google_sheet_id"], field, value)
    if not updated:
        return "尚無可修正的紀錄"

    if field == "meal":
        return f"已將最近一筆紀錄的餐次修正為「{value}」"
    return f"已將最近一筆紀錄的{field_label}修正為 {value}"


def _extract_sheet_id(raw: str) -> str:
    """自 Google Sheets 網址（若使用者貼上完整連結）取出 Sheet ID，否則視為 ID 原樣回傳。"""
    match = _SHEET_URL_ID_PATTERN.search(raw)
    return match.group(1) if match else raw


async def _handle_set(user_key: str, args: list[str]) -> str:
    if len(args) != 1:
        return "指令格式錯誤，請使用「設定 {Sheet ID}」（可直接貼 Google Sheets 網址）"

    google_sheet_id = _extract_sheet_id(args[0])
    existing_user = await sheets.get_user(user_key)
    display_name = existing_user["display_name"] if existing_user else ""

    await sheets.upsert_user(user_key, google_sheet_id=google_sheet_id, display_name=display_name)
    return f"已綁定個人 Google Sheet（{google_sheet_id}）"


async def _handle_cancel(user_key: str) -> str:
    await sheets.clear_buffer(user_key)
    return "已清空目前緩衝區"


async def _handle_goal(user_key: str) -> str:
    user = await sheets.get_user(user_key)
    if not user or not user.get("google_sheet_id"):
        return "尚未綁定個人 Google Sheet，請先輸入「設定 {Sheet ID}」完成綁定"

    goals = await sheets.get_goals(user["google_sheet_id"])
    if not goals:
        return "尚未設定每日營養目標"

    lines = [f"{goal['nutrient']} {goal['target']}{goal['unit']}" for goal in goals]
    return "每日營養目標：\n" + "\n".join(lines)
