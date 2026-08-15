"""平行下載照片模組（對應 docs/architecture.md 第 4 節：asyncio.gather 多圖平行下載）。

LINE 以 message_id 透過 Content API 取得圖片二進位內容；
Telegram 以 file_id 先呼叫 getFile 取得 file_path，再組合下載網址取得內容。
下載採 asyncio.gather 平行執行，任一張下載失敗僅記錄警告並略過，不中斷其餘照片。
"""

import asyncio
import logging

import httpx

from app.config import settings

logger = logging.getLogger("app.core.downloader")

_LINE_CONTENT_URL = "https://api-data.line.me/v2/bot/message/{message_id}/content"
_TG_GET_FILE_URL = "https://api.telegram.org/bot{token}/getFile"
_TG_FILE_DOWNLOAD_URL = "https://api.telegram.org/file/bot{token}/{file_path}"


async def _download_line_image(client: httpx.AsyncClient, message_id: str) -> bytes:
    response = await client.get(
        _LINE_CONTENT_URL.format(message_id=message_id),
        headers={"Authorization": f"Bearer {settings.line_channel_access_token}"},
    )
    response.raise_for_status()
    return response.content


async def _download_telegram_image(client: httpx.AsyncClient, file_id: str) -> bytes:
    token = settings.telegram_bot_token
    get_file_response = await client.get(
        _TG_GET_FILE_URL.format(token=token), params={"file_id": file_id}
    )
    get_file_response.raise_for_status()
    file_path = get_file_response.json()["result"]["file_path"]

    content_response = await client.get(
        _TG_FILE_DOWNLOAD_URL.format(token=token, file_path=file_path)
    )
    content_response.raise_for_status()
    return content_response.content


async def download_photos(user_key: str, photo_ids: list[str]) -> list[bytes]:
    """依 user_key 平台前綴（line: / tg:）平行下載所有照片，略過個別下載失敗的項目。"""
    if not photo_ids:
        return []

    is_line = user_key.startswith("line:")
    download_one = _download_line_image if is_line else _download_telegram_image

    async with httpx.AsyncClient(timeout=10.0) as client:
        results = await asyncio.gather(
            *(download_one(client, photo_id) for photo_id in photo_ids),
            return_exceptions=True,
        )

    images: list[bytes] = []
    for photo_id, result in zip(photo_ids, results):
        if isinstance(result, Exception):
            logger.warning(
                "user_key=%s 下載照片失敗 photo_id=%s：%s", user_key, photo_id, result
            )
        else:
            images.append(result)
    return images
