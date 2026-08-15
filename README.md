# PlateScan

AI 飲食紀錄與視覺化系統。架構設計詳見 [DESIGN-v6.md](DESIGN-v6.md)，Cloud Run／GitHub Pages 部署步驟詳見 [DEPLOY.md](DEPLOY.md)。

## 本機開發設置

需求：Python 3.10+

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux

pip install -r requirements.txt

copy .env.example .env        # Windows
# cp .env.example .env        # macOS/Linux
# 編輯 .env 填入實際憑證（此檔案已列入 .gitignore，不會被提交）

uvicorn app.main:app --reload
```

伺服器啟動後可於 `http://127.0.0.1:8000/health` 確認存活狀態。

## 執行測試

```bash
pip install -r requirements-dev.txt
pytest
```

## 目前狀態

M1：FastAPI 專案骨架 + LINE/Telegram Webhook echo。
M2：管理 Sheet（`users` & `buffer` 工作表）讀寫模組（`app/core/sheets.py`），以 gspread 存取管理用 Google Sheet，並以 `asyncio.Lock()` 依 user_key 序列化讀寫，防範連續傳送照片時的競態條件。
M3：新增 Core Handler（`app/core/dispatcher.py`），LINE/Telegram adapter 收到照片時將 message_id/file_id 暫存至 buffer 工作表，收到文字時判斷 `ok` 指令（觸發讀取 buffer 並記錄 log，實際 Gemini 辨識與清空 buffer 留待 M4）或視為餐點描述暫存。
M4：新增 `app/core/downloader.py`（依 LINE/Telegram 平台以 `asyncio.gather` 平行下載緩衝區中的照片，個別失敗僅略過不中斷）與 `app/core/vision.py`（以 `google-genai` SDK 呼叫 `gemini-flash-latest` 多模態辨識照片與文字描述，輸出結構化營養素品項）。`ok` 指令已串接下載＋辨識流程；已以真實 GEMINI_API_KEY 與實際照片手動驗證辨識結果格式正確。
M5：`app/core/sheets.py` 新增使用者個人 Google Sheet（依 `google_sheet_id`）存取層與 `daily_log` 工作表讀寫函式。`ok` 指令辨識完成後，依 Asia/Taipei 時區當下時段判斷早/午/晚餐（或宵夜），將品項與營養素加總彙整成一列寫入使用者專屬 `daily_log` 並清空 buffer；新增「今日」指令加總當日 `daily_log` 回傳文字摘要；新增「修正 <欄位> <值>」指令修正最近一筆紀錄的餐次標籤或數值欄位（例如自動時段判斷誤標時可用「修正 餐次 午餐」手動修正）。`handle_text()` 回傳欲回覆使用者的文字，實際透過 LINE Reply/Push 或 Telegram sendMessage 送出留待 M6。
M6：新增 `app/core/line_client.py`（LINE Loading Animation / Reply Message / Push Message）與 `app/core/telegram_client.py`（Telegram sendMessage）。`app/adapters/line_adapter.py`、`app/adapters/tg_adapter.py` 皆改為 image 訊息同步暫存、text 訊息以 FastAPI `BackgroundTasks` 背景處理（避免 `ok` 指令觸發的 Gemini 辨識拖慢 webhook 回應）；LINE 端偵測到 `ok` 指令會先同步觸發 Loading Animation，背景任務完成後依耗時是否超過 18 秒（對應 replyToken 有效期）決定使用 Reply（0 成本）或降級改用 Push；Telegram 端背景任務完成後統一以 sendMessage 送出回覆。程式碼已完成並通過單元測試，真實環境端對端測試已大致完成，僅剩 Gemini 服務端持續性 503 過載待恢復後重測。
M7：新增 `web/`（前端 PWA 儀表板），整合既有 `Daily_Nutrition_Distribution_Chart` 專案並改造資料來源：移除原本需使用者輸入 API Key 的 Google Sheets API v4 呼叫，改用免金鑰的 `gviz/tq` 端點（`https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&sheet=daily_log`）讀取資料，並支援網址參數 `?sheet_id=` 自動寫入 `localStorage`（對應 Bot 未來的「圖表」指令連結）。資料結構配合 `daily_log` 工作表欄位（`date/meal/items/calories/carbs_g/protein_g/fat_g`，一列為一個餐次彙整紀錄）調整為「餐次明細」而非逐項食物；日/週/月三種視圖（達成率長條圖／折線圖）與離線 Service Worker 快取沿用原專案設計。尚未部署，待 M8 以真實 Google Sheet 端對端驗證。
