"""app.core.telegram_client 單元測試：sendMessage 呼叫與失敗容錯。

以 monkeypatch 替換底層 _post，不實際呼叫 Telegram API。
"""

import pytest

from app.core import telegram_client


async def test_send_message_posts_chat_id_and_text(monkeypatch: pytest.MonkeyPatch):
    calls: list[tuple[str, dict]] = []

    async def _fake_post(url: str, json_body: dict) -> None:
        calls.append((url, json_body))

    monkeypatch.setattr(telegram_client, "_post", _fake_post)

    await telegram_client.send_message(123, "已記錄")

    assert len(calls) == 1
    url, body = calls[0]
    assert url.endswith("/sendMessage")
    assert body == {"chat_id": 123, "text": "已記錄"}


async def test_send_message_swallows_exception(monkeypatch: pytest.MonkeyPatch):
    async def _fake_post(url: str, json_body: dict) -> None:
        raise RuntimeError("網路錯誤")

    monkeypatch.setattr(telegram_client, "_post", _fake_post)

    await telegram_client.send_message(123, "已記錄")  # 不應拋出例外


async def test_send_photo_posts_chat_id_and_photo_url(monkeypatch: pytest.MonkeyPatch):
    calls: list[tuple[str, dict]] = []

    async def _fake_post(url: str, json_body: dict) -> None:
        calls.append((url, json_body))

    monkeypatch.setattr(telegram_client, "_post", _fake_post)

    await telegram_client.send_photo(123, "https://example.com/pic.jpg")

    assert len(calls) == 1
    url, body = calls[0]
    assert url.endswith("/sendPhoto")
    assert body == {"chat_id": 123, "photo": "https://example.com/pic.jpg"}


async def test_send_photo_includes_caption_when_given(monkeypatch: pytest.MonkeyPatch):
    calls: list[tuple[str, dict]] = []

    async def _fake_post(url: str, json_body: dict) -> None:
        calls.append((url, json_body))

    monkeypatch.setattr(telegram_client, "_post", _fake_post)

    await telegram_client.send_photo(123, "https://example.com/pic.jpg", caption="說明")

    _, body = calls[0]
    assert body == {"chat_id": 123, "photo": "https://example.com/pic.jpg", "caption": "說明"}


async def test_send_photo_swallows_exception(monkeypatch: pytest.MonkeyPatch):
    async def _fake_post(url: str, json_body: dict) -> None:
        raise RuntimeError("網路錯誤")

    monkeypatch.setattr(telegram_client, "_post", _fake_post)

    await telegram_client.send_photo(123, "https://example.com/pic.jpg")  # 不應拋出例外


async def test_set_my_commands_posts_command_list(monkeypatch: pytest.MonkeyPatch):
    calls: list[tuple[str, dict]] = []

    async def _fake_post(url: str, json_body: dict) -> None:
        calls.append((url, json_body))

    monkeypatch.setattr(telegram_client, "_post", _fake_post)

    await telegram_client.set_my_commands()

    assert len(calls) == 1
    url, body = calls[0]
    assert url.endswith("/setMyCommands")
    command_names = [c["command"] for c in body["commands"]]
    assert command_names == [
        "start", "ok", "today", "chart", "link", "fix", "fixhelp", "setdate", "delete",
        "set", "setgoal", "goal", "setmeal", "meal", "cancel", "help",
    ]


async def test_set_my_commands_swallows_exception(monkeypatch: pytest.MonkeyPatch):
    async def _fake_post(url: str, json_body: dict) -> None:
        raise RuntimeError("網路錯誤")

    monkeypatch.setattr(telegram_client, "_post", _fake_post)

    await telegram_client.set_my_commands()  # 不應拋出例外
