"""app.core.line_client 單元測試：Loading Animation / Reply / Push 三支 API 呼叫。

以 monkeypatch 替換底層 _post，不實際呼叫 LINE API。
"""

import pytest

from app.core import line_client


@pytest.fixture()
def spy_post(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    calls: list[tuple[str, dict]] = []

    async def _fake_post(url: str, json_body: dict) -> None:
        calls.append((url, json_body))

    monkeypatch.setattr(line_client, "_post", _fake_post)
    return calls


async def test_start_loading_animation_posts_chat_id_and_seconds(
    spy_post: list[tuple[str, dict]],
):
    await line_client.start_loading_animation("U1", seconds=30)

    assert spy_post == [(line_client._LOADING_URL, {"chatId": "U1", "loadingSeconds": 30})]


async def test_start_loading_animation_swallows_exception(
    monkeypatch: pytest.MonkeyPatch,
):
    async def _fake_post(url: str, json_body: dict) -> None:
        raise RuntimeError("網路錯誤")

    monkeypatch.setattr(line_client, "_post", _fake_post)

    await line_client.start_loading_animation("U1")  # 不應拋出例外


async def test_reply_message_posts_reply_token_and_text(
    spy_post: list[tuple[str, dict]],
):
    await line_client.reply_message("token-1", "已記錄")

    assert spy_post == [
        (
            line_client._REPLY_URL,
            {"replyToken": "token-1", "messages": [{"type": "text", "text": "已記錄"}]},
        )
    ]


async def test_reply_message_propagates_exception(monkeypatch: pytest.MonkeyPatch):
    async def _fake_post(url: str, json_body: dict) -> None:
        raise RuntimeError("reply token 已過期")

    monkeypatch.setattr(line_client, "_post", _fake_post)

    with pytest.raises(RuntimeError):
        await line_client.reply_message("token-1", "已記錄")


async def test_push_message_posts_to_and_text(spy_post: list[tuple[str, dict]]):
    await line_client.push_message("U1", "已記錄")

    assert spy_post == [
        (line_client._PUSH_URL, {"to": "U1", "messages": [{"type": "text", "text": "已記錄"}]})
    ]


async def test_push_message_swallows_exception(monkeypatch: pytest.MonkeyPatch):
    async def _fake_post(url: str, json_body: dict) -> None:
        raise RuntimeError("額度已用盡")

    monkeypatch.setattr(line_client, "_post", _fake_post)

    await line_client.push_message("U1", "已記錄")  # 不應拋出例外
