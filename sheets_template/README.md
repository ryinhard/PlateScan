# Google Sheet 範例檔

對應 [docs/data-schema.md](../docs/data-schema.md) 的資料結構，供實際部署時建立雲端 Sheet，或作為 GitHub 公開的欄位範例。

| 檔案 | 對應用途 | 內含工作表 |
|---|---|---|
| `admin_sheet_sample.xlsx` | 管理後台 Sheet（`ADMIN_SHEET_ID`），全系統只有一份 | `users`（使用者設定）、`buffer`（餐次暫存） |
| `personal_sheet_sample.xlsx` | 一般使用者個人 Sheet，每位使用者各自一份 | `daily_log`（飲食紀錄）、`goals`（每日營養目標） |

## 使用方式

1. 於 Google Drive 上傳對應的 `.xlsx` 檔案，Google 會自動轉換成 Google Sheets（或先「以 Google 試算表開啟」）。
2. 刪除範例資料列（第 2 列起），僅保留標題列。
3. 依角色設定共用權限：
   - **管理後台 Sheet**：僅分享給 Service Account Email（**編輯者**），不需公開。
   - **個人 Sheet**：分享給 Service Account Email（**編輯者**，供 Bot 寫入）＋設為「知道連結的人皆可**檢視**」（供前端 PWA 以 `gviz/tq` 讀取）。
4. 複製 Sheet 網址中的 ID（`https://docs.google.com/spreadsheets/d/{這一段}/edit`）：
   - 管理後台 Sheet 的 ID 填入後端 `.env` 的 `ADMIN_SHEET_ID`。
   - 個人 Sheet 的 ID 由使用者透過 Bot 指令「設定 {Sheet ID}」自行綁定，寫入 `users` 工作表的 `google_sheet_id` 欄位。

## 欄位格式備註

- 日期一律使用 `YYYY/MM/DD`（例：`2026/08/14`），相容前端 `parseDate()` 自動解析。
- `buffer.created_at` 由後端寫入 UTC ISO 8601 格式（例：`2026-08-14T04:30:05.123456+00:00`），僅供除錯追蹤，不影響邏輯判斷。
- `users.is_active` 目前恆為 `TRUE`（保留欄位，供未來停用帳號使用）。
