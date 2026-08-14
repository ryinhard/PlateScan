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

## 目前狀態

M1：FastAPI 專案骨架 + LINE/Telegram Webhook echo。尚未串接 Gemini 辨識、Google Sheets 讀寫或前端 PWA，詳見 [TODO.md](TODO.md)。
