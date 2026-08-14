"""app.core.dispatcher 單元測試：照片/文字分發邏輯與 ok 指令觸發機制。

以 monkeypatch 替換 app.core.sheets 的讀寫函式，不觸及真實 Google Sheets，
專注驗證 dispatcher 對 buffer 操作的呼叫是否正確。
"""

import pytest

from app.core import dispatcher, downloader, sheets, vision


@pytest.fixture()
def spy_append(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, str]]:
    calls: list[tuple[str, str, str]] = []

    async def _fake_append(user_key: str, item_type: str, content: str) -> None:
        calls.append((user_key, item_type, content))

    monkeypatch.setattr(sheets, "append_buffer_item", _fake_append)
    return calls


async def test_handle_photo_appends_photo_item_to_buffer(
    spy_append: list[tuple[str, str, str]],
):
    await dispatcher.handle_photo("line:U1", "msg-1")

    assert spy_append == [("line:U1", "photo", "msg-1")]


async def test_handle_text_appends_non_ok_text_as_buffer_item(
    spy_append: list[tuple[str, str, str]],
):
    await dispatcher.handle_text("line:U1", "  雞腿便當  ")

    assert spy_append == [("line:U1", "text", "雞腿便當")]


@pytest.fixture()
def fake_user(monkeypatch: pytest.MonkeyPatch):
    async def _fake_get_user(user_key: str):
        return {
            "user_key": user_key,
            "display_name": "小明",
            "google_sheet_id": "sheet-abc",
            "is_active": True,
            "created_at": "2026/08/14",
        }

    monkeypatch.setattr(sheets, "get_user", _fake_get_user)


@pytest.mark.parametrize("command", ["ok", "OK", " ok ", "Ok\n"])
async def test_handle_text_ok_command_downloads_photos_and_calls_gemini(
    monkeypatch: pytest.MonkeyPatch,
    spy_append: list[tuple[str, str, str]],
    fake_user,
    command: str,
):
    read_calls: list[str] = []
    clear_calls: list[str] = []
    download_calls: list[tuple[str, list[str]]] = []
    analyze_calls: list[tuple[list[bytes], list[str]]] = []
    append_log_calls: list[tuple] = []

    async def _fake_get_buffer_items(user_key: str):
        read_calls.append(user_key)
        return [
            {"user_key": user_key, "item_type": "photo", "content": "msg-1"},
            {"user_key": user_key, "item_type": "text", "content": "雞腿便當"},
        ]

    async def _fake_clear_buffer(user_key: str) -> None:
        clear_calls.append(user_key)

    async def _fake_download_photos(user_key: str, photo_ids: list[str]) -> list[bytes]:
        download_calls.append((user_key, photo_ids))
        return [b"fake-image-bytes"]

    async def _fake_analyze_meal(images: list[bytes], captions: list[str]):
        analyze_calls.append((images, captions))
        return [{"name": "雞腿便當", "calories": 650, "carbs_g": 80, "protein_g": 30, "fat_g": 20}]

    async def _fake_append_daily_log(google_sheet_id, date, meal, items, calories, carbs_g, protein_g, fat_g):
        append_log_calls.append((google_sheet_id, date, meal, items, calories, carbs_g, protein_g, fat_g))

    monkeypatch.setattr(sheets, "get_buffer_items", _fake_get_buffer_items)
    monkeypatch.setattr(sheets, "clear_buffer", _fake_clear_buffer)
    monkeypatch.setattr(sheets, "append_daily_log", _fake_append_daily_log)
    monkeypatch.setattr(downloader, "download_photos", _fake_download_photos)
    monkeypatch.setattr(vision, "analyze_meal", _fake_analyze_meal)

    reply = await dispatcher.handle_text("line:U1", command)

    assert read_calls == ["line:U1"]
    assert spy_append == []  # ok 指令不應被當成一般文字暫存
    assert download_calls == [("line:U1", ["msg-1"])]
    assert analyze_calls == [([b"fake-image-bytes"], ["雞腿便當"])]
    assert clear_calls == ["line:U1"]  # 寫入 daily_log 成功後應清空緩衝區

    assert len(append_log_calls) == 1
    google_sheet_id, date, meal, items, calories, carbs_g, protein_g, fat_g = append_log_calls[0]
    assert google_sheet_id == "sheet-abc"
    assert items == "雞腿便當"
    assert (calories, carbs_g, protein_g, fat_g) == (650, 80, 30, 20)
    assert meal in {"早餐", "午餐", "晚餐", "宵夜"}

    assert reply is not None
    assert "雞腿便當" in reply
    assert "650" in reply


