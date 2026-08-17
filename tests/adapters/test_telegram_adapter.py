"""app.adapters.tg_adapter 單元測試：BackgroundTasks 排程與 sendMessage 回覆。

以 monkeypatch 替換 app.core.dispatcher / app.core.telegram_client，不觸及真實 API。
"""

import pytest
from fastapi import BackgroundTasks

from app.adapters import tg_adapter
from app.core import dispatcher, telegram_client


def _photo_update(chat_id: int = 123, file_id: str = "file-1") -> dict:
    return {
        "update_id": 1,
        "message": {
            "chat": {"id": chat_id},
            "photo": [{"file_id": "small"}, {"file_id": file_id}],
        },
    }


def _text_update(text: str, chat_id: int = 123) -> dict:
    return {"update_id": 1, "message": {"chat": {"id": chat_id}, "text": text}}


async def test_dispatch_update_handles_photo_synchronously_without_scheduling_task(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[tuple[str, str]] = []

    async def _fake_handle_photo(user_key: str, photo_id: str) -> None:
        calls.append((user_key, photo_id))

    monkeypatch.setattr(dispatcher, "handle_photo", _fake_handle_photo)

    background_tasks = BackgroundTasks()
    await tg_adapter._dispatch_update(_photo_update(), background_tasks)

    assert calls == [("tg:123", "file-1")]
    assert background_tasks.tasks == []


async def test_dispatch_update_schedules_text_processing_as_background_task(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[tuple] = []

    async def _fake_process(user_key, text, chat_id) -> None:
        calls.append((user_key, text, chat_id))

    monkeypatch.setattr(tg_adapter, "_process_text_reply", _fake_process)

    background_tasks = BackgroundTasks()
    await tg_adapter._dispatch_update(_text_update("雞腿便當"), background_tasks)

    assert len(background_tasks.tasks) == 1
    await background_tasks.tasks[0]()
    assert calls == [("tg:123", "雞腿便當", 123)]


async def test_process_text_reply_sends_message_when_reply_text_present(
    monkeypatch: pytest.MonkeyPatch,
):
    async def _fake_handle_text_with_media(user_key: str, text: str):
        return "已記錄", None

    send_calls: list[tuple[int, str]] = []
    photo_calls: list[tuple] = []

    async def _fake_send(chat_id: int, text: str) -> None:
        send_calls.append((chat_id, text))

    async def _fake_send_photo(chat_id: int, photo_url: str) -> None:
        photo_calls.append((chat_id, photo_url))

    monkeypatch.setattr(dispatcher, "handle_text_with_media", _fake_handle_text_with_media)
    monkeypatch.setattr(telegram_client, "send_message", _fake_send)
    monkeypatch.setattr(telegram_client, "send_photo", _fake_send_photo)

    await tg_adapter._process_text_reply("tg:123", "今日", 123)

    assert send_calls == [(123, "已記錄")]
    assert photo_calls == []  # 無圖片 URL 時不應額外呼叫 sendPhoto


async def test_process_text_reply_sends_photo_when_image_url_present(
    monkeypatch: pytest.MonkeyPatch,
):
    """新手教學／綁定的空參數或佔位符情境會帶圖，須在 sendMessage 之後額外呼叫 sendPhoto。"""

    async def _fake_handle_text_with_media(user_key: str, text: str):
        return "歡迎使用 PlateScan！", "https://example.com/pic.jpg"

    send_calls: list[tuple[int, str]] = []
    photo_calls: list[tuple] = []

    async def _fake_send(chat_id: int, text: str) -> None:
        send_calls.append((chat_id, text))

    async def _fake_send_photo(chat_id: int, photo_url: str) -> None:
        photo_calls.append((chat_id, photo_url))

    monkeypatch.setattr(dispatcher, "handle_text_with_media", _fake_handle_text_with_media)
    monkeypatch.setattr(telegram_client, "send_message", _fake_send)
    monkeypatch.setattr(telegram_client, "send_photo", _fake_send_photo)

    await tg_adapter._process_text_reply("tg:123", "/start", 123)

    assert send_calls == [(123, "歡迎使用 PlateScan！")]
    assert photo_calls == [(123, "https://example.com/pic.jpg")]


async def test_process_text_reply_does_nothing_when_no_reply_text(
    monkeypatch: pytest.MonkeyPatch,
):
    async def _fake_handle_text_with_media(user_key: str, text: str):
        return None, None

    send_calls: list[tuple[int, str]] = []

    async def _fake_send(chat_id: int, text: str) -> None:
        send_calls.append((chat_id, text))

    monkeypatch.setattr(dispatcher, "handle_text_with_media", _fake_handle_text_with_media)
    monkeypatch.setattr(telegram_client, "send_message", _fake_send)

    await tg_adapter._process_text_reply("tg:123", "雞腿便當", 123)

    assert send_calls == []
