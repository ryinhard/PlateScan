# 指令規格

Bot 端（LINE / Telegram）支援的文字指令。系統架構與 Reply/Push 降級機制見 [architecture.md](architecture.md)；新使用者設定教學（含截圖）見 [getting-started.md](getting-started.md)。

| 指令 | 英文/slash 別名 | 功能 | 狀態 |
|---|---|---|---|
| 傳送照片/文字 | — | 追加至當前餐次緩衝區 | ✅ 已實作 |
| `新手教學` / `教學` | `/start` | 顯示完整設定教學（LINE 加好友、Telegram 首次對話會自動觸發） | ✅ 已實作 |
| `ok` | `/ok` | 結束當前餐次，觸發抓圖與 AI 辨識寫入 | ✅ 已實作 |
| `今日` | `/today` | 查詢今日累計營養素 | ✅ 已實作 |
| `圖表` / `分析` | `/chart` | 傳送專屬 PWA 視覺化儀表板連結（含 Sheet ID） | ✅ 已實作 |
| `連結` / `原始表單` | `/link` | 回傳個人 Google Sheet 編輯連結 | ✅ 已實作 |
| `修正 熱量 700` | `/fix` | 修正最近一筆紀錄的數值欄位或餐次標籤 | ✅ 已實作 |
| `取消` | `/cancel` | 清除目前緩衝區 | ✅ 已實作 |
| `目標` | `/goal` | 回傳每日營養目標文字彙整 | ✅ 已實作 |
| `設定目標 熱量 2000` | `/setgoal` | 設定/更新每日營養目標數值 | ✅ 已實作 |
| `設定 {Sheet ID}` | `/set` | 綁定/更換 Google Sheet | ✅ 已實作 |
| `說明` | `/help` | 顯示指令列表 | ✅ 已實作 |

## 實作細節備註

- `新手教學`/`教學`（`/start`）：回傳完整設定教學文字（建立 Sheet → 分享權限 → 綁定 → 開始使用），內嵌即時讀出的 Service Account Email；LINE 加好友（`follow` 事件）與 Telegram 首次對話送出的 `/start` 都會自動觸發，其餘時間也可隨時手動輸入查看。
- `ok`：僅在緩衝區有照片或文字時觸發辨識；辨識完全失敗時仍會清空緩衝區並提示使用者重新拍照，避免無限重試；緩衝區為空時回覆提示文字（而非完全不回覆），確保 Rich Menu 按鈕點擊時一定有反應。
- `今日`：依 Asia/Taipei 時區當天日期彙總 `daily_log`。
- `連結`/`原始表單`：回傳 `https://docs.google.com/spreadsheets/d/{google_sheet_id}/edit`，供使用者直接開啟自己的個人 Sheet 檢視/編輯原始資料（與 `圖表`/`分析` 回傳的 PWA 儀表板連結不同）。Rich Menu 上顯示「原始表單」以跟「圖表」明確區分。
- `修正`：支援欄位別名 `熱量`/`碳水`/`蛋白質`/`脂肪`/`餐次`，僅修改**最近一筆**紀錄。
- `圖表`/`分析`：組出 `{WEB_BASE_URL}/?sheet_id={google_sheet_id}`，成功時額外附上一行提醒：確認 Sheet 的「一般存取權」已設為「知道連結的人可檢視」；未綁定 Sheet 或 `WEB_BASE_URL` 未設定時回傳對應錯誤訊息。
- `設定 {Sheet ID}`：接受直接貼 Google Sheets 完整網址（自動擷取 `/d/{ID}/` 中的 ID）或純 ID。綁定流程：① 先呼叫 `sheets.ensure_user_worksheets()` 驗證存取權並自動建立缺少的 `daily_log`/`goals` 工作表（含表頭）—— 驗證放在寫入 `users` 工作表**之前**，避免綁定一個實際存取不了的 Sheet ID；② 驗證失敗（通常是尚未分享編輯權限或 ID 錯誤）時回傳包含 Service Account Email 的具體修正指引；③ 成功時寫入/更新管理 Sheet `users` 工作表的 `google_sheet_id`（更新既有使用者會保留原本的 `display_name`），回覆訊息依「是否有自動建立工作表」動態組裝，並附上設定目標與圖表權限的提醒。
- `設定目標 熱量 2000`：支援欄位別名 `熱量`(kcal)/`碳水`(g)/`蛋白質`(g)/`脂肪`(g)，寫入/更新使用者個人 Sheet 的 `goals` 工作表對應列（`nutrient` 已存在時原地更新，否則新增一列）。
- `取消`：清空 `buffer` 工作表中該使用者的所有暫存項目（照片/文字），不影響已寫入的 `daily_log`。
- `目標`：讀取使用者個人 Sheet 的 `goals` 工作表，依列彙整成「營養素 數值單位」文字回覆；尚未綁定 Sheet 或 `goals` 工作表無資料時提示對應訊息。
- `說明`：回傳固定文字列出上述所有指令用法，不查詢 Sheet。

## 指令別名與容錯（`app/core/dispatcher.py` 的 `_resolve_command()`）

- 每個指令除了原本的中文觸發詞，另外提供一個英文/slash 別名（見上表），大小寫不敏感，且會自動去除 Telegram 群組常見的 `/指令@BotName` 後綴。
- 不需要參數的指令（`新手教學`/`ok`/`今日`/`圖表`/`分析`/`連結`/`原始表單`/`目標`/`取消`/`說明`）要求整則訊息去除頭尾空白後**完全等於**觸發詞，才會被視為指令；否則一律視為一般餐點描述文字追加至緩衝區（例如「今日吃了雞腿便當」不會被誤判成「今日」查詢指令）。
- 需要參數的指令（`修正`/`設定`/`設定目標`）僅比對訊息的**第一個詞**，其餘視為參數。
- 全形空白（`　`）與訊息前後空白會先正規化為一般半形空白再比對，提升手機輸入法容錯。

## Bot 選單設定

- **Telegram**：`app/core/telegram_client.py` 的 `set_my_commands()` 於 FastAPI 啟動時（`app/main.py` 的 `on_startup`）呼叫 `setMyCommands`，向 Telegram 註冊上表的英文 slash 別名（含 `/start`），使用者可在輸入框打 `/` 叫出指令選單與說明。註冊失敗僅記錄警告，不影響服務啟動。
- **LINE**：`scripts/setup_line_richmenu.py` 為一次性設定腳本（非後端執行期程式碼），以 Pillow 產生 6 格單排陽春文字版選單圖（2500x843，LINE compact 尺寸）後，透過 LINE Rich Menu API 建立並設為所有使用者的預設選單。只放 6 個高頻指令（`ok`／`今日`／`圖表`／`原始表單`／`設定`／`說明`），`修正`/`設定目標`/`目標`/`取消` 等低頻或進階指令改用打字或 Telegram slash 指令即可，不佔選單格位。`python scripts/setup_line_richmenu.py --dry-run` 可只產生預覽圖（`scripts/richmenu_preview.png`，不進版控）不呼叫任何 LINE API；不加參數執行則會**直接覆蓋正式環境的預設選單**，執行前需確認 `.env` 的 `LINE_CHANNEL_ACCESS_TOKEN` 為正確頻道。之後如需美術設計圖，可直接替換 `generate_image()` 產生的圖片來源，只要維持相同的按鈕座標即可沿用既有的 `_build_areas()` 綁定邏輯。