async def test_handle_text_ok_command_without_bound_sheet_skips_write(
    monkeypatch: pytest.MonkeyPatch,
    spy_append: list[tuple[str, str, str]],
):
    async def _fake_get_buffer_items(user_key: str):
        return [{"user_key": user_key, "item_type": "text", "content": "雞腿便當"}]

    async def _fake_get_user(user_key: str):
        return None

    download_calls: list[tuple[str, list[str]]] = []

    async def _fake_download_photos(user_key: str, photo_ids: list[str]) -> list[bytes]:
        download_calls.append((user_key, photo_ids))
        return []

    monkeypatch.setattr(sheets, "get_buffer_items", _fake_get_buffer_items)
    monkeypatch.setattr(sheets, "get_user", _fake_get_user)
    monkeypatch.setattr(downloader, "download_photos", _fake_download_photos)

    reply = await dispatcher.handle_text("line:U1", "ok")

    assert download_calls == []  # 未綁定 Sheet 時不應呼叫下載/辨識
    assert reply is not None and "綁定" in reply


async def test_handle_text_ok_command_clears_buffer_when_recognition_empty(
    monkeypatch: pytest.MonkeyPatch,
    spy_append: list[tuple[str, str, str]],
    fake_user,
):
    clear_calls: list[str] = []

    async def _fake_get_buffer_items(user_key: str):
        return [{"user_key": user_key, "item_type": "text", "content": "不明食物"}]

    async def _fake_clear_buffer(user_key: str) -> None:
        clear_calls.append(user_key)

    async def _fake_download_photos(user_key: str, photo_ids: list[str]) -> list[bytes]:
        return []

    async def _fake_analyze_meal(images: list[bytes], captions: list[str]):
        return []

    monkeypatch.setattr(sheets, "get_buffer_items", _fake_get_buffer_items)
    monkeypatch.setattr(sheets, "clear_buffer", _fake_clear_buffer)
    monkeypatch.setattr(downloader, "download_photos", _fake_download_photos)
    monkeypatch.setattr(vision, "analyze_meal", _fake_analyze_meal)

    reply = await dispatcher.handle_text("line:U1", "ok")

    assert clear_calls == ["line:U1"]  # 辨識不出結果也應清空緩衝區，避免無限重試
    assert reply is not None and "無法辨識" in reply


async def test_handle_text_ok_command_skips_recognition_when_buffer_empty(
    monkeypatch: pytest.MonkeyPatch,
    spy_append: list[tuple[str, str, str]],
):
    download_calls: list[tuple[str, list[str]]] = []
    analyze_calls: list[tuple[list[bytes], list[str]]] = []

    async def _fake_get_buffer_items(user_key: str):
        return []

    async def _fake_download_photos(user_key: str, photo_ids: list[str]) -> list[bytes]:
        download_calls.append((user_key, photo_ids))
        return []

    async def _fake_analyze_meal(images: list[bytes], captions: list[str]):
        analyze_calls.append((images, captions))
        return []

    monkeypatch.setattr(sheets, "get_buffer_items", _fake_get_buffer_items)
    monkeypatch.setattr(downloader, "download_photos", _fake_download_photos)
    monkeypatch.setattr(vision, "analyze_meal", _fake_analyze_meal)

    await dispatcher.handle_text("line:U1", "ok")

    assert spy_append == []
    assert download_calls == []  # 緩衝區為空時不應呼叫下載或辨識
    assert analyze_calls == []


# --- 「今日」查詢指令 ---


async def test_handle_text_today_command_sums_rows_for_current_date(
    monkeypatch: pytest.MonkeyPatch,
    spy_append: list[tuple[str, str, str]],
    fake_user,
):
    read_calls: list[tuple[str, str]] = []

    async def _fake_get_daily_log_rows(google_sheet_id: str, date: str):
        read_calls.append((google_sheet_id, date))
        return [
            {"date": date, "meal": "早餐", "items": "蛋餅", "calories": 400, "carbs_g": 45, "protein_g": 15, "fat_g": 18},
            {"date": date, "meal": "午餐", "items": "雞腿便當", "calories": 650, "carbs_g": 80, "protein_g": 30, "fat_g": 20},
        ]

    monkeypatch.setattr(sheets, "get_daily_log_rows", _fake_get_daily_log_rows)

    reply = await dispatcher.handle_text("line:U1", "今日")

    assert spy_append == []  # 「今日」不應被當成一般文字暫存
    assert len(read_calls) == 1
    assert read_calls[0][0] == "sheet-abc"
    assert reply is not None
    assert "1050" in reply  # 400 + 650
    assert "125" in reply  # 45 + 80
    assert "45" in reply  # 15 + 30
    assert "38" in reply  # 18 + 20


async def test_handle_text_today_command_reports_no_records(
    monkeypatch: pytest.MonkeyPatch,
    fake_user,
):
    async def _fake_get_daily_log_rows(google_sheet_id: str, date: str):
        return []

    monkeypatch.setattr(sheets, "get_daily_log_rows", _fake_get_daily_log_rows)

    reply = await dispatcher.handle_text("line:U1", "今日")

    assert reply is not None and "尚無" in reply


