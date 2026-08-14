"""Core Handler：訊息與指令分發（對應 DESIGN-v6.md 系統架構圖的 Core Handler 層）。

LINE / Telegram adapter 皆呼叫本模組的 handle_photo() / handle_text()，
統一轉換為 app.core.sheets 的 buffer 讀寫操作，避免兩個 adapter 各自重複實作。
"""

import logging

from app.core import downloader, sheets, vision

logger = logging.getLogger("app.core.dispatcher")

OK_COMMAND = "ok"


async def handle_photo(user_key: str, photo_id: str) -> None:
    """將照片代碼（LINE message_id 或 Telegram file_id）追加至當前餐次緩衝區。"""
    await sheets.append_buffer_item(user_key, "photo", photo_id)


async def handle_text(user_key: str, text: str) -> None:
    """處理文字訊息：`ok` 觸發辨識，其餘文字視為餐點描述追加至緩衝區。"""
    stripped = text.strip()

    if stripped.lower() == OK_COMMAND:
        items = await sheets.get_buffer_items(user_key)
        photo_ids = [item["content"] for item in items if item["item_type"] == "photo"]
        captions = [item["content"] for item in items if item["item_type"] == "text"]

        if not photo_ids and not captions:
            logger.info("user_key=%s 觸發 ok 指令，但緩衝區為空，略過辨識", user_key)
            return

        images = await downloader.download_photos(user_key, photo_ids)
        result = await vision.analyze_meal(images, captions)
        logger.info(
            "user_key=%s 觸發 ok 指令，緩衝區 %d 張照片（成功下載 %d 張）+ %d 則文字，"
            "Gemini 辨識出 %d 筆品項，等待 M5 串接寫入 daily_log",
            user_key,
            len(photo_ids),
            len(images),
            len(captions),
            len(result),
        )
        # TODO(M5): 將 result 依 daily_log 欄位格式寫入使用者專屬 Sheet
        # → 寫入成功後呼叫 sheets.clear_buffer(user_key)
        return

    await sheets.append_buffer_item(user_key, "text", stripped)
