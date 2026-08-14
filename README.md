# PlateScan

營養素擷取與視覺化系統。架構設計詳見 [DESIGN-v6.md](DESIGN-v6.md)，開發規範詳見 [CLAUDE.md](CLAUDE.md)，目前進度詳見 [TODO.md](TODO.md)。

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
M5：`app/core/sheets.py` 新增使用者個人 Google Sheet（依 `google_sheet_id`）存取層與 `daily_log` 工作表讀寫函式。`ok` 指令辨識完成後，依 Asia/Taipei 時區當下時段判斷早/午/晚餐（或宵夜），將品項與營養素加總彙整成一列寫入使用者專屬 `daily_log` 並清空 buffer；新增「今日」指令加總當日 `daily_log` 回傳文字摘要；新增「修正 <欄位> <值>」指令修正最近一筆紀錄的餐次標籤或數值欄位（例如自動時段判斷誤標時可用「修正 餐次 午餐」手動修正）。`handle_text()` 回傳欲回覆使用者的文字，實際透過 LINE Reply/Push 或 Telegram sendMessage 送出留待 M6，詳見 [TODO.md](TODO.md)。
