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
    async def _fake_handle_text(user_key: str, text: str):
        return "已記錄"

    send_calls: list[tuple[int, str]] = []

    async def _fake_send(chat_id: int, text: str) -> None:
        send_calls.append((chat_id, text))

    monkeypatch.setattr(dispatcher, "handle_text", _fake_handle_text)
    monkeypatch.setattr(telegram_client, "send_message", _fake_send)

    await tg_adapter._process_text_reply("tg:123", "今日", 123)

    assert send_calls == [(123, "已記錄")]


async def test_process_text_reply_does_nothing_when_no_reply_text(
    monkeypatch: pytest.MonkeyPatch,
):
    async def _fake_handle_text(user_key: str, text: str):
        return None

    send_calls: list[tuple[int, str]] = []

    async def _fake_send(chat_id: int, text: str) -> None:
        send_calls.append((chat_id, text))

    monkeypatch.setattr(dispatcher, "handle_text", _fake_handle_text)
    monkeypatch.setattr(telegram_client, "send_message", _fake_send)

    await tg_adapter._process_text_reply("tg:123", "雞腿便當", 123)

    assert send_calls == []
