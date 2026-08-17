"""app.core.dispatcher 單元測試：照片/文字分發邏輯與 ok 指令觸發機制。

以 monkeypatch 替換 app.core.sheets 的讀寫函式，不觸及真實 Google Sheets，
專注驗證 dispatcher 對 buffer 操作的呼叫是否正確。
"""

import pytest

from app.config import settings
from app.core import dispatcher, downloader, sheets, vision


@pytest.fixture(autouse=True)
def stub_daily_quota(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, int, str]]:
    """預設放行每日用量配額，並攔截該呼叫避免打到真實 Google Sheets。

    設為 autouse 是因為 ok 指令路徑上每個測試都會經過配額檢查，逐一 mock 容易
    漏掉（漏掉時會靜默改打真實 API），需要驗證上限行為的測試自行覆寫此替身。
    """
    calls: list[tuple[str, int, str]] = []

    async def _fake_try_consume(user_key: str, limit: int, today: str) -> tuple[bool, int]:
        calls.append((user_key, limit, today))
        return True, len(calls)

    monkeypatch.setattr(sheets, "try_consume_daily_quota", _fake_try_consume)
    return calls


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


@pytest.fixture()
def fake_service_account_email(monkeypatch: pytest.MonkeyPatch) -> str:
    email = "sa@example.iam.gserviceaccount.com"
    monkeypatch.setattr(sheets, "get_service_account_email", lambda: email)
    return email


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
        return {
            "cot_reasoning": "便當為現成組合餐",
            "confidence_score": 0.85,
            "items": [{"name": "雞腿便當", "calories": 650, "carbs_g": 80, "protein_g": 30, "fat_g": 20}],
        }

    async def _fake_append_daily_log(
        google_sheet_id, date, meal, items, calories, carbs_g, protein_g, fat_g, confidence=0
    ):
        append_log_calls.append(
            (google_sheet_id, date, meal, items, calories, carbs_g, protein_g, fat_g, confidence)
        )

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
    google_sheet_id, date, meal, items, calories, carbs_g, protein_g, fat_g, confidence = append_log_calls[0]
    assert google_sheet_id == "sheet-abc"
    assert items == "雞腿便當"
    assert (calories, carbs_g, protein_g, fat_g) == (650, 80, 30, 20)
    assert confidence == 0.85
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
        return None

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
        return None

    monkeypatch.setattr(sheets, "get_buffer_items", _fake_get_buffer_items)
    monkeypatch.setattr(downloader, "download_photos", _fake_download_photos)
    monkeypatch.setattr(vision, "analyze_meal", _fake_analyze_meal)

    reply = await dispatcher.handle_text("line:U1", "ok")

    assert spy_append == []
    assert download_calls == []  # 緩衝區為空時不應呼叫下載或辨識
    assert analyze_calls == []
    assert reply == "緩衝區是空的，請先傳照片或輸入餐點描述"  # Rich Menu 按鈕點擊時也應收到提示，而非完全靜默


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


# --- 「圖表」/「分析」查詢指令 ---


@pytest.mark.parametrize("command", ["圖表", "分析"])
async def test_handle_text_chart_command_returns_pwa_link_with_sheet_id(
    monkeypatch: pytest.MonkeyPatch,
    spy_append: list[tuple[str, str, str]],
    fake_user,
    command: str,
):
    monkeypatch.setattr(settings, "web_base_url", "https://example.github.io/PlateScan")

    reply = await dispatcher.handle_text("line:U1", command)

    assert spy_append == []  # 「圖表」/「分析」不應被當成一般文字暫存
    assert reply is not None
    assert reply.startswith("https://example.github.io/PlateScan/?sheet_id=sheet-abc\n")
    assert "知道連結的人可檢視" in reply


async def test_handle_text_chart_command_without_bound_sheet(
    monkeypatch: pytest.MonkeyPatch,
):
    async def _fake_get_user(user_key: str):
        return None

    monkeypatch.setattr(sheets, "get_user", _fake_get_user)

    reply = await dispatcher.handle_text("line:U1", "圖表")

    assert reply is not None and "綁定" in reply


async def test_handle_text_chart_command_without_web_base_url_configured(
    monkeypatch: pytest.MonkeyPatch,
    fake_user,
):
    monkeypatch.setattr(settings, "web_base_url", None)

    reply = await dispatcher.handle_text("line:U1", "圖表")

    assert reply is not None and "尚未部署" in reply


# --- 「修正」指令 ---


async def test_handle_text_correct_command_updates_numeric_field(
    monkeypatch: pytest.MonkeyPatch,
    spy_append: list[tuple[str, str, str]],
    fake_user,
):
    calls: list[tuple[str, dict]] = []

    async def _fake_update(google_sheet_id: str, fields: dict):
        calls.append((google_sheet_id, fields))
        return True

    monkeypatch.setattr(sheets, "update_latest_daily_log_fields", _fake_update)

    reply = await dispatcher.handle_text("line:U1", "修正 熱量 700")

    assert spy_append == []
    assert calls == [("sheet-abc", {"calories": 700})]
    assert reply is not None and "700" in reply


