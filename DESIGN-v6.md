# 營養素擷取與視覺化系統 - 系統設計 (v6.0 雙平台、PWA 圖表整合與 0 成本架構)

## 1. 專案目標

結合 **AI 飲食紀錄 Bot** 與 **PWA 視覺化圖表 (Daily_Nutrition_Distribution_Chart)**，打造完整飲食履歷生態系：
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
| Gemini 1.5 Flash | | 管理用 Sheet      | | 各使用者的 Sheet       |
| (多模態 AI 辨識)  | | - users (用戶設定) | | - daily_log (飲食紀錄) |
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

### 3.2 使用者設定儲存 (`users` 工作表)

設定存放於 **管理用 Google Sheet**（`ADMIN_SHEET_ID`）的 `users` 工作表：

**管理用 Sheet - 工作表名稱：`users`**

| A: user_key | B: display_name | C: google_sheet_id | D: is_active | E: created_at |
|---|---|---|---|---|
| `line:U1234abcd...` | 小明 | 1BxiMVs0XRA5... | TRUE | 2026-08-14 |
| `tg:987654321` | 小華 | 2CyjNWt1QBZ6... | TRUE | 2026-08-14 |

### 3.3 Google Sheet 權限設計

部署者建立一個 **Google Service Account**，使用者只需：
1. 開啟自己的 Google Sheet
2. 點「共用」→ 將 Service Account Email 加為**編輯者**（供 Bot 寫入）
3. 將權限設為「知道連結的人皆可**檢視**」（供前端 PWA 讀取）

---

## 4. 零成本輕量 Buffer 暫存機制 (`buffer` 工作表)

### 4.1 核心儲存邏輯
- 照片不轉成 Base64，存平台提供的 **圖片代碼 (LINE: `message_id`, TG: `file_id`)**。
- 後端引入 `asyncio.Lock()` 防止使用者連發照片時引起的競態條件。

---

## 5. 使用者互動、平行下載與極限 Timeout 降級機制

### 5.1 LINE Timeout 與 0 成本 Reply 機制
- 收到 `ok` 指令時，立刻呼叫 LINE 官方 **Display Loading Animation API**（顯示「AI 輸入中...」）。
- 背景任務採用 `asyncio.gather` 平行下載多圖（1 秒內下載完成），總處理時間控制在 4~6 秒。
- 超過 18 秒極限時，自動切換為 **Push Message 發送** 作為 Fallback 保底。

### 5.2 指令列表

| 指令 | 功能 |
|---|---|
| 傳送照片/文字 | 追加至當前餐次緩衝區 |
| `ok` | 結束當前餐次，觸發抓圖與 AI 辨識寫入 |
| `今日` | 查詢今日累計營養素 |
| `圖表` / `分析` | **傳送專屬 PWA 視覺化儀表板連結 (含 Sheet ID)** |
| `取消` | 清除目前緩衝區 |
| `修正 熱量 700` | 修正最近一筆紀錄 |
| `目標` | 回傳 `goals` 工作表連結 |
| `設定 {Sheet ID}` | 綁定/更換 Google Sheet |
| `說明` | 顯示指令列表 |

---

## 6. 前端 PWA 視覺化圖表模組整合 (Daily_Nutrition_Distribution_Chart)

### 6.1 模組定位
將 `Daily_Nutrition_Distribution_Chart` 專案完全整合做為本系統的前端視覺化 UI。

### 6.2 零設定免 API Key 對接邏輯
1. **網址帶入 Sheet ID**：
   - 使用者在 Bot 輸入 `圖表` 時，Bot 產出網址：
     `https://<your-github-pages-url>/?sheet_id={google_sheet_id}`
2. **PWA 自動帶入與儲存**：
   - PWA 開啟時，檢測 URL 參數 `sheet_id`。若存在，自動寫入本地 `localStorage.setItem('sheet_id', ...)`。
3. **`gviz/tq` 無金鑰數據讀取**：
   - PWA 無需使用 Google Sheets API Key，直接透過 Google Sheet 的公開 CSV/JSON 端點抓取數據：
     `https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&sheet=daily_log`
   - 自動相容日期格式與欄位長度放寬。

### 6.3 PWA 核心視覺化功能
- **日視圖**：4 張目標達成率進度卡（綠黃紅狀態） + 實際 vs 目標長條圖 + 當日食物明細表。
- **週視圖**：7 天群組長條圖 + 點擊長條直接跳轉至該日視圖。
- **月視圖**：每日趨勢折線圖 + 點擊數據點跳轉至該日視圖。
- **離線支援**：以 PWA Service Worker 做靜態快取，並將上一次資料存於 `localStorage`，無網路時仍可瀏覽圖表。

---

## 7. Google Sheet 統一資料結構

Bot 與 PWA 統一使用以下工作表結構，確保前後端讀寫完全一致：

**工作表名稱：`daily_log`**

| A: date | B: meal | C: items | D: calories | E: carbs_g | F: protein_g | G: fat_g |
|---|---|---|---|---|---|---|
| 2026/08/14 | 午餐 | 雞腿便當, 味噌湯 | 650 | 80 | 30 | 20 |

> *註：日期格式統一採用 `YYYY/MM/DD`，相容 PWA `parseDate()` 自動解析。*

**工作表名稱：`goals`（每日營養目標）**

| A: nutrient | B: target | C: unit |
|---|---|---|
| calories | 2000 | kcal |
| carbs | 250 | g |
| protein | 120 | g |
| fat | 60 | g |

---

## 8. 專案整體目錄結構

```
nutrition-extractor/
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
├── Dockerfile
├── requirements.txt
└── DESIGN-v6.md              # 本系統設計文件
```

---

## 9. 開發里程碑

| 階段 | 內容 | 預估時間 |
|---|---|---|
| M1 | FastAPI 專案骨架 + Base Adapter + LINE/TG Webhook echo | 1.5 hr |
| M2 | 管理 Sheet (`users` & `buffer`) 讀寫模組 + Async Lock | 1.5 hr |
| M3 | 照片 `message_id`/`file_id` 暫存 + `ok` 指令觸發機制 | 1.5 hr |
| M4 | `asyncio.gather` 多圖平行下載 + Gemini Vision 解析 | 2 hr |
| M5 | 個人 Google Sheets 動態寫入與 `今日` 查詢 | 1.5 hr |
| M6 | LINE BackgroundTasks + Loading API + Reply/Push 降級整合 | 1 hr |
| M7 | PWA 視圖微調 (支援 `gviz/tq` 讀取與 URL `sheet_id` 帶入) | 1.5 hr |
| M8 | 部署至 Cloud Run & GitHub Pages + 雙端整合測試 | 1.5 hr |

**預估總開發時間：12 小時**
