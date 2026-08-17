"""管理用 Google Sheet（`users` & `buffer` 工作表）讀寫模組。

透過 Service Account 存取 `ADMIN_SHEET_ID` 指向的管理用 Google Sheet。
gspread 為同步（blocking）函式庫，因此所有實際的 API 呼叫皆封裝於內部同步
函式中，並以 `asyncio.to_thread()` 轉為非同步執行，對外一律提供 async 介面。

`buffer` 工作表在使用者連續傳送多張照片時，可能被同一 user_key 的多個
webhook 事件並行讀寫，因此以 user_key 為單位建立 `asyncio.Lock()`，
確保同一使用者的暫存讀寫序列化執行，防範競態條件（Race Condition）。
"""

import asyncio
import functools
from datetime import datetime, timezone
from typing import Any, Optional

import gspread
from google.oauth2.service_account import Credentials

from app.config import settings

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

USERS_WORKSHEET = "users"
BUFFER_WORKSHEET = "buffer"
DAILY_LOG_WORKSHEET = "daily_log"
GOALS_WORKSHEET = "goals"


@functools.lru_cache(maxsize=1)
def _get_client() -> gspread.Client:
    """建立並快取 gspread client（僅在首次呼叫時執行一次授權）。"""
    credentials = Credentials.from_service_account_file(
        settings.google_application_credentials, scopes=_SCOPES
    )
    return gspread.authorize(credentials)


@functools.lru_cache(maxsize=1)
def _get_admin_sheet() -> gspread.Spreadsheet:
    """開啟並快取管理用 Google Sheet（ADMIN_SHEET_ID 對應的試算表）。"""
    return _get_client().open_by_key(settings.admin_sheet_id)


def _get_worksheet(name: str) -> gspread.Worksheet:
    """依工作表名稱（users / buffer）取得對應的 worksheet 物件。"""
    return _get_admin_sheet().worksheet(name)


# --- 使用者個人 Google Sheet（google_sheet_id 對應，M5 daily_log 讀寫用） ---
# 每位使用者的 Sheet ID 不同，無法沿用 _get_admin_sheet() 的單一快取，
# 改以 dict 依 google_sheet_id 個別快取 Spreadsheet 物件，避免重複開表。

_user_spreadsheets: dict[str, gspread.Spreadsheet] = {}


def _get_user_spreadsheet(google_sheet_id: str) -> gspread.Spreadsheet:
    """開啟並快取使用者個人 Google Sheet（依 google_sheet_id 個別快取）。"""
    if google_sheet_id not in _user_spreadsheets:
        _user_spreadsheets[google_sheet_id] = _get_client().open_by_key(google_sheet_id)
    return _user_spreadsheets[google_sheet_id]


def _get_user_worksheet(google_sheet_id: str, name: str) -> gspread.Worksheet:
    """依 google_sheet_id 與工作表名稱（daily_log / goals）取得對應的 worksheet 物件。"""
    return _get_user_spreadsheet(google_sheet_id).worksheet(name)


# --- Async Lock：以 user_key（或使用者個人 Sheet 讀寫時的 google_sheet_id）為單位，
# 序列化同一把鎖對應資源的讀寫 ---

_locks: dict[str, asyncio.Lock] = {}
_locks_guard = asyncio.Lock()


async def _get_lock(user_key: str) -> asyncio.Lock:
    """取得（或建立）對應 user_key 的 asyncio.Lock。

    以 `_locks_guard` 保護字典的建立過程，避免多個並行事件同時檢查
    「key 不存在」而各自建立出不同的 Lock 實例。
    """
    async with _locks_guard:
        if user_key not in _locks:
            _locks[user_key] = asyncio.Lock()
        return _locks[user_key]


# --- users 工作表：user_key | display_name | google_sheet_id | is_active | created_at
#                   | daily_count | count_date | meal_schedule ---
# F/G 欄為 Gemini 每日用量計數（M13），由 try_consume_daily_quota() 獨立維護；
# H 欄為自訂餐次時段（M17），由 upsert_meal_schedule() 獨立維護；
# upsert_user() 只更新 B:E 欄，三者互不干擾。