async def test_handle_text_correct_command_updates_meal_label(
    monkeypatch: pytest.MonkeyPatch,
    spy_append: list[tuple[str, str, str]],
    fake_user,
):
    calls: list[tuple[str, dict]] = []

    async def _fake_update(google_sheet_id: str, fields: dict):
        calls.append((google_sheet_id, fields))
        return True

    monkeypatch.setattr(sheets, "update_latest_daily_log_fields", _fake_update)

    reply = await dispatcher.handle_text("line:U1", "修正 餐次 午餐")

    assert calls == [("sheet-abc", {"meal": "午餐"})]
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
    async def _fake_update(google_sheet_id: str, fields: dict):
        return False

    monkeypatch.setattr(sheets, "update_latest_daily_log_fields", _fake_update)

    reply = await dispatcher.handle_text("line:U1", "修正 熱量 700")

    assert reply is not None and "尚無" in reply


@pytest.mark.parametrize(
    "text",
    [
        "修正 熱量",  # 缺少數值
        "修正 卡路里 700",  # 不支援的欄位
        "修正 熱量 abc",  # 數值非數字
        "修正 餐次 深夜食堂",  # 餐次非合法選項
        "修正 日期 2026/02/30",  # 不存在的日期
        "修正 日期 明天",  # 不支援的相對日期說法
        "修正 熱量 100 蛋白質",  # 多欄位時參數落單
        "修正 熱量 100 熱量 200",  # 同一欄位重複指定
        "修正 熱量 100 蛋白質 abc",  # 多欄位時後面那欄格式錯誤
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

    async def _fake_update(google_sheet_id: str, fields: dict):
        update_calls.append((google_sheet_id, fields))
        return True

    monkeypatch.setattr(sheets, "get_user", _fake_get_user)
    monkeypatch.setattr(sheets, "update_latest_daily_log_fields", _fake_update)

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


# --- 「說明」指令 ---


@pytest.mark.parametrize("command", ["說明", "指令", "help"])
async def test_handle_text_help_command_returns_static_command_list(
    spy_append: list[tuple[str, str, str]],
    command: str,
):
    reply = await dispatcher.handle_text("line:U1", command)

    assert spy_append == []  # 「說明」不應被當成一般文字暫存
    assert reply is not None and "ok" in reply and "綁定" in reply


# --- 「修正範例」指令 ---


@pytest.mark.parametrize("command", ["修正範例", "fixhelp"])
async def test_handle_text_fixhelp_command_returns_correct_command_examples(
    spy_append: list[tuple[str, str, str]],
    command: str,
):
    reply = await dispatcher.handle_text("line:U1", command)

    assert spy_append == []  # 「修正範例」不應被當成一般文字暫存
    assert reply is not None and "修正 熱量 700" in reply and "修正 日期" in reply


# --- 「新手教學」指令（LINE follow / Telegram /start 皆會觸發） ---


@pytest.mark.parametrize("command", ["新手教學", "教學", "start", "/start"])
async def test_handle_text_onboarding_command_returns_setup_guide(
    spy_append: list[tuple[str, str, str]],
    fake_service_account_email: str,
    command: str,
):
    reply = await dispatcher.handle_text("line:U1", command)

    assert spy_append == []  # 不應被當成一般文字暫存
    assert reply is not None
    assert fake_service_account_email in reply
    assert "綁定 {Sheet ID}" in reply
    assert "知道連結的任何人" in reply


def test_get_onboarding_text_embeds_service_account_email(fake_service_account_email: str):
    assert fake_service_account_email in dispatcher.get_onboarding_text()


# --- 引導圖片（M15：新手教學／綁定的空參數或佔位符會額外附上一張圖片） ---


def test_get_onboarding_image_url_builds_from_web_base_url(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "web_base_url", "https://example.github.io/PlateScan")

    assert (
        dispatcher.get_onboarding_image_url()
        == "https://example.github.io/PlateScan/images/copy-sheet-link.jpg"
    )


def test_get_onboarding_image_url_none_when_web_base_url_unset(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "web_base_url", None)

    assert dispatcher.get_onboarding_image_url() is None


@pytest.mark.parametrize("command", ["新手教學", "教學", "start", "綁定", "設定", "綁定 {Sheet ID}"])
async def test_handle_text_with_media_attaches_image_for_onboarding_and_unbound_set(
    monkeypatch: pytest.MonkeyPatch,
    fake_service_account_email: str,
    command: str,
):
    monkeypatch.setattr(settings, "web_base_url", "https://example.github.io/PlateScan")

    reply, image_url = await dispatcher.handle_text_with_media("line:U1", command)

    assert reply is not None
    assert image_url == "https://example.github.io/PlateScan/images/copy-sheet-link.jpg"


async def test_handle_text_with_media_no_image_for_normal_commands(
    monkeypatch: pytest.MonkeyPatch,
    fake_user,
):
    monkeypatch.setattr(settings, "web_base_url", "https://example.github.io/PlateScan")

    async def _fake_get_daily_log_rows(google_sheet_id: str, date: str):
        return []

    monkeypatch.setattr(sheets, "get_daily_log_rows", _fake_get_daily_log_rows)

    reply, image_url = await dispatcher.handle_text_with_media("line:U1", "今日")

    assert reply is not None
    assert image_url is None


async def test_handle_text_with_media_no_image_when_bound_set_succeeds(
    monkeypatch: pytest.MonkeyPatch,
):
    """已綁定過的「綁定 {合法ID}」（有效參數、非佔位符）走正常綁定流程，不附圖。"""
    monkeypatch.setattr(settings, "web_base_url", "https://example.github.io/PlateScan")

    async def _fake_get_user(user_key: str):
        return None

    async def _fake_upsert_user(*args, **kwargs) -> None:
        return None

    async def _fake_ensure_worksheets(google_sheet_id: str):
        return []

    monkeypatch.setattr(sheets, "get_user", _fake_get_user)
    monkeypatch.setattr(sheets, "upsert_user", _fake_upsert_user)
    monkeypatch.setattr(sheets, "ensure_user_worksheets", _fake_ensure_worksheets)

    reply, image_url = await dispatcher.handle_text_with_media("line:U1", "綁定 sheet-new")

    assert reply is not None and "sheet-new" in reply
    assert image_url is None


async def test_handle_text_with_media_no_image_when_reply_is_none(
    monkeypatch: pytest.MonkeyPatch,
):
    """一般餐點描述文字（handle_text 回傳 None）也不應附圖。"""
    monkeypatch.setattr(settings, "web_base_url", "https://example.github.io/PlateScan")

    async def _fake_append(user_key: str, item_type: str, content: str) -> None:
        return None

    monkeypatch.setattr(sheets, "append_buffer_item", _fake_append)

    reply, image_url = await dispatcher.handle_text_with_media("line:U1", "雞腿便當")

    assert reply is None
    assert image_url is None


# --- 「取消」指令 ---


async def test_handle_text_cancel_command_clears_buffer(
    monkeypatch: pytest.MonkeyPatch,
    spy_append: list[tuple[str, str, str]],
):
    clear_calls: list[str] = []

    async def _fake_clear_buffer(user_key: str) -> None:
        clear_calls.append(user_key)

    monkeypatch.setattr(sheets, "clear_buffer", _fake_clear_buffer)

    reply = await dispatcher.handle_text("line:U1", "取消")

    assert spy_append == []
    assert clear_calls == ["line:U1"]
    assert reply is not None and "清空" in reply


# --- 「綁定」指令（M15：由「設定」更名，「設定」靜默保留為別名） ---


async def test_handle_text_set_command_binds_new_sheet_id(
    monkeypatch: pytest.MonkeyPatch,
    spy_append: list[tuple[str, str, str]],
):
    async def _fake_get_user(user_key: str):
        return None

    upsert_calls: list[tuple[str, str, str]] = []

    async def _fake_upsert_user(user_key: str, google_sheet_id: str, display_name: str = "") -> None:
        upsert_calls.append((user_key, google_sheet_id, display_name))

    async def _fake_ensure_worksheets(google_sheet_id: str):
        return []  # 分頁已存在，本次沒有新建

    monkeypatch.setattr(sheets, "get_user", _fake_get_user)
    monkeypatch.setattr(sheets, "upsert_user", _fake_upsert_user)
    monkeypatch.setattr(sheets, "ensure_user_worksheets", _fake_ensure_worksheets)

    reply = await dispatcher.handle_text("line:U1", "綁定 sheet-new")

    assert spy_append == []
    assert upsert_calls == [("line:U1", "sheet-new", "")]
    assert reply is not None
    assert "sheet-new" in reply
    assert "已自動建立" not in reply  # 分頁已存在時不出現這行
    assert "設定目標" in reply  # 目標設定提醒（指令名稱不變，不受本次更名影響）
    assert "知道連結的人可檢視" in reply  # 圖表權限提醒


async def test_handle_text_set_legacy_alias_still_binds_sheet_id(
    monkeypatch: pytest.MonkeyPatch,
    spy_append: list[tuple[str, str, str]],
):
    """舊指令詞「設定」靜默保留為別名，行為須與「綁定」完全一致。"""

    async def _fake_get_user(user_key: str):
        return None

    upsert_calls: list[tuple[str, str, str]] = []

    async def _fake_upsert_user(user_key: str, google_sheet_id: str, display_name: str = "") -> None:
        upsert_calls.append((user_key, google_sheet_id, display_name))

    async def _fake_ensure_worksheets(google_sheet_id: str):
        return []

    monkeypatch.setattr(sheets, "get_user", _fake_get_user)
    monkeypatch.setattr(sheets, "upsert_user", _fake_upsert_user)
    monkeypatch.setattr(sheets, "ensure_user_worksheets", _fake_ensure_worksheets)

    reply = await dispatcher.handle_text("line:U1", "設定 sheet-new")

    assert spy_append == []
    assert upsert_calls == [("line:U1", "sheet-new", "")]
    assert reply is not None and "sheet-new" in reply


async def test_handle_text_set_command_reports_auto_created_worksheets(
    monkeypatch: pytest.MonkeyPatch,
):
    async def _fake_get_user(user_key: str):
        return None

    async def _fake_upsert_user(user_key: str, google_sheet_id: str, display_name: str = "") -> None:
        return None

    async def _fake_ensure_worksheets(google_sheet_id: str):
        return ["daily_log", "goals"]

    monkeypatch.setattr(sheets, "get_user", _fake_get_user)
    monkeypatch.setattr(sheets, "upsert_user", _fake_upsert_user)
    monkeypatch.setattr(sheets, "ensure_user_worksheets", _fake_ensure_worksheets)

    reply = await dispatcher.handle_text("line:U1", "設定 sheet-new")

    assert reply is not None
    assert "已自動建立缺少的工作表：daily_log、goals" in reply


async def test_handle_text_set_command_extracts_id_from_full_url_and_preserves_display_name(
    monkeypatch: pytest.MonkeyPatch,
    fake_user,
):
    upsert_calls: list[tuple[str, str, str]] = []

    async def _fake_upsert_user(user_key: str, google_sheet_id: str, display_name: str = "") -> None:
        upsert_calls.append((user_key, google_sheet_id, display_name))

    async def _fake_ensure_worksheets(google_sheet_id: str):
        return []

    monkeypatch.setattr(sheets, "upsert_user", _fake_upsert_user)
    monkeypatch.setattr(sheets, "ensure_user_worksheets", _fake_ensure_worksheets)

    reply = await dispatcher.handle_text(
        "line:U1", "設定 https://docs.google.com/spreadsheets/d/sheet-xyz/edit#gid=0"
    )

    assert upsert_calls == [("line:U1", "sheet-xyz", "小明")]  # fake_user 的既有 display_name 不被清空
    assert reply is not None and "sheet-xyz" in reply


async def test_handle_text_set_command_rejects_too_many_arguments(
    monkeypatch: pytest.MonkeyPatch,
    spy_append: list[tuple[str, str, str]],
):
    """兩個以上參數（非空、非佔位符）仍視為格式錯誤，維持原本行為。"""
    upsert_calls: list[tuple] = []
    ensure_calls: list[str] = []

    async def _fake_upsert_user(*args, **kwargs) -> None:
        upsert_calls.append((args, kwargs))

    async def _fake_ensure_worksheets(google_sheet_id: str):
        ensure_calls.append(google_sheet_id)
        return []

    monkeypatch.setattr(sheets, "upsert_user", _fake_upsert_user)
    monkeypatch.setattr(sheets, "ensure_user_worksheets", _fake_ensure_worksheets)

    reply = await dispatcher.handle_text("line:U1", "綁定 sheet-new 多餘參數")

    assert spy_append == []
    assert upsert_calls == []
    assert ensure_calls == []  # 格式驗證應先於任何 I/O
    assert reply is not None and "格式錯誤" in reply


@pytest.mark.parametrize("command", ["綁定", "設定", " 綁定 "])
async def test_handle_text_set_command_without_args_returns_onboarding_guide(
    monkeypatch: pytest.MonkeyPatch,
    spy_append: list[tuple[str, str, str]],
    fake_service_account_email: str,
    command: str,
):
    """無參數時不再回格式錯誤，改回完整的綁定步驟引導（含真實 Service Account Email）。"""
    upsert_calls: list[tuple] = []
    ensure_calls: list[str] = []

    async def _fake_upsert_user(*args, **kwargs) -> None:
        upsert_calls.append((args, kwargs))

    async def _fake_ensure_worksheets(google_sheet_id: str):
        ensure_calls.append(google_sheet_id)
        return []

    monkeypatch.setattr(sheets, "upsert_user", _fake_upsert_user)
    monkeypatch.setattr(sheets, "ensure_user_worksheets", _fake_ensure_worksheets)

    reply = await dispatcher.handle_text("line:U1", command)

    assert spy_append == []
    assert upsert_calls == []
    assert ensure_calls == []  # 引導訊息不應觸發任何 Sheet I/O
    assert reply is not None
    assert reply == dispatcher.get_onboarding_text()
    assert fake_service_account_email in reply
    assert "格式錯誤" not in reply


@pytest.mark.parametrize("command", ["綁定 {Sheet ID}", "綁定  {Sheet  ID}  "])
async def test_handle_text_set_command_with_placeholder_arg_returns_onboarding_guide(
    monkeypatch: pytest.MonkeyPatch,
    fake_service_account_email: str,
    command: str,
):
    """Rich Menu 按鈕送出的樣板字面「綁定 {Sheet ID}」（大括號含空白會被切成兩個 token）
    須被視同無參數，導向引導訊息，而非拿去當 Sheet ID 嘗試存取。
    """
    ensure_calls: list[str] = []

    async def _fake_ensure_worksheets(google_sheet_id: str):
        ensure_calls.append(google_sheet_id)
        return []

    monkeypatch.setattr(sheets, "ensure_user_worksheets", _fake_ensure_worksheets)

    reply = await dispatcher.handle_text("line:U1", command)

    assert ensure_calls == []  # 不應把佔位符文字拿去嘗試驗證存取權
    assert reply == dispatcher.get_onboarding_text()


async def test_handle_text_set_command_reports_access_failure_before_binding(
    monkeypatch: pytest.MonkeyPatch,
    fake_service_account_email: str,
):
    async def _fake_ensure_worksheets(google_sheet_id: str):
        raise RuntimeError("PERMISSION_DENIED")

    get_user_calls: list[str] = []

    async def _fake_get_user(user_key: str):
        get_user_calls.append(user_key)
        return None

    upsert_calls: list[tuple] = []

    async def _fake_upsert_user(*args, **kwargs) -> None:
        upsert_calls.append((args, kwargs))

    monkeypatch.setattr(sheets, "ensure_user_worksheets", _fake_ensure_worksheets)
    monkeypatch.setattr(sheets, "get_user", _fake_get_user)
    monkeypatch.setattr(sheets, "upsert_user", _fake_upsert_user)

    reply = await dispatcher.handle_text("line:U1", "設定 sheet-bad")

    assert get_user_calls == []  # 存取驗證失敗時不應寫入 users 工作表
    assert upsert_calls == []
    assert reply is not None
    assert "無法存取" in reply
    assert fake_service_account_email in reply


# --- 「目標」指令 ---


async def test_handle_text_goal_command_returns_formatted_goal_summary(
    monkeypatch: pytest.MonkeyPatch,
    fake_user,
):
    async def _fake_get_goals(google_sheet_id: str):
        assert google_sheet_id == "sheet-abc"
        return [
            {"nutrient": "calories", "target": "2000", "unit": "kcal"},
            {"nutrient": "protein", "target": "120", "unit": "g"},
        ]

    monkeypatch.setattr(sheets, "get_goals", _fake_get_goals)

    reply = await dispatcher.handle_text("line:U1", "目標")

    assert reply == "每日營養目標：\ncalories 2000kcal\nprotein 120g"


async def test_handle_text_goal_command_without_bound_sheet(
    monkeypatch: pytest.MonkeyPatch,
):
    async def _fake_get_user(user_key: str):
        return None

    monkeypatch.setattr(sheets, "get_user", _fake_get_user)

    reply = await dispatcher.handle_text("line:U1", "目標")

    assert reply is not None and "綁定" in reply


async def test_handle_text_goal_command_when_no_goals_set(
    monkeypatch: pytest.MonkeyPatch,
    fake_user,
):
    async def _fake_get_goals(google_sheet_id: str):
        return []

    monkeypatch.setattr(sheets, "get_goals", _fake_get_goals)

    reply = await dispatcher.handle_text("line:U1", "目標")

    assert reply == "尚未設定每日營養目標"


# --- 「設定目標」指令 ---


async def test_handle_text_goal_set_command_upserts_goal(
    monkeypatch: pytest.MonkeyPatch,
    spy_append: list[tuple[str, str, str]],
    fake_user,
):
    calls: list[tuple[str, str, object, str]] = []

    async def _fake_upsert_goal(google_sheet_id: str, nutrient: str, target, unit: str) -> None:
        calls.append((google_sheet_id, nutrient, target, unit))

    monkeypatch.setattr(sheets, "upsert_goal", _fake_upsert_goal)

    reply = await dispatcher.handle_text("line:U1", "設定目標 熱量 2000")

    assert spy_append == []
    assert calls == [("sheet-abc", "calories", 2000, "kcal")]
    assert reply is not None and "2000" in reply


async def test_handle_text_goal_set_command_without_bound_sheet(
    monkeypatch: pytest.MonkeyPatch,
):
    async def _fake_get_user(user_key: str):
        return None

    monkeypatch.setattr(sheets, "get_user", _fake_get_user)

    reply = await dispatcher.handle_text("line:U1", "設定目標 熱量 2000")

    assert reply is not None and "綁定" in reply


@pytest.mark.parametrize(
    "text",
    [
        "設定目標 熱量",  # 缺少數值
        "設定目標 卡路里 2000",  # 不支援的欄位
        "設定目標 熱量 abc",  # 數值非數字
        "設定目標 熱量 2000 蛋白質",  # 多項時參數數量為奇數
        "設定目標 熱量 2000 熱量 1800",  # 同一欄位重複指定
    ],
)
async def test_handle_text_goal_set_command_rejects_invalid_input(
    monkeypatch: pytest.MonkeyPatch,
    spy_append: list[tuple[str, str, str]],
    text: str,
):
    get_user_calls: list[str] = []

    async def _fake_get_user(user_key: str):
        get_user_calls.append(user_key)
        return {"google_sheet_id": "sheet-abc"}

    monkeypatch.setattr(sheets, "get_user", _fake_get_user)

    reply = await dispatcher.handle_text("line:U1", text)

    assert spy_append == []
    assert get_user_calls == []  # 格式驗證應先於任何 I/O
    assert reply is not None and reply != ""


async def test_handle_text_goal_set_command_accepts_multiple_fields(
    monkeypatch: pytest.MonkeyPatch,
    spy_append: list[tuple[str, str, str]],
    fake_user,
):
    """PWA「📋 複製指令」按鈕複製出的單行多項格式須能一次寫入全部目標。"""
    calls: list[tuple[str, str, object, str]] = []

    async def _fake_upsert_goal(google_sheet_id: str, nutrient: str, target, unit: str) -> None:
        calls.append((google_sheet_id, nutrient, target, unit))

    monkeypatch.setattr(sheets, "upsert_goal", _fake_upsert_goal)

    reply = await dispatcher.handle_text(
        "line:U1", "設定目標 熱量 2000 蛋白質 120 碳水 220 脂肪 100"
    )

    assert spy_append == []
    assert calls == [
        ("sheet-abc", "calories", 2000, "kcal"),
        ("sheet-abc", "protein", 120, "g"),
        ("sheet-abc", "carbs", 220, "g"),
        ("sheet-abc", "fat", 100, "g"),
    ]
    assert reply is not None
    assert reply.startswith("已設定每日目標：")
    for expected in ("熱量 2000 kcal", "蛋白質 120 g", "碳水 220 g", "脂肪 100 g"):
        assert expected in reply


async def test_handle_text_goal_set_command_writes_nothing_when_any_field_invalid(
    monkeypatch: pytest.MonkeyPatch,
    fake_user,
):
    """多項中只要有一項驗證失敗，前面已驗證通過的項目也不可先寫入（不留半套狀態）。"""
    calls: list[tuple[str, str, object, str]] = []

    async def _fake_upsert_goal(google_sheet_id: str, nutrient: str, target, unit: str) -> None:
        calls.append((google_sheet_id, nutrient, target, unit))

    monkeypatch.setattr(sheets, "upsert_goal", _fake_upsert_goal)

    reply = await dispatcher.handle_text("line:U1", "設定目標 熱量 2000 蛋白質 abc")

    assert calls == []
    assert reply is not None and "蛋白質" in reply


# --- 「連結」指令 ---


@pytest.mark.parametrize("command", ["連結", "原始表單", "記錄表單", "/link"])
async def test_handle_text_link_command_returns_sheet_edit_url(
    spy_append: list[tuple[str, str, str]],
    fake_user,
    command: str,
):
    reply = await dispatcher.handle_text("line:U1", command)

    assert spy_append == []
    assert reply == "https://docs.google.com/spreadsheets/d/sheet-abc/edit"


async def test_handle_text_link_command_without_bound_sheet(
    monkeypatch: pytest.MonkeyPatch,
):
    async def _fake_get_user(user_key: str):
        return None

    monkeypatch.setattr(sheets, "get_user", _fake_get_user)

    reply = await dispatcher.handle_text("line:U1", "連結")

    assert reply is not None and "綁定" in reply


# --- 指令別名與容錯 ---


@pytest.mark.parametrize(
    "text",
    ["today", "TODAY", " today ", "/today", "/Today@PlateScanBot"],
)
async def test_handle_text_today_command_accepts_english_and_slash_aliases(
    monkeypatch: pytest.MonkeyPatch,
    fake_user,
    text: str,
):
    async def _fake_get_daily_log_rows(google_sheet_id: str, date: str):
        return []

    monkeypatch.setattr(sheets, "get_daily_log_rows", _fake_get_daily_log_rows)

    reply = await dispatcher.handle_text("line:U1", text)

    assert reply is not None and "尚無" in reply


@pytest.mark.parametrize("text", ["/ok", "/OK@PlateScanBot"])
async def test_is_ok_command_accepts_slash_aliases(text: str):
    assert dispatcher.is_ok_command(text) is True


@pytest.mark.parametrize("text", ["今日 我吃了雞腿便當", "okay", "/todayish"])
async def test_handle_text_does_not_misfire_on_lookalike_text(
    monkeypatch: pytest.MonkeyPatch,
    spy_append: list[tuple[str, str, str]],
    text: str,
):
    await dispatcher.handle_text("line:U1", text)

    assert spy_append == [("line:U1", "text", text.strip())]


async def test_handle_text_fix_alias_updates_numeric_field(
    monkeypatch: pytest.MonkeyPatch,
    fake_user,
):
    calls: list[tuple[str, str, object]] = []

    async def _fake_update(google_sheet_id: str, fields: dict):
        calls.append((google_sheet_id, fields))
        return True

    monkeypatch.setattr(sheets, "update_latest_daily_log_fields", _fake_update)

    reply = await dispatcher.handle_text("line:U1", "/fix 熱量 700")

    assert calls == [("sheet-abc", {"calories": 700})]
    assert reply is not None and "700" in reply


async def test_handle_text_set_alias_binds_sheet_id(
    monkeypatch: pytest.MonkeyPatch,
):
    async def _fake_get_user(user_key: str):
        return None

    upsert_calls: list[tuple[str, str, str]] = []

    async def _fake_upsert_user(user_key: str, google_sheet_id: str, display_name: str = "") -> None:
        upsert_calls.append((user_key, google_sheet_id, display_name))

    async def _fake_ensure_worksheets(google_sheet_id: str):
        return []

    monkeypatch.setattr(sheets, "get_user", _fake_get_user)
    monkeypatch.setattr(sheets, "upsert_user", _fake_upsert_user)
    monkeypatch.setattr(sheets, "ensure_user_worksheets", _fake_ensure_worksheets)

    reply = await dispatcher.handle_text("line:U1", "/set sheet-new")

    assert upsert_calls == [("line:U1", "sheet-new", "")]
    assert reply is not None and "sheet-new" in reply


# --- M13：日期修正、多欄位修正、刪除、每日用量上限 ---


@pytest.fixture()
def spy_update_fields(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    calls: list[tuple[str, dict]] = []

    async def _fake_update(google_sheet_id: str, fields: dict):
        calls.append((google_sheet_id, fields))
        return True

    monkeypatch.setattr(sheets, "update_latest_daily_log_fields", _fake_update)
    return calls


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("修正 日期 2026/08/17", "2026/08/17"),
        ("修正 日期 2026-08-17", "2026/08/17"),
        ("修正 日期 2026.08.17", "2026/08/17"),
        ("修正 日期 20260817", "2026/08/17"),
        ("修正 日期 2026/8/7", "2026/08/07"),
        ("修改日期 2026/08/17", "2026/08/17"),
        ("修改日期 2026-08-17", "2026/08/17"),
        ("/setdate 20260817", "2026/08/17"),
    ],
)
async def test_date_correction_normalizes_accepted_formats(
    spy_update_fields: list[tuple[str, dict]],
    fake_user,
    text: str,
    expected: str,
):
    """各種日期寫法都應正規化為 YYYY/MM/DD 再寫入（前端 parseDate() 依此解析）。"""
    reply = await dispatcher.handle_text("line:U1", text)

    assert spy_update_fields == [("sheet-abc", {"date": expected})]
    assert reply is not None and expected in reply


async def test_date_correction_supports_relative_days(
    spy_update_fields: list[tuple[str, dict]],
    fake_user,
):
    from datetime import datetime, timedelta

    yesterday = (datetime.now(dispatcher._TAIPEI_TZ) - timedelta(days=1)).strftime("%Y/%m/%d")

    await dispatcher.handle_text("line:U1", "修改日期 昨天")

    assert spy_update_fields == [("sheet-abc", {"date": yesterday})]


async def test_date_correction_without_year_fills_current_year(
    spy_update_fields: list[tuple[str, dict]],
    fake_user,
):
    from datetime import datetime

    current_year = datetime.now(dispatcher._TAIPEI_TZ).year

    await dispatcher.handle_text("line:U1", "修改日期 8/17")

    assert spy_update_fields == [("sheet-abc", {"date": f"{current_year}/08/17"})]


async def test_correct_command_updates_multiple_fields_in_one_batch(
    spy_update_fields: list[tuple[str, dict]],
    fake_user,
):
    reply = await dispatcher.handle_text("line:U1", "修正 熱量 2000 蛋白質 120")

    assert spy_update_fields == [("sheet-abc", {"calories": 2000, "protein_g": 120})]
    assert reply is not None
    assert "熱量 → 2000" in reply and "蛋白質 → 120" in reply


async def test_correct_command_mixes_meal_and_date_fields(
    spy_update_fields: list[tuple[str, dict]],
    fake_user,
):
    await dispatcher.handle_text("line:U1", "修正 餐次 早餐 日期 2026/08/17")

    assert spy_update_fields == [("sheet-abc", {"meal": "早餐", "date": "2026/08/17"})]


async def test_correct_command_validates_all_fields_before_writing(
    monkeypatch: pytest.MonkeyPatch,
    fake_user,
):
    """後面欄位格式錯誤時，前面已驗證的欄位也不得寫入（避免半套狀態）。"""
    update_calls: list[tuple] = []

    async def _fake_update(google_sheet_id: str, fields: dict):
        update_calls.append((google_sheet_id, fields))
        return True

    monkeypatch.setattr(sheets, "update_latest_daily_log_fields", _fake_update)

    reply = await dispatcher.handle_text("line:U1", "修正 熱量 2000 日期 2026/02/30")

    assert update_calls == []
    assert reply is not None and "日期" in reply


@pytest.mark.parametrize("command", ["刪除", "delete", "/delete", " 刪除 "])
async def test_delete_command_removes_latest_row_and_reports_content(
    monkeypatch: pytest.MonkeyPatch,
    spy_append: list[tuple[str, str, str]],
    fake_user,
    command: str,
):
    delete_calls: list[str] = []

    async def _fake_delete(google_sheet_id: str):
        delete_calls.append(google_sheet_id)
        return {
            "date": "2026/08/17",
            "meal": "午餐",
            "items": "雞腿便當、燙青菜",
            "calories": 850.0,
            "carbs_g": 95.0,
            "protein_g": 38.0,
            "fat_g": 32.0,
            "confidence": 0.9,
        }

    monkeypatch.setattr(sheets, "delete_latest_daily_log_row", _fake_delete)

    reply = await dispatcher.handle_text("line:U1", command)

    assert spy_append == []  # 指令不應被當成一般文字暫存
    assert delete_calls == ["sheet-abc"]
    assert reply is not None
    assert "已刪除最近一筆紀錄" in reply
    assert "2026/08/17 午餐 雞腿便當、燙青菜" in reply
    assert "熱量 850" in reply  # float 應去掉多餘的 .0
    assert "850.0" not in reply


async def test_delete_command_reports_when_no_records(
    monkeypatch: pytest.MonkeyPatch,
    fake_user,
):
    async def _fake_delete(google_sheet_id: str):
        return None

    monkeypatch.setattr(sheets, "delete_latest_daily_log_row", _fake_delete)

    reply = await dispatcher.handle_text("line:U1", "刪除")

    assert reply == "尚無可刪除的紀錄"


async def test_delete_command_without_bound_sheet(monkeypatch: pytest.MonkeyPatch):
    async def _fake_get_user(user_key: str):
        return None

    monkeypatch.setattr(sheets, "get_user", _fake_get_user)

    reply = await dispatcher.handle_text("line:U1", "刪除")

    assert reply is not None and "綁定" in reply


async def test_ok_command_blocked_when_daily_quota_exhausted(
    monkeypatch: pytest.MonkeyPatch,
    fake_user,
):
    """超過每日上限時：不得下載照片、不得呼叫 Gemini，且緩衝區必須保留。"""
    download_calls: list[tuple] = []
    analyze_calls: list[tuple] = []
    clear_calls: list[str] = []

    async def _fake_get_buffer_items(user_key: str):
        return [{"user_key": user_key, "item_type": "photo", "content": "msg-1"}]

    async def _fake_try_consume(user_key: str, limit: int, today: str):
        return False, limit

    async def _fake_download_photos(user_key: str, photo_ids: list[str]):
        download_calls.append((user_key, photo_ids))
        return []

    async def _fake_analyze_meal(images, captions):
        analyze_calls.append((images, captions))
        return None

    async def _fake_clear_buffer(user_key: str) -> None:
        clear_calls.append(user_key)

    monkeypatch.setattr(sheets, "get_buffer_items", _fake_get_buffer_items)
    monkeypatch.setattr(sheets, "try_consume_daily_quota", _fake_try_consume)
    monkeypatch.setattr(sheets, "clear_buffer", _fake_clear_buffer)
    monkeypatch.setattr(downloader, "download_photos", _fake_download_photos)
    monkeypatch.setattr(vision, "analyze_meal", _fake_analyze_meal)

    reply = await dispatcher.handle_text("line:U1", "ok")

    assert download_calls == []  # 必須擋在下載與 Gemini 呼叫之前
    assert analyze_calls == []
    assert clear_calls == []  # 緩衝區保留，隔天可接續辨識
    assert reply is not None
    assert "上限" in reply and "還留著" in reply


async def test_ok_command_truncates_photos_over_per_call_limit(
    monkeypatch: pytest.MonkeyPatch,
    fake_user,
):
    """單次 ok 照片數超過上限時只取前 N 張，並在回覆中告知已略過幾張。"""
    monkeypatch.setattr(settings, "max_photos_per_ok", 3)

    download_calls: list[tuple[str, list[str]]] = []

    async def _fake_get_buffer_items(user_key: str):
        return [
            {"user_key": user_key, "item_type": "photo", "content": f"msg-{i}"} for i in range(5)
        ]

    async def _fake_download_photos(user_key: str, photo_ids: list[str]):
        download_calls.append((user_key, photo_ids))
        return [b"img"] * len(photo_ids)

    async def _fake_analyze_meal(images, captions):
        return {
            "cot_reasoning": "測試",
            "confidence_score": 0.8,
            "items": [{"name": "便當", "calories": 650, "carbs_g": 80, "protein_g": 30, "fat_g": 20}],
        }

    async def _fake_append_daily_log(*args, **kwargs) -> None:
        return None

    async def _fake_clear_buffer(user_key: str) -> None:
        return None

    monkeypatch.setattr(sheets, "get_buffer_items", _fake_get_buffer_items)
    monkeypatch.setattr(sheets, "clear_buffer", _fake_clear_buffer)
    monkeypatch.setattr(sheets, "append_daily_log", _fake_append_daily_log)
    monkeypatch.setattr(downloader, "download_photos", _fake_download_photos)
    monkeypatch.setattr(vision, "analyze_meal", _fake_analyze_meal)

    reply = await dispatcher.handle_text("line:U1", "ok")

    assert download_calls == [("line:U1", ["msg-0", "msg-1", "msg-2"])]
    assert reply is not None and "略過後面 2 張" in reply


async def test_ok_command_passes_configured_limit_and_taipei_date_to_quota(
    monkeypatch: pytest.MonkeyPatch,
    stub_daily_quota: list[tuple[str, int, str]],
    fake_user,
):
    from datetime import datetime

    monkeypatch.setattr(settings, "daily_ok_limit_per_user", 30)

    async def _fake_get_buffer_items(user_key: str):
        return [{"user_key": user_key, "item_type": "text", "content": "雞腿便當"}]

    async def _fake_analyze_meal(images, captions):
        return None

    async def _fake_clear_buffer(user_key: str) -> None:
        return None

    async def _fake_download_photos(user_key: str, photo_ids: list[str]):
        return []

    monkeypatch.setattr(sheets, "get_buffer_items", _fake_get_buffer_items)
    monkeypatch.setattr(sheets, "clear_buffer", _fake_clear_buffer)
    monkeypatch.setattr(downloader, "download_photos", _fake_download_photos)
    monkeypatch.setattr(vision, "analyze_meal", _fake_analyze_meal)

    await dispatcher.handle_text("line:U1", "ok")

    today = datetime.now(dispatcher._TAIPEI_TZ).strftime("%Y/%m/%d")
    assert stub_daily_quota == [("line:U1", 30, today)]
