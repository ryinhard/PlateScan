"""Core Handler：訊息與指令分發（對應 docs/architecture.md 系統架構圖的 Core Handler 層）。

LINE / Telegram adapter 皆呼叫本模組的 handle_photo() / handle_text()，
統一轉換為 app.core.sheets 的 buffer / daily_log 讀寫操作，避免兩個 adapter
各自重複實作。handle_text() 回傳值為欲回覆使用者的文字（無需回覆則為 None），
實際透過 LINE Reply/Push（app.core.line_client）或 Telegram sendMessage
（app.core.telegram_client）送出由各自 adapter 的 BackgroundTasks 負責。
"""

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

from app.config import settings
from app.core import downloader, sheets, vision

logger = logging.getLogger("app.core.dispatcher")

_HELP_TEXT = (
    "支援的指令（中文或 / 開頭英文指令皆可，Telegram 可用選單快速輸入）：\n"
    "新手教學（/start）→ 查看完整設定教學（首次使用推薦）\n"
    "傳送照片/文字 → 追加至當前餐次緩衝區\n"
    "ok（/ok）→ 結束當前餐次，觸發辨識並寫入紀錄\n"
    "今日（/today）→ 查詢今日累計營養素\n"
    "圖表 / 分析（/chart）→ 取得個人 PWA 儀表板連結\n"
    "連結 / 原始表單（/link）→ 取得個人 Google Sheet 編輯連結\n"
    "修正 熱量 700（/fix）→ 修正最近一筆紀錄的數值或餐次（可一次修正多項）\n"
    "修改日期 2026/08/17（/setdate）→ 修正最近一筆紀錄的日期\n"
    "刪除（/delete）→ 刪除最近一筆紀錄\n"
    "設定 {Sheet ID}（/set）→ 綁定/更換個人 Google Sheet\n"
    "設定目標 熱量 2000（/setgoal）→ 設定每日營養目標\n"
    "目標（/goal）→ 查詢每日營養目標\n"
    "取消（/cancel）→ 清空目前緩衝區\n"
    "說明（/help）→ 顯示本列表"
)

_GOAL_REMINDER = (
    "如需設定每日營養目標，可輸入「原始表單」取得 Sheet 連結，開啟後在 goals 工作表直接填寫；"
    "或用指令「設定目標 熱量 2000」直接設定"
)
_CHART_PERMISSION_REMINDER = "提醒：若要使用圖表功能，請確認 Sheet 的「一般存取權」已設為「知道連結的人可檢視」"

_SHEET_URL_ID_PATTERN = re.compile(r"/d/([a-zA-Z0-9_-]+)")

# 不需要額外參數的指令：整則訊息（去除頭尾空白後）須完全等於下列其中一個
# 觸發詞才會被辨識為指令，避免誤判以指令詞開頭的一般餐點描述文字
# （例如「今日 吃了雞腿便當」應視為餐點描述，而非「今日」查詢指令）。
# 觸發詞比對一律轉小寫，中文字不受影響、英文與 /slash 別名不分大小寫。
_EXACT_COMMAND_ALIASES: dict[str, frozenset[str]] = {
    "ok": frozenset({"ok"}),
    "today": frozenset({"今日", "today"}),
    "chart": frozenset({"圖表", "分析", "chart"}),
    "link": frozenset({"連結", "原始表單", "link"}),
    "goal_query": frozenset({"目標", "goal"}),
    "cancel": frozenset({"取消", "cancel"}),
    "delete": frozenset({"刪除", "delete"}),
    "help": frozenset({"說明", "help"}),
    "onboarding": frozenset({"新手教學", "教學", "start"}),
}

# 需要額外參數的指令：僅比對第一個詞，其餘視為參數（例如「修正 熱量 700」）。
# 「設定目標」須排在別名集合中優先於「設定」比對（兩者是不同的完整詞，不會互相前綴衝突）。
_PREFIX_COMMAND_ALIASES: dict[str, frozenset[str]] = {
    "correct": frozenset({"修正", "fix"}),
    "correct_date": frozenset({"修改日期", "setdate"}),
    "goal_set": frozenset({"設定目標", "setgoal"}),
    "set_sheet": frozenset({"設定", "set"}),
}


def _normalize_first_token(token: str) -> str:
    """將指令詞正規化以提升容錯：轉小寫、去除 LINE/Telegram slash 前綴與
    Telegram 群組指令常見的 `@BotName` 後綴（例如 `/OK@PlateScanBot` → `ok`）。
    """
    normalized = token.lower()
    if normalized.startswith("/"):
        normalized = normalized[1:]
    at_index = normalized.find("@")
    if at_index != -1:
        normalized = normalized[:at_index]
    return normalized