def _row_to_user(row: list[str]) -> dict[str, Any]:
    return {
        "user_key": row[0] if len(row) > 0 else "",
        "display_name": row[1] if len(row) > 1 else "",
        "google_sheet_id": row[2] if len(row) > 2 else "",
        "is_active": (row[3] if len(row) > 3 else "").strip().upper() == "TRUE",
        "created_at": row[4] if len(row) > 4 else "",
        "meal_schedule": row[7] if len(row) > 7 else "",
    }


async def get_user(user_key: str) -> Optional[dict[str, Any]]:
    """依 user_key 查詢 users 工作表，找不到時回傳 None。"""

    def _read() -> Optional[dict[str, Any]]:
        worksheet = _get_worksheet(USERS_WORKSHEET)
        for row in worksheet.get_all_values()[1:]:
            if row and row[0] == user_key:
                return _row_to_user(row)
        return None

    return await asyncio.to_thread(_read)


async def upsert_user(
    user_key: str, google_sheet_id: str, display_name: str = ""
) -> None:
    """新增或更新 users 工作表中對應 user_key 的一筆設定。

    對應「綁定 {Sheet ID}」指令：使用者第一次綁定時新增列，
    之後更換 Google Sheet 時更新既有列。
    """

    def _write() -> None:
        worksheet = _get_worksheet(USERS_WORKSHEET)
        now = datetime.now(timezone.utc).strftime("%Y/%m/%d")
        cell = worksheet.find(user_key, in_column=1)
        if cell is None:
            worksheet.append_row([user_key, display_name, google_sheet_id, "TRUE", now])
        else:
            worksheet.update(
                range_name=f"B{cell.row}:E{cell.row}",
                values=[[display_name, google_sheet_id, "TRUE", now]],
            )

    lock = await _get_lock(user_key)
    async with lock:
        await asyncio.to_thread(_write)


def _parse_count(raw: Any) -> int:
    """將 users 工作表 daily_count 欄位轉為整數，空白/非數字/負數一律視為 0。

    gspread 的 get_all_values() 回傳字串，但不假設型別——使用者也可能在
    Sheet 上手動把該格改成數字格式或填入奇怪的內容。
    """
    try:
        return max(0, int(str(raw).strip()))
    except (TypeError, ValueError):
        return 0


async def try_consume_daily_quota(user_key: str, limit: int, today: str) -> tuple[bool, int]:
    """檢查並扣用 user_key 當日的 Gemini 辨識次數配額（對應「ok」指令）。

    回傳 (是否允許本次辨識, 本次計入後的已用次數)；被擋下時第二個值為目前已用次數。
    以 users 工作表的 daily_count / count_date 兩欄持久化計數——記憶體計數在
    Cloud Run 容器重啟或多實例時會歸零、形同虛設，故必須寫回 Sheet。

    「讀取→判斷→寫回」整段都在 _get_lock(user_key) 內完成，避免同一使用者
    連續觸發 ok 時發生少算。count_date 與傳入的 today（Asia/Taipei 日期字串）
    不同即視為新的一天並歸零，因此不需要另外排程重置。
    找不到該使用者（尚未綁定）時一律放行，交由呼叫端既有的綁定檢查處理。
    """

    def _write() -> tuple[bool, int]:
        worksheet = _get_worksheet(USERS_WORKSHEET)
        # gspread 列號從 1 起算且第 1 列為表頭，故資料列自第 2 列開始
        for row_number, row in enumerate(worksheet.get_all_values()[1:], start=2):
            if not row or row[0] != user_key:
                continue

            stored_date = row[6] if len(row) > 6 else ""
            used = _parse_count(row[5]) if len(row) > 5 else 0
            if stored_date != today:
                used = 0

            if used >= limit:
                return False, used

            worksheet.update(range_name=f"F{row_number}:G{row_number}", values=[[used + 1, today]])
            return True, used + 1

        return True, 0

    lock = await _get_lock(user_key)
    async with lock:
        return await asyncio.to_thread(_write)


