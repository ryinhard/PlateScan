"""app.core.downloader 單元測試：依平台前綴選擇下載來源、平行下載與個別失敗容錯。

以 monkeypatch 替換底層 _download_line_image / _download_telegram_image，
不實際呼叫 LINE / Telegram API。
"""

import pytest

from app.core import downloader


async def test_download_photos_returns_empty_list_when_no_photo_ids():
    assert await downloader.download_photos("line:U1", []) == []


async def test_download_photos_uses_line_downloader_for_line_user_key(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[str] = []

    async def _fake_line(client, message_id: str) -> bytes:
        calls.append(message_id)
        return f"line-{message_id}".encode()

    async def _fake_telegram(client, file_id: str) -> bytes:
        raise AssertionError("line 使用者不應呼叫 telegram 下載函式")

    monkeypatch.setattr(downloader, "_download_line_image", _fake_line)
    monkeypatch.setattr(downloader, "_download_telegram_image", _fake_telegram)

    images = await downloader.download_photos("line:U1", ["msg-1", "msg-2"])

    assert calls == ["msg-1", "msg-2"]
    assert images == [b"line-msg-1", b"line-msg-2"]


async def test_download_photos_uses_telegram_downloader_for_tg_user_key(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[str] = []

    async def _fake_telegram(client, file_id: str) -> bytes:
        calls.append(file_id)
        return f"tg-{file_id}".encode()

    monkeypatch.setattr(downloader, "_download_telegram_image", _fake_telegram)

    images = await downloader.download_photos("tg:123", ["file-1"])

    assert calls == ["file-1"]
    assert images == [b"tg-file-1"]


async def test_download_photos_skips_failed_downloads_without_raising(
    monkeypatch: pytest.MonkeyPatch,
):
    async def _fake_line(client, message_id: str) -> bytes:
        if message_id == "bad":
            raise RuntimeError("下載失敗")
        return b"ok-image"

    monkeypatch.setattr(downloader, "_download_line_image", _fake_line)

    images = await downloader.download_photos("line:U1", ["good", "bad"])

    assert images == [b"ok-image"]  # 失敗的項目被略過，成功的照片仍保留
