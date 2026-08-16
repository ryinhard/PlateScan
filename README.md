# PlateScan

AI 飲食紀錄與視覺化系統：拍照傳送給 LINE / Telegram Bot，AI 自動辨識營養素並寫入 Google Sheet，再透過 PWA 儀表板查看日/週/月進度圖表。

系統架構詳見 [docs/architecture.md](docs/architecture.md)，指令規格詳見 [docs/commands.md](docs/commands.md)，資料表欄位詳見 [docs/data-schema.md](docs/data-schema.md)，部署步驟詳見 [docs/deployment.md](docs/deployment.md)。

## 給使用者：如何開始使用

已經加 LINE 好友或開始跟 Telegram Bot 對話？加好友／首次對話當下 Bot 會自動傳送設定教學；完整圖文版（含 Google Sheet 建立與權限設定步驟）見 [docs/getting-started.md](docs/getting-started.md)。

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

- **Bot 端**：LINE / Telegram Webhook 已串接照片緩衝、AI 辨識（Gemini，含 503 過載自動降級備用模型）、寫入使用者 Google Sheet、Reply/Push 降級回覆，並具備每人每日的辨識次數與單次照片張數上限以控管 API 成本。已支援的指令列表見 [docs/commands.md](docs/commands.md)。
- **前端 PWA**：`web/` 儀表板透過免金鑰的 `gviz/tq` 端點讀取 Google Sheet 資料，提供日/週/月營養素進度圖表，並支援離線快取。
- **部署**：後端已上線 Cloud Run，前端已上線 GitHub Pages（詳見 [docs/deployment.md](docs/deployment.md)）。

各里程碑的詳細開發紀錄不再收錄於此（避免與程式碼實際狀態分岔），如需歷史脈絡請查 git log。