async def upsert_meal_schedule(user_key: str, schedule_str: str) -> None:
    """更新 users 工作表中對應 user_key 的自訂餐次時段（H 欄 meal_schedule）。

    對應「設定餐次」指令。比照 try_consume_daily_quota() 只更新單欄的做法，
    不觸及 upsert_user() 維護的 B:E 範圍。schedule_str 為空字串代表還原成預設時段
    （對應「設定餐次 預設」）。找不到該使用者（尚未綁定）時略過寫入，
    交由呼叫端既有的綁定檢查處理。
    """

    def _write() -> None:
        worksheet = _get_worksheet(USERS_WORKSHEET)
        for row_number, row in enumerate(worksheet.get_all_values()[1:], start=2):
            if row and row[0] == user_key:
                worksheet.update(range_name=f"H{row_number}", values=[[schedule_str]])
                return

    lock = await _get_lock(user_key)
    async with lock:
        await asyncio.to_thread(_write)


# --- buffer 工作表：user_key | item_type | content | created_at ---
# content 存放 LINE message_id 或 Telegram file_id（item_type="photo"）或文字內容（item_type="text"）。


def _row_to_buffer_item(row: list[str]) -> dict[str, Any]:
    return {
        "user_key": row[0] if len(row) > 0 else "",
        "item_type": row[1] if len(row) > 1 else "",
        "content": row[2] if len(row) > 2 else "",
        "created_at": row[3] if len(row) > 3 else "",
    }


async def append_buffer_item(user_key: str, item_type: str, content: str) -> None:
    """將一筆暫存項目（照片 message_id/file_id 或文字）追加至 buffer 工作表。"""

    def _write() -> None:
        worksheet = _get_worksheet(BUFFER_WORKSHEET)
        now = datetime.now(timezone.utc).isoformat()
        worksheet.append_row([user_key, item_type, content, now])

    lock = await _get_lock(user_key)
    async with lock:
        await asyncio.to_thread(_write)


async def get_buffer_items(user_key: str) -> list[dict[str, Any]]:
    """讀取指定使用者目前所有暫存項目，依寫入順序回傳。"""

    def _read() -> list[dict[str, Any]]:
        worksheet = _get_worksheet(BUFFER_WORKSHEET)
        rows = worksheet.get_all_values()[1:]
        return [_row_to_buffer_item(row) for row in rows if row and row[0] == user_key]

    lock = await _get_lock(user_key)
    async with lock:
        return await asyncio.to_thread(_read)


async def clear_buffer(user_key: str) -> None:
    """清除指定使用者的所有暫存項目（對應「取消」指令，或 ok 觸發辨識後的清空）。"""

    def _write() -> None:
        worksheet = _get_worksheet(BUFFER_WORKSHEET)
        all_values = worksheet.get_all_values()
        # 由下往上刪除，避免刪除列後造成尚未處理列的列號位移
        for idx in range(len(all_values) - 1, 0, -1):
            if all_values[idx] and all_values[idx][0] == user_key:
                worksheet.delete_rows(idx + 1)  # gspread 列號從 1 起算

    lock = await _get_lock(user_key)
    async with lock:
        await asyncio.to_thread(_write)


# --- daily_log 工作表（使用者個人 Sheet）：date | meal | items | calories | carbs_g | protein_g | fat_g ---
# 對應 ok 指令辨識完成後的彙整寫入，以及「今日」指令的查詢加總（app/core/dispatcher.py）。


def _row_to_daily_log(row: list[str]) -> dict[str, Any]:
    def _to_number(value: str) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    return {
        "date": row[0] if len(row) > 0 else "",
        "meal": row[1] if len(row) > 1 else "",
        "items": row[2] if len(row) > 2 else "",
        "calories": _to_number(row[3] if len(row) > 3 else ""),
        "carbs_g": _to_number(row[4] if len(row) > 4 else ""),
        "protein_g": _to_number(row[5] if len(row) > 5 else ""),
        "fat_g": _to_number(row[6] if len(row) > 6 else ""),
        "confidence": _to_number(row[7] if len(row) > 7 else ""),
    }


