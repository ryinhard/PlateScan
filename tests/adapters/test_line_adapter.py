"""app.adapters.line_adapter 單元測試：BackgroundTasks 排程、Loading Animation
觸發時機、以及 Reply/Push 降級 fallback 邏輯。

以 monkeypatch 替換 app.core.dispatcher / app.core.line_client，不觸及真實 API。
"""

import time

import pytest
from fastapi import BackgroundTasks

from app.adapters import line_adapter
from app.core import dispatcher, line_client


def _image_event(user_id: str = "U1", message_id: str = "msg-1") -> dict:
    return {
        "type": "message",
        "replyToken": "token-1",
        "source": {"userId": user_id},
        "message": {"type": "image", "id": message_id},
    }


def _text_event(text: str, user_id: str = "U1", reply_token: str = "token-1") -> dict:
    return {
        "type": "message",
        "replyToken": reply_token,
        "source": {"userId": user_id},
        "message": {"type": "text", "text": text},
    }


def _follow_event(user_id: str = "U1", reply_token: str = "token-1") -> dict:
    return {
        "type": "follow",
        "replyToken": reply_token,
        "source": {"userId": user_id},
    }


async def test_dispatch_event_handles_photo_synchronously_without_scheduling_task(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[tuple[str, str]] = []

    async def _fake_handle_photo(user_key: str, photo_id: str) -> None:
        calls.append((user_key, photo_id))

    monkeypatch.setattr(dispatcher, "handle_photo", _fake_handle_photo)

    background_tasks = BackgroundTasks()
    await line_adapter._dispatch_event(_image_event(), background_tasks)

    assert calls == [("line:U1", "msg-1")]
    assert background_tasks.tasks == []


async def test_dispatch_event_schedules_text_processing_as_background_task(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[tuple] = []

    async def _fake_process(user_key, text, user_id, reply_token, received_at) -> None:
        calls.append((user_key, text, user_id, reply_token))

    monkeypatch.setattr(line_adapter, "_process_text_reply", _fake_process)

    background_tasks = BackgroundTasks()
    await line_adapter._dispatch_event(_text_event("雞腿便當"), background_tasks)

    assert len(background_tasks.tasks) == 1
    await background_tasks.tasks[0]()
    assert calls == [("line:U1", "雞腿便當", "U1", "token-1")]


async def test_dispatch_event_ok_command_triggers_loading_animation(
    monkeypatch: pytest.MonkeyPatch,
):
    loading_calls: list[str] = []

    async def _fake_start_loading(user_id: str, seconds: int = 60) -> None:
        loading_calls.append(user_id)

    monkeypatch.setattr(line_client, "start_loading_animation", _fake_start_loading)

    background_tasks = BackgroundTasks()
    await line_adapter._dispatch_event(_text_event("  OK "), background_tasks)

    assert loading_calls == ["U1"]


async def test_dispatch_event_non_ok_text_does_not_trigger_loading_animation(
    monkeypatch: pytest.MonkeyPatch,
):
    loading_calls: list[str] = []

    async def _fake_start_loading(user_id: str, seconds: int = 60) -> None:
        loading_calls.append(user_id)

    monkeypatch.setattr(line_client, "start_loading_animation", _fake_start_loading)

    background_tasks = BackgroundTasks()
    await line_adapter._dispatch_event(_text_event("今日"), background_tasks)

    assert loading_calls == []


async def test_dispatch_event_follow_replies_with_onboarding_text_and_image(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(dispatcher, "get_onboarding_text", lambda: "歡迎使用 PlateScan！")
    monkeypatch.setattr(
        dispatcher, "get_onboarding_image_url", lambda: "https://example.com/pic.jpg"
    )

    reply_calls: list[tuple] = []

    async def _fake_reply(reply_token: str, text: str, image_url=None) -> None:
        reply_calls.append((reply_token, text, image_url))

    monkeypatch.setattr(line_client, "reply_message", _fake_reply)

    background_tasks = BackgroundTasks()
    await line_adapter._dispatch_event(_follow_event(), background_tasks)

    assert reply_calls == [("token-1", "歡迎使用 PlateScan！", "https://example.com/pic.jpg")]
    assert background_tasks.tasks == []  # follow 事件同步處理，不排背景任務


async def test_dispatch_event_follow_falls_back_to_push_when_reply_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(dispatcher, "get_onboarding_text", lambda: "歡迎使用 PlateScan！")
    monkeypatch.setattr(
        dispatcher, "get_onboarding_image_url", lambda: "https://example.com/pic.jpg"
    )

    async def _fake_reply(reply_token: str, text: str, image_url=None) -> None:
        raise RuntimeError("reply token 已使用過")

    push_calls: list[tuple] = []

    async def _fake_push(user_id: str, text: str, image_url=None) -> None:
        push_calls.append((user_id, text, image_url))

    monkeypatch.setattr(line_client, "reply_message", _fake_reply)
    monkeypatch.setattr(line_client, "push_message", _fake_push)

    background_tasks = BackgroundTasks()
    await line_adapter._dispatch_event(_follow_event(), background_tasks)

    assert push_calls == [("U1", "歡迎使用 PlateScan！", "https://example.com/pic.jpg")]


async def test_process_text_reply_uses_reply_within_time_limit(
    monkeypatch: pytest.MonkeyPatch,
):
    async def _fake_handle_text_with_media(user_key: str, text: str):
        return "已記錄", None

    reply_calls: list[tuple] = []
    push_calls: list[tuple] = []

    async def _fake_reply(reply_token: str, text: str, image_url=None) -> None:
        reply_calls.append((reply_token, text, image_url))

    async def _fake_push(user_id: str, text: str, image_url=None) -> None:
        push_calls.append((user_id, text, image_url))

    monkeypatch.setattr(dispatcher, "handle_text_with_media", _fake_handle_text_with_media)
    monkeypatch.setattr(line_client, "reply_message", _fake_reply)
    monkeypatch.setattr(line_client, "push_message", _fake_push)

    await line_adapter._process_text_reply(
        "line:U1", "今日", "U1", "token-1", time.monotonic()
    )

    assert reply_calls == [("token-1", "已記錄", None)]
    assert push_calls == []


async def test_process_text_reply_passes_through_image_url(
    monkeypatch: pytest.MonkeyPatch,
):
    """新手教學／綁定的空參數或佔位符情境會帶圖，image_url 須原樣轉交給 reply_message。"""

    async def _fake_handle_text_with_media(user_key: str, text: str):
        return "歡迎使用 PlateScan！", "https://example.com/pic.jpg"

    reply_calls: list[tuple] = []

    async def _fake_reply(reply_token: str, text: str, image_url=None) -> None:
        reply_calls.append((reply_token, text, image_url))

    monkeypatch.setattr(dispatcher, "handle_text_with_media", _fake_handle_text_with_media)
    monkeypatch.setattr(line_client, "reply_message", _fake_reply)

    await line_adapter._process_text_reply(
        "line:U1", "新手教學", "U1", "token-1", time.monotonic()
    )

    assert reply_calls == [("token-1", "歡迎使用 PlateScan！", "https://example.com/pic.jpg")]


async def test_process_text_reply_falls_back_to_push_when_elapsed_too_long(
    monkeypatch: pytest.MonkeyPatch,
):
    async def _fake_handle_text_with_media(user_key: str, text: str):
        return "已記錄", None

    reply_calls: list[tuple] = []
    push_calls: list[tuple] = []

    async def _fake_reply(reply_token: str, text: str, image_url=None) -> None:
        reply_calls.append((reply_token, text, image_url))

    async def _fake_push(user_id: str, text: str, image_url=None) -> None:
        push_calls.append((user_id, text, image_url))

    monkeypatch.setattr(dispatcher, "handle_text_with_media", _fake_handle_text_with_media)
    monkeypatch.setattr(line_client, "reply_message", _fake_reply)
    monkeypatch.setattr(line_client, "push_message", _fake_push)

    received_at = time.monotonic() - 20  # 超過 18 秒 reply token 限制
    await line_adapter._process_text_reply(
        "line:U1", "ok", "U1", "token-1", received_at
    )

    assert reply_calls == []
    assert push_calls == [("U1", "已記錄", None)]


async def test_process_text_reply_falls_back_to_push_when_reply_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    async def _fake_handle_text_with_media(user_key: str, text: str):
        return "已記錄", None

    async def _fake_reply(reply_token: str, text: str, image_url=None) -> None:
        raise RuntimeError("reply token 已使用過")

    push_calls: list[tuple] = []

    async def _fake_push(user_id: str, text: str, image_url=None) -> None:
        push_calls.append((user_id, text, image_url))

    monkeypatch.setattr(dispatcher, "handle_text_with_media", _fake_handle_text_with_media)
    monkeypatch.setattr(line_client, "reply_message", _fake_reply)
    monkeypatch.setattr(line_client, "push_message", _fake_push)

    await line_adapter._process_text_reply(
        "line:U1", "ok", "U1", "token-1", time.monotonic()
    )

    assert push_calls == [("U1", "已記錄", None)]


async def test_process_text_reply_does_nothing_when_no_reply_text(
    monkeypatch: pytest.MonkeyPatch,
):
    async def _fake_handle_text_with_media(user_key: str, text: str):
        return None, None

    calls: list[str] = []

    async def _fail_if_called(*args, **kwargs) -> None:
        calls.append("called")

    monkeypatch.setattr(dispatcher, "handle_text_with_media", _fake_handle_text_with_media)
    monkeypatch.setattr(line_client, "reply_message", _fail_if_called)
    monkeypatch.setattr(line_client, "push_message", _fail_if_called)

    await line_adapter._process_text_reply(
        "line:U1", "雞腿便當", "U1", "token-1", time.monotonic()
    )

    assert calls == []
