"""FastAPI 入口：掛載 LINE / Telegram webhook router 與健康檢查路由。"""

import logging

from fastapi import FastAPI

from app.adapters.line_adapter import router as line_router
from app.adapters.tg_adapter import router as tg_router

# 匯入時即會觸發 app.config 中 Settings() 的實例化與驗證，
# 若缺少必要環境變數會在此立即以 ValidationError 清楚失敗。

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app.main")

app = FastAPI(title="PlateScan")

app.include_router(line_router)
app.include_router(tg_router)


@app.on_event("startup")
async def on_startup() -> None:
    """啟動時記錄伺服器已就緒。"""
    logger.info("PlateScan 啟動完成，line/telegram adapter 已就緒")


@app.get("/health")
async def health() -> dict[str, str]:
    """存活檢查路由，供 M8 Cloud Run 健康檢查與本機測試使用。"""
    return {"status": "ok"}