def _resolve_command(stripped: str) -> tuple[Optional[str], list[str]]:
    """解析文字訊息開頭是否符合任一指令別名，回傳 (指令代稱, 參數列表)；
    非指令（一般餐點描述文字）時回傳 (None, [])。
    """
    parts = stripped.replace("　", " ").split()
    if not parts:
        return None, []

    first = _normalize_first_token(parts[0])

    if len(parts) == 1:
        for command, triggers in _EXACT_COMMAND_ALIASES.items():
            if first in triggers:
                return command, []

    for command, triggers in _PREFIX_COMMAND_ALIASES.items():
        if first in triggers:
            return command, parts[1:]

    return None, []


def is_ok_command(text: str) -> bool:
    """供 adapter 判斷是否需在同步階段先觸發 LINE Loading Animation。"""
    command, _ = _resolve_command(text.strip())
    return command == "ok"


def get_onboarding_text() -> str:
    """新手引導文字：對應「新手教學」「教學」「start」（Telegram 首次對話自動送出）指令，
    以及 LINE 加好友當下的 follow 事件（app/adapters/line_adapter.py）。

    動態組字串是因為 Service Account Email 需在執行時從已快取的憑證讀出，
    不額外存一份到 .env，避免兩處設定不同步。
    """
    email = sheets.get_service_account_email()
    return (
        "歡迎使用 PlateScan！設定步驟：\n"
        "1. 建立一個新的 Google 試算表（空白即可）\n"
        "2. 右上角「共用」，在同一個視窗完成兩件事：\n"
        f"(1) 新增編輯者：{email}\n"
        "(2) 把「一般存取權」改為「知道連結的任何人」＋「檢視者」（供之後使用圖表功能）\n"
        "3. 回來這裡輸入「設定 {Sheet ID}」（可直接貼網址），Bot 會自動建立需要的工作表\n"
        "\n"
        "綁定後即可開始：傳照片或輸入餐點文字 → 輸入「ok」觸發辨識記錄。\n"
        f"{_GOAL_REMINDER}。\n"
        "輸入「說明」可查看完整指令列表。"
    )


_CORRECT_FIELD_ALIASES = {
    "熱量": "calories",
    "碳水": "carbs_g",
    "蛋白質": "protein_g",
    "脂肪": "fat_g",
    "餐次": "meal",
    "日期": "date",
}
_VALID_MEAL_NAMES = {"早餐", "午餐", "晚餐", "宵夜"}
_CORRECT_FIELD_LIST = "、".join(_CORRECT_FIELD_ALIASES)

# 「修改日期 昨天」等相對日期說法，值為相對今日（Asia/Taipei）的天數差。
_RELATIVE_DATE_OFFSETS = {"今天": 0, "昨天": -1, "前天": -2}
# 日期分隔符號：接受 2026/08/17、2026-08-17、2026.08.17 三種常見寫法。
_DATE_SEPARATOR_PATTERN = re.compile(r"[/\-.]")


def _parse_date(raw: str) -> Optional[str]:
    """將使用者輸入的日期正規化為 YYYY/MM/DD（CLAUDE.md 規定的統一格式，
    前端 web/index.html 的 parseDate() 依此解析），無法解析時回傳 None。

    接受 2026/08/17、2026-08-17、2026.08.17、20260817、2026/8/7（未補零）、
    8/17（省略年份時補當年）以及「今天」「昨天」「前天」。
    刻意不阻擋未來日期（使用者可能預先調整），但 2026/02/30 這類
    不存在的日期會由 datetime() 自行擋下。
    """
    text = raw.strip()
    today = datetime.now(_TAIPEI_TZ)

    if text in _RELATIVE_DATE_OFFSETS:
        return (today + timedelta(days=_RELATIVE_DATE_OFFSETS[text])).strftime("%Y/%m/%d")

    if len(text) == 8 and text.isdigit():
        parts = [text[:4], text[4:6], text[6:]]
    else:
        parts = _DATE_SEPARATOR_PATTERN.split(text)

    if len(parts) == 2:  # 只給月/日時補上當年
        parts = [str(today.year), *parts]
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return None

    try:
        return datetime(int(parts[0]), int(parts[1]), int(parts[2])).strftime("%Y/%m/%d")
    except ValueError:
        return None


def _date_format_hint() -> str:
    """日期格式錯誤提示。範例日期帶入當天實際日期，比寫死的範例更好照著改。"""
    example = datetime.now(_TAIPEI_TZ).strftime("%Y/%m/%d")
    return (
        f"日期格式無法辨識，請使用「修改日期 {example}」（YYYY/MM/DD）\n"
        f"也可以輸入 {example.replace('/', '-')} 或 8/17（自動補今年），或直接輸入「昨天」「前天」"
    )

