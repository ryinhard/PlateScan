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


@pytest.mark.parametrize("command", ["ok", "OK", " ok ", "Ok\n"])
async def test_handle_text_ok_command_downloads_photos_and_calls_gemini(
    monkeypatch: pytest.MonkeyPatch,
    spy_append: list[tuple[str, str, str]],
    command: str,
):
    read_calls: list[str] = []
    clear_calls: list[str] = []
    download_calls: list[tuple[str, list[str]]] = []
    analyze_calls: list[tuple[list[bytes], list[str]]] = []

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

    monkeypatch.setattr(sheets, "get_buffer_items", _fake_get_buffer_items)
    monkeypatch.setattr(sheets, "clear_buffer", _fake_clear_buffer)
    monkeypatch.setattr(downloader, "download_photos", _fake_download_photos)
    monkeypatch.setattr(vision, "analyze_meal", _fake_analyze_meal)

    await dispatcher.handle_text("line:U1", command)

    assert read_calls == ["line:U1"]
    assert spy_append == []  # ok 指令不應被當成一般文字暫存
    assert download_calls == [("line:U1", ["msg-1"])]
    assert analyze_calls == [([b"fake-image-bytes"], ["雞腿便當"])]
    assert clear_calls == []  # M4 階段尚未清空 buffer，寫入 daily_log 與清空留待 M5


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
