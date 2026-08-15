# 資料表結構

Bot 與 PWA 統一使用以下 Google Sheet 工作表結構，確保前後端讀寫完全一致。

## 管理用 Google Sheet（`ADMIN_SHEET_ID`）

### `users` 工作表

| A: user_key | B: display_name | C: google_sheet_id | D: is_active | E: created_at |
|---|---|---|---|---|
| `line:U1234abcd...` | 小明 | 1BxiMVs0XRA5... | TRUE | 2026-08-14 |
| `tg:987654321` | 小華 | 2CyjNWt1QBZ6... | TRUE | 2026-08-14 |

### `buffer` 工作表

暫存使用者傳送照片（`message_id`/`file_id`）與文字描述，觸發 `ok` 指令後清空。詳見 [architecture.md](architecture.md) 第 4 節。

## 使用者個人 Google Sheet（`google_sheet_id`，由使用者自行建立並分享給 Service Account）

### `daily_log` 工作表

| A: date | B: meal | C: items | D: calories | E: carbs_g | F: protein_g | G: fat_g | H: confidence |
|---|---|---|---|---|---|---|---|
| 2026/08/14 | 午餐 | 雞腿便當, 味噌湯 | 650 | 80 | 30 | 20 | 0.85 |

> 日期格式統一採用 `YYYY/MM/DD`，相容 PWA `parseDate()` 自動解析。
> `confidence`（0.0~1.0）為 Gemini 回傳的 `confidence_score`，目前僅記錄供之後觀察分佈使用，尚無任何依此欄位觸發的行為（例如信心度過低時切換更高階模型重算）；PWA 目前不讀取此欄位，僅供 Bot 端寫入與未來擴充。

### `goals` 工作表（每日營養目標）

| A: nutrient | B: target | C: unit |
|---|---|---|
| calories | 2000 | kcal |
| carbs | 250 | g |
| protein | 120 | g |
| fat | 60 | g |

> `goals` 工作表目前僅供 PWA 讀取顯示達成率使用；Bot 端尚無寫入/查詢 `goals` 的指令（見 [commands.md](commands.md) 的 `目標` 指令狀態）。
