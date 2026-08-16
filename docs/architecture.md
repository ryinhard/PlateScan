# 系統架構

## 1. 專案目標

結合 **AI 飲食紀錄 Bot** 與 **PWA 視覺化圖表**，打造完整飲食履歷生態系：
> 1. **輸入端 (Bot)**：拍照 + 文字描述 -> 傳送至 LINE / Telegram Bot -> 自動辨識並寫入各自的 Google Sheet。
> 2. **分析端 (PWA)**：於 Bot 輸入 `圖表` -> 點擊開啟 PWA 儀表板 -> 查看日/週/月進度圖表與目標達成率。

---

## 2. 系統架構圖

```
+------------------+    +----------------------+
| User A (LINE)    |    | User B (Telegram)    |
+------------------+    +----------------------+
         |                         |
         +------------+------------+
                      | Webhook (FastAPI on Cloud Run)
                      v
       +-------------------------------+
       | Bot Adapter Layer             |
       | (line_adapter / tg_adapter)   |
       +-------------------------------+
                      | 統一封裝轉發 (含 Async Lock 防並發)
                      v
       +-------------------------------+
       | Core Handler (訊息與指令分發)  |
       +-------------------------------+
         |             |             |
         | 呼叫 API    | 讀寫暫存    | 寫入紀錄 & 回傳 PWA 連結
         v             v             v
+------------------+ +-------------------+ +-----------------------+
| Gemini（多模態    | | 管理用 Sheet      | | 各使用者的 Sheet       |
| AI 辨識）          | | - users (用戶設定) | | - daily_log (飲食紀錄) |
+------------------+ | - buffer (餐次暫存)| | - goals (每日目標)     |
                     +-------------------+ +-----------------------+
                                                     ▲
                                                     │ 透過 gviz/tq 免 API Key 讀取
                                                     │
                                           +-------------------+
                                           | 前端 PWA 儀表板    |
                                           | (Chart.js 視覺化) |
                                           +-------------------+
```

---

## 3. 多租戶與跨平台設計

### 3.1 跨平台用戶標識 (`user_key`)
為了同時支援 LINE 與 Telegram，系統統一使用 `user_key` 格式做為用戶唯一 Key：
- LINE 用戶：`line:{LINE_USER_ID}`（例：`line:U1234abcd...`）
- Telegram 用戶：`tg:{TG_CHAT_ID}`（例：`tg:987654321`）

使用者設定（含 `user_key` 對應的個人 `google_sheet_id`）存放於管理用 Google Sheet 的 `users` 工作表，欄位定義見 [data-schema.md](data-schema.md)。

### 3.2 Google Sheet 權限設計

部署者建立一個 **Google Service Account**，使用者只需：
1. 開啟自己的 Google Sheet
2. 點「共用」→ 將 Service Account Email 加為**編輯者**（供 Bot 寫入）
3. 將權限設為「知道連結的人皆可**檢視**」（供前端 PWA 讀取）

---

## 4. 零成本輕量 Buffer 暫存機制 (`buffer` 工作表)

- 照片不轉成 Base64，存平台提供的 **圖片代碼 (LINE: `message_id`, TG: `file_id`)**。
- 後端引入 `asyncio.Lock()` 防止使用者連發照片時引起的競態條件。
- `ok` 觸發辨識前會先檢查每日用量配額（同樣以 `asyncio.Lock()` 序列化計數的讀寫），超限時保留緩衝區內容不清空，見 [commands.md](commands.md) 的「Gemini 用量控管」。

---

## 5. LINE Timeout 與 0 成本 Reply 機制

- 收到 `ok` 指令時，立刻呼叫 LINE 官方 **Display Loading Animation API**（顯示「AI 輸入中...」）。
- 背景任務採用 `asyncio.gather` 平行下載多圖，總處理時間視 AI 服務回應速度而定。
- 超過 18 秒極限（LINE replyToken 有效期）時，自動切換為 **Push Message 發送** 作為 Fallback 保底。
- 指令規格詳見 [commands.md](commands.md)。

---

## 6. 前端 PWA 視覺化圖表模組

### 6.1 零設定免 API Key 對接邏輯
1. **網址帶入 Sheet ID**：
   - 使用者在 Bot 輸入 `圖表` 時，Bot 產出網址：
     `https://<your-github-pages-url>/?sheet_id={google_sheet_id}`
2. **PWA 自動帶入與儲存**：
   - PWA 開啟時，檢測 URL 參數 `sheet_id`。若存在，自動寫入本地 `localStorage.setItem('sheet_id', ...)`。
3. **`gviz/tq` 無金鑰數據讀取**：
   - PWA 無需使用 Google Sheets API Key，直接透過 Google Sheet 的公開 CSV/JSON 端點抓取數據：
     `https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&sheet=daily_log`
   - 自動相容日期格式與欄位長度放寬。

### 6.2 PWA 核心視覺化功能
- **日視圖**：4 張目標達成率進度卡（綠黃紅狀態） + 實際 vs 目標長條圖 + 當日食物明細表。
- **週視圖**：7 天群組長條圖 + 點擊長條直接跳轉至該日視圖。
- **月視圖**：每日趨勢折線圖 + 點擊數據點跳轉至該日視圖。
- **離線支援**：以 PWA Service Worker 做靜態快取，並將上一次資料存於 `localStorage`，無網路時仍可瀏覽圖表。

---

## 7. 專案整體目錄結構

```
PlateScan/
├── app/                      # 👈 後端 API 與 Bot (Cloud Run)
│   ├── main.py               # FastAPI 入口 (LINE & TG Webhook Routes)
│   ├── adapters/             # 平台轉接層 (LINE / TG)
│   ├── core/                 # 核心業務邏輯 (Gemini, Sheets, Buffer)
│   └── config.py
├── web/                      # 👈 前端 PWA 儀表板 (GitHub Pages 部署)
│   ├── index.html            # 儀表板主體與 Chart.js
│   ├── manifest.json         # PWA 安裝清單
│   ├── service-worker.js     # 離線快取
│   └── icon.svg              # App 圖示
├── docs/                     # 系統設計文件（本檔案所在目錄）
├── scripts/                  # 一次性維運腳本（如 LINE Rich Menu 設定，非後端執行期程式碼）
├── Dockerfile
└── requirements.txt
```