# 「設定目標 熱量 2000」的欄位別名：對應 goals 工作表 nutrient 值與預設單位。
_GOAL_FIELD_ALIASES: dict[str, tuple[str, str]] = {
    "熱量": ("calories", "kcal"),
    "碳水": ("carbs", "g"),
    "蛋白質": ("protein", "g"),
    "脂肪": ("fat", "g"),
}

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
    command, args = _resolve_command(stripped)

    if command == "ok":
        return await _handle_ok(user_key)

    if command == "today":
        return await _handle_today(user_key)

    if command == "chart":
        return await _handle_chart(user_key)

    if command == "link":
        return await _handle_link(user_key)

    if command == "help":
        return _HELP_TEXT

    if command == "onboarding":
        return get_onboarding_text()

    if command == "cancel":
        return await _handle_cancel(user_key)

    if command == "goal_query":
        return await _handle_goal(user_key)

    if command == "goal_set":
        return await _handle_goal_set(user_key, args)

    if command == "correct":
        return await _handle_correct(user_key, args)

    if command == "correct_date":
        # 「修改日期 2026/08/17」與「修正 日期 2026/08/17」是同一件事，
        # 兩種說法都保留，內部統一導向 _handle_correct 處理。
        if len(args) != 1:
            return _date_format_hint()
        return await _handle_correct(user_key, ["日期", args[0]])

    if command == "delete":
        return await _handle_delete(user_key)

    if command == "set_sheet":
        return await _handle_set(user_key, args)

    await sheets.append_buffer_item(user_key, "text", stripped)
    return None


async def _handle_ok(user_key: str) -> Optional[str]:
    items = await sheets.get_buffer_items(user_key)
    photo_ids = [item["content"] for item in items if item["item_type"] == "photo"]
    captions = [item["content"] for item in items if item["item_type"] == "text"]

    if not photo_ids and not captions:
        logger.info("user_key=%s 觸發 ok 指令，但緩衝區為空，略過辨識", user_key)
        return "緩衝區是空的，請先傳照片或輸入餐點描述"

    user = await sheets.get_user(user_key)
    if not user or not user.get("google_sheet_id"):
        logger.warning("user_key=%s 尚未綁定個人 Google Sheet，略過寫入 daily_log", user_key)
        return "尚未綁定個人 Google Sheet，請先輸入「設定 {Sheet ID}」完成綁定"

    # 用量控管必須擋在下載照片與呼叫 Gemini「之前」，否則額度照樣消耗；
    # 且超限時刻意不清空 buffer，讓使用者隔天直接傳 ok 就能接續辨識。
    allowed, used = await sheets.try_consume_daily_quota(
        user_key, settings.daily_ok_limit_per_user, datetime.now(_TAIPEI_TZ).strftime("%Y/%m/%d")
    )
    if not allowed:
        logger.warning("user_key=%s 今日辨識次數已達上限（%d 次），略過辨識", user_key, used)
        return (
            f"今日辨識次數已達上限（{settings.daily_ok_limit_per_user} 次），請明天再試。\n"
            "你的照片和描述都還留著，明天直接傳「ok」就會繼續辨識。"
        )

    # Gemini 按 token 計費、圖片 token 為成本大宗，故單次 ok 的張數也須設上限；
    # 超量時取前 N 張而非整批擋下，避免使用者卡住無法完成這一餐的紀錄。
    skipped_photos = max(0, len(photo_ids) - settings.max_photos_per_ok)
    if skipped_photos:
        logger.warning(
            "user_key=%s 單次 ok 照片數 %d 超過上限 %d，僅取前 %d 張",
            user_key,
            len(photo_ids),
            settings.max_photos_per_ok,
            settings.max_photos_per_ok,
        )
        photo_ids = photo_ids[: settings.max_photos_per_ok]

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

    reply = (
        f"已記錄「{meal}」：{item_names}\n"
        f"熱量 {calories} kcal ｜ 碳水 {carbs_g}g ｜ 蛋白質 {protein_g}g ｜ 脂肪 {fat_g}g"
    )
    if skipped_photos:
        reply += f"\n（單次最多辨識 {settings.max_photos_per_ok} 張照片，本次已略過後面 {skipped_photos} 張）"
    return reply


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

    link = f"{settings.web_base_url}/?sheet_id={user['google_sheet_id']}"
    return (
        f"{link}\n"
        "若圖表顯示不出資料，請確認 Sheet 的「一般存取權」已設為「知道連結的人可檢視」"
        "（在 Google Sheets 右上角「共用」視窗設定）"
    )