async def test_handle_text_today_command_without_bound_sheet(
    monkeypatch: pytest.MonkeyPatch,
):
    async def _fake_get_user(user_key: str):
        return None

    monkeypatch.setattr(sheets, "get_user", _fake_get_user)

    reply = await dispatcher.handle_text("line:U1", "今日")

    assert reply is not None and "綁定" in reply


# --- 「修正」指令 ---


async def test_handle_text_correct_command_updates_numeric_field(
    monkeypatch: pytest.MonkeyPatch,
    spy_append: list[tuple[str, str, str]],
    fake_user,
):
    calls: list[tuple[str, str, object]] = []

    async def _fake_update(google_sheet_id: str, field: str, value):
        calls.append((google_sheet_id, field, value))
        return True

    monkeypatch.setattr(sheets, "update_latest_daily_log_field", _fake_update)

    reply = await dispatcher.handle_text("line:U1", "修正 熱量 700")

    assert spy_append == []
    assert calls == [("sheet-abc", "calories", 700)]
    assert reply is not None and "700" in reply


async def test_handle_text_correct_command_updates_meal_label(
    monkeypatch: pytest.MonkeyPatch,
    spy_append: list[tuple[str, str, str]],
    fake_user,
):
    calls: list[tuple[str, str, object]] = []

    async def _fake_update(google_sheet_id: str, field: str, value):
        calls.append((google_sheet_id, field, value))
        return True

    monkeypatch.setattr(sheets, "update_latest_daily_log_field", _fake_update)

    reply = await dispatcher.handle_text("line:U1", "修正 餐次 午餐")

    assert calls == [("sheet-abc", "meal", "午餐")]
    assert reply is not None and "午餐" in reply


async def test_handle_text_correct_command_without_bound_sheet(
    monkeypatch: pytest.MonkeyPatch,
):
    async def _fake_get_user(user_key: str):
        return None

    monkeypatch.setattr(sheets, "get_user", _fake_get_user)

    reply = await dispatcher.handle_text("line:U1", "修正 熱量 700")

    assert reply is not None and "綁定" in reply


async def test_handle_text_correct_command_reports_no_records(
    monkeypatch: pytest.MonkeyPatch,
    fake_user,
):
    async def _fake_update(google_sheet_id: str, field: str, value):
        return False

    monkeypatch.setattr(sheets, "update_latest_daily_log_field", _fake_update)

    reply = await dispatcher.handle_text("line:U1", "修正 熱量 700")

    assert reply is not None and "尚無" in reply


@pytest.mark.parametrize(
    "text",
    [
        "修正 熱量",  # 缺少數值
        "修正 卡路里 700",  # 不支援的欄位
        "修正 熱量 abc",  # 數值非數字
        "修正 餐次 深夜食堂",  # 餐次非合法選項
    ],
)
async def test_handle_text_correct_command_rejects_invalid_input(
    monkeypatch: pytest.MonkeyPatch,
    spy_append: list[tuple[str, str, str]],
    text: str,
):
    get_user_calls: list[str] = []
    update_calls: list[tuple] = []

    async def _fake_get_user(user_key: str):
        get_user_calls.append(user_key)
        return {"google_sheet_id": "sheet-abc"}

    async def _fake_update(google_sheet_id: str, field: str, value):
        update_calls.append((google_sheet_id, field, value))
        return True

    monkeypatch.setattr(sheets, "get_user", _fake_get_user)
    monkeypatch.setattr(sheets, "update_latest_daily_log_field", _fake_update)

    reply = await dispatcher.handle_text("line:U1", text)

    assert spy_append == []  # 格式錯誤不應被當成一般文字暫存
    assert get_user_calls == []  # 格式驗證應先於任何 I/O
    assert update_calls == []
    assert reply is not None and reply != ""


async def test_handle_text_returns_fallback_reply_when_unexpected_error_occurs(
    monkeypatch: pytest.MonkeyPatch,
    spy_append: list[tuple[str, str, str]],
    fake_user,
):
    """模擬 Gemini 503 等未預期例外：handle_text 不應向外拋出，而是回傳可回覆使用者的錯誤文字。

    對應真實環境曾發生的情況：vision.analyze_meal() 拋出例外導致 BackgroundTasks
    整個中斷、LINE/Telegram 使用者完全收不到任何回覆訊息。
    """

    async def _fake_get_buffer_items(user_key: str):
        return [{"user_key": user_key, "item_type": "photo", "content": "msg-1"}]

    async def _fake_download_photos(user_key: str, photo_ids: list[str]) -> list[bytes]:
        return [b"fake-image-bytes"]

    async def _raise_server_error(images, captions):
        raise RuntimeError("503 UNAVAILABLE：模擬 Gemini 過載")

    monkeypatch.setattr(sheets, "get_buffer_items", _fake_get_buffer_items)
    monkeypatch.setattr(downloader, "download_photos", _fake_download_photos)
    monkeypatch.setattr(vision, "analyze_meal", _raise_server_error)

    reply = await dispatcher.handle_text("line:U1", "ok")

    assert reply == "處理時發生錯誤，請稍後再試一次"