async def append_daily_log(
    google_sheet_id: str,
    date: str,
    meal: str,
    items: str,
    calories: float,
    carbs_g: float,
    protein_g: float,
    fat_g: float,
    confidence: float = 0,
) -> None:
    """將一筆彙整後的餐次紀錄寫入使用者個人 Sheet 的 daily_log 工作表。

    confidence 為 Gemini 回傳的 confidence_score（0.0~1.0），供之後觀察辨識信心度
    分佈、評估是否需要在信心度過低時切換更高階模型重算使用，目前僅記錄不觸發任何行為。
    """

    def _write() -> None:
        worksheet = _get_user_worksheet(google_sheet_id, DAILY_LOG_WORKSHEET)
        worksheet.append_row([date, meal, items, calories, carbs_g, protein_g, fat_g, confidence])

    lock = await _get_lock(google_sheet_id)
    async with lock:
        await asyncio.to_thread(_write)


async def get_daily_log_rows(google_sheet_id: str, date: str) -> list[dict[str, Any]]:
    """讀取使用者個人 Sheet 中指定日期（格式 YYYY/MM/DD）的所有 daily_log 紀錄。"""

    def _read() -> list[dict[str, Any]]:
        worksheet = _get_user_worksheet(google_sheet_id, DAILY_LOG_WORKSHEET)
        rows = worksheet.get_all_values()[1:]
        return [_row_to_daily_log(row) for row in rows if row and row[0] == date]

    lock = await _get_lock(google_sheet_id)
    async with lock:
        return await asyncio.to_thread(_read)


async def delete_latest_daily_log_row(google_sheet_id: str) -> Optional[dict[str, Any]]:
    """刪除 daily_log 最後一列（最近一筆紀錄），並回傳被刪除的內容供回覆使用者確認。

    對應「刪除」指令。daily_log 尚無任何紀錄（只有表頭列或全空）時回傳 None。
    刻意先讀出內容再刪除，讓使用者能從回覆中看到刪掉的究竟是哪一筆。
    """

    def _write() -> Optional[dict[str, Any]]:
        worksheet = _get_user_worksheet(google_sheet_id, DAILY_LOG_WORKSHEET)
        all_values = worksheet.get_all_values()
        if len(all_values) <= 1:
            return None
        last_row = len(all_values)  # gspread 列號從 1 起算，含表頭列
        deleted = _row_to_daily_log(all_values[last_row - 1])
        worksheet.delete_rows(last_row)
        return deleted

    lock = await _get_lock(google_sheet_id)
    async with lock:
        return await asyncio.to_thread(_write)


_DAILY_LOG_FIELD_COLUMNS = {
    "date": "A",
    "meal": "B",
    "calories": "D",
    "carbs_g": "E",
    "protein_g": "F",
    "fat_g": "G",
}


async def update_latest_daily_log_fields(google_sheet_id: str, fields: dict[str, Any]) -> bool:
    """修正使用者個人 Sheet 中 daily_log 最後一列（最近一筆紀錄）的一或多個欄位。

    對應「修正 熱量 700」「修正 餐次 午餐 日期 2026/08/17」等指令。fields 的 key
    須為 _DAILY_LOG_FIELD_COLUMNS 的其中一個。呼叫端必須先完成全部欄位的格式驗證
    再呼叫本函式，這裡以 batch_update 一次送出，避免多欄位修正時前幾欄已寫入、
    後面某欄才發現有問題而留下半套狀態。

    daily_log 尚無任何紀錄（只有表頭列或全空）時回傳 False，呼叫端應提示使用者
    尚無可修正的紀錄。
    """

    def _write() -> bool:
        worksheet = _get_user_worksheet(google_sheet_id, DAILY_LOG_WORKSHEET)
        all_values = worksheet.get_all_values()
        if len(all_values) <= 1:
            return False
        last_row = len(all_values)  # gspread 列號從 1 起算，含表頭列
        worksheet.batch_update(
            [
                {"range": f"{_DAILY_LOG_FIELD_COLUMNS[field]}{last_row}", "values": [[value]]}
                for field, value in fields.items()
            ]
        )
        return True

    lock = await _get_lock(google_sheet_id)
    async with lock:
        return await asyncio.to_thread(_write)


# --- goals 工作表（使用者個人 Sheet）：nutrient | target | unit ---
# 三個讀寫來源：Bot 的「目標」查詢與「設定目標」寫入（app/core/dispatcher.py），
# 以及 PWA（web/index.html 的 loadGoalsFromGviz()）透過公開的 gviz/tq 端點唯讀取用，
# 用來顯示各項營養素的達成率。PWA 無法回寫，其「複製指令」按鈕產生的是「設定目標」指令文字。