async def _handle_link(user_key: str) -> str:
    user = await sheets.get_user(user_key)
    if not user or not user.get("google_sheet_id"):
        return "尚未綁定個人 Google Sheet，請先輸入「設定 {Sheet ID}」完成綁定"

    return f"https://docs.google.com/spreadsheets/d/{user['google_sheet_id']}/edit"


def _validate_correct_value(field: str, field_label: str, raw_value: str) -> tuple[Any, Optional[str]]:
    """驗證單一修正欄位的值，回傳 (正規化後的值, 錯誤訊息)；驗證通過時錯誤訊息為 None。"""
    if field == "meal":
        if raw_value not in _VALID_MEAL_NAMES:
            return None, f"餐次僅能為：{'、'.join(_VALID_MEAL_NAMES)}"
        return raw_value, None

    if field == "date":
        parsed = _parse_date(raw_value)
        if parsed is None:
            return None, _date_format_hint()
        return parsed, None

    try:
        return int(raw_value), None
    except ValueError:
        try:
            return float(raw_value), None
        except ValueError:
            return None, f"「{field_label}」需要輸入數字，例如「修正 {field_label} 700」"


async def _handle_correct(user_key: str, args: list[str]) -> str:
    """處理「修正 熱量 700」「修正 餐次 早餐 日期 2026/08/17」等單欄或多欄修正。

    參數以「欄位 值」成對解析，全部驗證通過後才一次批次寫入，
    避免前面幾欄已寫進 Sheet、後面某欄才發現格式錯誤而留下半套狀態。
    """
    if not args or len(args) % 2 != 0:
        return (
            "指令格式錯誤，請使用「修正 熱量 700」或「修正 餐次 午餐」或「修正 日期 2026/08/17」\n"
            "也可以一次修正多項，例如「修正 熱量 2000 蛋白質 120」"
        )

    updates: dict[str, Any] = {}
    changes: list[str] = []
    for field_label, raw_value in zip(args[::2], args[1::2]):
        field = _CORRECT_FIELD_ALIASES.get(field_label)
        if field is None:
            return f"不支援的欄位「{field_label}」，可用欄位：{_CORRECT_FIELD_LIST}"
        if field in updates:
            return f"欄位「{field_label}」重複出現，請每個欄位只指定一次"

        value, error = _validate_correct_value(field, field_label, raw_value)
        if error is not None:
            return error
        updates[field] = value
        changes.append(f"{field_label} → {value}")

    user = await sheets.get_user(user_key)
    if not user or not user.get("google_sheet_id"):
        return "尚未綁定個人 Google Sheet，請先輸入「設定 {Sheet ID}」完成綁定"

    updated = await sheets.update_latest_daily_log_fields(user["google_sheet_id"], updates)
    if not updated:
        return "尚無可修正的紀錄"

    if len(updates) == 1:
        field_label, value = args[0], updates[next(iter(updates))]
        if "meal" in updates:
            return f"已將最近一筆紀錄的餐次修正為「{value}」"
        return f"已將最近一筆紀錄的{field_label}修正為 {value}"

    return "已修正最近一筆紀錄：\n" + "\n".join(changes)


async def _handle_delete(user_key: str) -> str:
    """處理「刪除」指令：刪掉最近一筆 daily_log 紀錄。

    刻意不做二次確認——紀錄為單筆且隨時可重新拍照補回，維護 pending 確認狀態
    的複雜度不划算；改為在回覆中列出完整的被刪內容，讓使用者能立即察覺刪錯。
    """
    user = await sheets.get_user(user_key)
    if not user or not user.get("google_sheet_id"):
        return "尚未綁定個人 Google Sheet，請先輸入「設定 {Sheet ID}」完成綁定"

    deleted = await sheets.delete_latest_daily_log_row(user["google_sheet_id"])
    if deleted is None:
        return "尚無可刪除的紀錄"

    return (
        "已刪除最近一筆紀錄：\n"
        f"{deleted['date']} {deleted['meal']} {deleted['items']}\n"
        f"熱量 {_format_number(deleted['calories'])}｜碳水 {_format_number(deleted['carbs_g'])}g"
        f"｜蛋白質 {_format_number(deleted['protein_g'])}g｜脂肪 {_format_number(deleted['fat_g'])}g\n"
        "若刪錯，請重新拍照或輸入描述後傳「ok」重新記錄"
    )


