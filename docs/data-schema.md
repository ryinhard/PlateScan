# 資料表結構

Bot 與 PWA 統一使用以下 Google Sheet 工作表結構，確保前後端讀寫完全一致。

## 管理用 Google Sheet（`ADMIN_SHEET_ID`）

### `users` 工作表

| A: user_key | B: display_name | C: google_sheet_id | D: is_active | E: created_at | F: daily_count | G: count_date |
|---|---|---|---|---|---|---|
| `line:U1234abcd...` | 小明 | 1BxiMVs0XRA5... | TRUE | 2026-08-14 | 7 | 2026/08/17 |
| `tg:987654321` | 小華 | 2CyjNWt1QBZ6... | TRUE | 2026-08-14 | 0 | |

> `daily_count`／`count_date` 為 Gemini 每日用量計數（見 [commands.md](commands.md) 的「Gemini 用量控管」）。`count_date` 與當日（Asia/Taipei）不同時計數即歸零，不需排程重置。這兩欄由 `try_consume_daily_quota()` 獨立維護，`upsert_user()` 只更新 B:E 欄，兩者互不干擾。

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

> `goals` 工作表由 Bot 的 `目標`／`設定目標` 指令查詢與寫入（見 [commands.md](commands.md)），PWA 則透過公開的 `gviz/tq` 端點**唯讀**取用以顯示各項營養素達成率。
> `nutrient` 欄位由 Bot 寫入時固定為英文（`calories`/`carbs`/`protein`/`fat`），PWA 另接受使用者自行手動填寫的中文寫法（`熱量`/`碳水`/`蛋白質`/`脂肪`）。
> PWA **無法回寫**此工作表（`gviz/tq` 為唯讀端點，且前端不得持有任何金鑰），其目標設定彈窗的「複製指令」按鈕產生的是 `設定目標 熱量 2000` 這類指令文字，需貼到 Bot 送出才會生效；彈窗中可直接儲存的只有容許誤差（存於瀏覽器 `localStorage`，不進 Sheet）。