def _row_to_goal(row: list[str]) -> dict[str, Any]:
    return {
        "nutrient": row[0] if len(row) > 0 else "",
        "target": row[1] if len(row) > 1 else "",
        "unit": row[2] if len(row) > 2 else "",
    }


async def get_goals(google_sheet_id: str) -> list[dict[str, Any]]:
    """讀取使用者個人 Sheet 的 goals 工作表所有列（nutrient/target/unit）。"""

    def _read() -> list[dict[str, Any]]:
        worksheet = _get_user_worksheet(google_sheet_id, GOALS_WORKSHEET)
        rows = worksheet.get_all_values()[1:]
        return [_row_to_goal(row) for row in rows if row and row[0]]

    lock = await _get_lock(google_sheet_id)
    async with lock:
        return await asyncio.to_thread(_read)


_DAILY_LOG_HEADER = ["date", "meal", "items", "calories", "carbs_g", "protein_g", "fat_g", "confidence"]
_GOALS_HEADER = ["nutrient", "target", "unit"]


async def ensure_user_worksheets(google_sheet_id: str) -> list[str]:
    """確保使用者個人 Sheet 具備 daily_log／goals 工作表，缺少時自動建立並寫入表頭。

    對應「綁定 {Sheet ID}」指令：使用者只需建立一個空白 Sheet 分享編輯權限即可，
    不必自行複製範本或手動建立分頁。回傳這次實際新增的工作表名稱（供組裝提示訊息），
    兩個工作表皆已存在時回傳空列表。開啟 Sheet 失敗（ID 錯誤或尚未分享編輯權限）時，
    底層 gspread 例外原樣往外拋出，由呼叫端（dispatcher._handle_set）轉換成使用者看得懂的錯誤訊息。
    """

    def _write() -> list[str]:
        spreadsheet = _get_user_spreadsheet(google_sheet_id)
        existing_titles = {worksheet.title for worksheet in spreadsheet.worksheets()}
        created: list[str] = []

        if DAILY_LOG_WORKSHEET not in existing_titles:
            worksheet = spreadsheet.add_worksheet(
                title=DAILY_LOG_WORKSHEET, rows=1000, cols=len(_DAILY_LOG_HEADER)
            )
            worksheet.append_row(_DAILY_LOG_HEADER)
            created.append(DAILY_LOG_WORKSHEET)

        if GOALS_WORKSHEET not in existing_titles:
            worksheet = spreadsheet.add_worksheet(title=GOALS_WORKSHEET, rows=100, cols=len(_GOALS_HEADER))
            worksheet.append_row(_GOALS_HEADER)
            created.append(GOALS_WORKSHEET)

        return created

    lock = await _get_lock(google_sheet_id)
    async with lock:
        return await asyncio.to_thread(_write)


def get_service_account_email() -> str:
    """回傳目前 Service Account 的 email，供 Bot 回覆訊息引導使用者設定 Google Sheet 共用權限。

    gspread 6.x 的憑證物件掛在 Client.http_client.auth（並非 Client.auth），已用
    inspect.getsource() 實際核對過 gspread 原始碼確認此存取路徑。
    """
    return _get_client().http_client.auth.service_account_email


async def upsert_goal(google_sheet_id: str, nutrient: str, target: Any, unit: str) -> None:
    """新增或更新 goals 工作表中對應 nutrient 的一筆目標值。

    對應「設定目標 熱量 2000」指令：nutrient 已存在時更新該列，否則新增一列。
    """

    def _write() -> None:
        worksheet = _get_user_worksheet(google_sheet_id, GOALS_WORKSHEET)
        cell = worksheet.find(nutrient, in_column=1)
        if cell is None:
            worksheet.append_row([nutrient, target, unit])
        else:
            worksheet.update(range_name=f"B{cell.row}:C{cell.row}", values=[[target, unit]])

    lock = await _get_lock(google_sheet_id)
    async with lock:
        await asyncio.to_thread(_write)