def _format_number(value: float) -> str:
    """daily_log 的營養素以 float 讀回，整數值去掉多餘的 .0 再顯示給使用者。"""
    return str(int(value)) if float(value).is_integer() else str(value)


def _extract_sheet_id(raw: str) -> str:
    """自 Google Sheets 網址（若使用者貼上完整連結）取出 Sheet ID，否則視為 ID 原樣回傳。"""
    match = _SHEET_URL_ID_PATTERN.search(raw)
    return match.group(1) if match else raw


async def _handle_set(user_key: str, args: list[str]) -> str:
    if len(args) != 1:
        return "指令格式錯誤，請使用「設定 {Sheet ID}」（可直接貼 Google Sheets 網址）"

    google_sheet_id = _extract_sheet_id(args[0])

    try:
        created = await sheets.ensure_user_worksheets(google_sheet_id)
    except Exception as exc:
        logger.warning("user_key=%s 設定 Sheet ID=%s 存取失敗：%s", user_key, google_sheet_id, exc)
        email = sheets.get_service_account_email()
        return (
            "無法存取這個 Google Sheet，請確認：\n"
            "1. Sheet ID／網址正確\n"
            f"2. 已將此 Sheet 分享給 {email}（權限選「編輯者」）\n"
            "完成後請重新輸入一次「設定 {Sheet ID}」"
        )

    existing_user = await sheets.get_user(user_key)
    display_name = existing_user["display_name"] if existing_user else ""

    await sheets.upsert_user(user_key, google_sheet_id=google_sheet_id, display_name=display_name)

    lines = [f"已綁定個人 Google Sheet（{google_sheet_id}）"]
    if created:
        lines.append(f"已自動建立缺少的工作表：{'、'.join(created)}")
    lines.append(_GOAL_REMINDER)
    lines.append(_CHART_PERMISSION_REMINDER)
    return "\n".join(lines)


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


def _parse_goal_value(raw: str) -> Optional[Any]:
    """目標數值優先解析為 int（避免 2000 被顯示成 2000.0），否則試 float，皆失敗回傳 None。"""
    try:
        return int(raw)
    except ValueError:
        try:
            return float(raw)
        except ValueError:
            return None


async def _handle_goal_set(user_key: str, args: list[str]) -> str:
    """處理「設定目標 熱量 2000」與一次設定多項的「設定目標 熱量 2000 蛋白質 120」。

    參數以「欄位 值」成對解析（與 `修正` 多欄位同一套語意），全部驗證通過後才開始寫入，
    避免前面幾項已寫進 goals 工作表、後面某項才發現格式錯誤而留下半套狀態。
    PWA（web/index.html 的 copyGoalCommands()）複製出的指令即為此單行多項格式。
    """
    if not args or len(args) % 2 != 0:
        return (
            "指令格式錯誤，請使用「設定目標 熱量 2000」，可用欄位：熱量、碳水、蛋白質、脂肪\n"
            "也可以一次設定多項，例如「設定目標 熱量 2000 蛋白質 120」"
        )

    # 每項為 (顯示用欄位標籤, goals 工作表的 nutrient 值, 單位, 目標數值)
    updates: list[tuple[str, str, str, Any]] = []
    seen: set[str] = set()
    for field_label, raw_value in zip(args[::2], args[1::2]):
        field_info = _GOAL_FIELD_ALIASES.get(field_label)
        if field_info is None:
            return f"不支援的欄位「{field_label}」，可用欄位：熱量、碳水、蛋白質、脂肪"

        nutrient, unit = field_info
        if nutrient in seen:
            return f"欄位「{field_label}」重複出現，請每個欄位只指定一次"
        seen.add(nutrient)

        target = _parse_goal_value(raw_value)
        if target is None:
            return f"「{field_label}」需要輸入數字，例如「設定目標 {field_label} 2000」"
        updates.append((field_label, nutrient, unit, target))

    user = await sheets.get_user(user_key)
    if not user or not user.get("google_sheet_id"):
        return "尚未綁定個人 Google Sheet，請先輸入「設定 {Sheet ID}」完成綁定"

    # goals 工作表為逐列 find-or-append（無批次 API），故多項時逐項寫入；
    # 格式驗證已全部完成，此處只剩網路/API 層級的失敗可能。
    for _, nutrient, unit, target in updates:
        await sheets.upsert_goal(user["google_sheet_id"], nutrient, target, unit)

    if len(updates) == 1:
        field_label, _, unit, target = updates[0]
        return f"已將每日{field_label}目標設定為 {target}{unit}"

    lines = "\n".join(f"{label} {target} {unit}" for label, _, unit, target in updates)
    return "已設定每日目標：\n" + lines
