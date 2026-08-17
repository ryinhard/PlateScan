# 資料表結構

Bot 與 PWA 統一使用以下 Google Sheet 工作表結構，確保前後端讀寫完全一致。

## 管理用 Google Sheet（`ADMIN_SHEET_ID`）

### `users` 工作表

| A: user_key | B: display_name | C: google_sheet_id | D: is_active | E: created_at | F: daily_count | G: count_date | H: meal_schedule |
|---|---|---|---|---|---|---|---|
| `line:U1234abcd...` | 小明 | 1BxiMVs0XRA5... | TRUE | 2026-08-14 | 7 | 2026/08/17 | |
| `tg:987654321` | 小華 | 2CyjNWt1QBZ6... | TRUE | 2026-08-14 | 0 | | 05:00,11:30,14:00,17:30,21:00 |

> `daily_count`／`count_date` 為 Gemini 每日用量計數（見 [commands.md](commands.md) 的「Gemini 用量控管」）。`count_date` 與當日（Asia/Taipei）不同時計數即歸零，不需排程重置。這兩欄由 `try_consume_daily_quota()` 獨立維護，`upsert_user()` 只更新 B:E 欄，兩者互不干擾。
> `meal_schedule`（見 [commands.md](commands.md) 的「餐次時段規格」）為使用者自訂的五個餐次開始時間，以逗號分隔的 `HH:MM` 字串儲存（如 `05:00,11:30,14:00,17:30,21:00`），空白代表使用預設時段。由 `upsert_meal_schedule()` 獨立維護 H 欄，同樣不影響 `upsert_user()` 的 B:E 欄。
>
> ⚠️ **手動步驟**：`users` 工作表沒有自動建表機制，新增 `meal_schedule` 欄位後須手動在管理 Sheet 的 `users` 第 1 列 H 欄補上表頭文字 `meal_schedule`。

### `buffer` 工作表

暫存使用者傳送照片（`message_id`/`file_id`）與文字描述，觸發 `ok` 指令後清空。機制設計見 [architecture.md](architecture.md) 第 4 節。

| A: user_key | B: item_type | C: content | D: created_at |
|---|---|---|---|
| `line:U1234abcd...` | `photo` | `465726095868788771` | 2026-08-17T09:12:34.567890+00:00 |
| `line:U1234abcd...` | `text` | 雞腿便當加了半碗飯 | 2026-08-17T09:12:51.221004+00:00 |

> `item_type` 固定為 `photo` 或 `text` 兩種值。`content` 依 `item_type` 而異：`photo` 存平台提供的圖片代碼（LINE 為 `message_id`、Telegram 為 `file_id`，**不存 Base64**），`text` 存使用者輸入的餐點描述原文。
> `created_at` 為 UTC ISO 8601 字串（`datetime.now(timezone.utc).isoformat()`），僅供人工排查用；`get_buffer_items()` 是依**列的先後順序**回傳，不依此欄排序。
> 同一 `user_key` 的讀寫以 `asyncio.Lock()` 序列化（見 [architecture.md](architecture.md) 第 4 節），避免使用者連發照片時的競態條件。
>
> ⚠️ **手動步驟**：與 `users` 相同，`buffer` 工作表**沒有自動建表機制**，須在建立管理 Sheet 時手動建立工作表並填入第 1 列表頭（見下方「管理 Sheet 的手動建表表頭」）。

### 管理 Sheet 的手動建表表頭

管理 Sheet（`ADMIN_SHEET_ID`）的 `users`／`buffer` 兩張工作表**不會由程式自動建立**（自動建表機制只存在於使用者個人 Sheet 的 `daily_log`／`goals`，見 `app/core/sheets.py` 的 `ensure_user_worksheets()`）。首次部署時必須手動建好，否則 Bot 一啟用即失敗。完整操作步驟見 [deployment.md](deployment.md) 的「建立管理用 Google Sheet」一節，兩張工作表的第 1 列請逐格填入下列表頭文字：

| 工作表名稱 | 第 1 列表頭（A 欄起，逐格一個） |
|---|---|
| `users` | `user_key`、`display_name`、`google_sheet_id`、`is_active`、`created_at`、`daily_count`、`count_date`、`meal_schedule` |
| `buffer` | `user_key`、`item_type`、`content`、`created_at` |

> 工作表名稱須與 `app/core/sheets.py` 的 `USERS_WORKSHEET`／`BUFFER_WORKSHEET` 常數完全一致（區分大小寫）。
> 表頭文字本身不被程式讀取（讀寫一律以欄位位置為準，且讀取時以 `get_all_values()[1:]` 跳過第 1 列），但**第 1 列必須存在**，否則首筆資料會被當成表頭略過。日後新增欄位時亦須手動補上表頭文字，否則資料會寫進沒有表頭的欄位。

## 使用者個人 Google Sheet（`google_sheet_id`，由使用者自行建立並分享給 Service Account）

### `daily_log` 工作表

| A: date | B: meal | C: items | D: calories | E: carbs_g | F: protein_g | G: fat_g | H: confidence |
|---|---|---|---|---|---|---|---|
| 2026/08/14 | 午餐 | 雞腿便當, 味噌湯 | 650 | 80 | 30 | 20 | 0.85 |

> 日期格式統一採用 `YYYY/MM/DD`，相容 PWA `parseDate()` 自動解析。
> `confidence`（0.0~1.0）為 Gemini 回傳的 `confidence_score`，目前僅記錄供之後觀察分佈使用，尚無任何依此欄位觸發的行為（例如信心度過低時切換更高階模型重算）；PWA 目前不讀取此欄位，僅供 Bot 端寫入與未來擴充。
> `meal` 合法值固定為五種：`早餐`／`午餐`／`下午茶`／`晚餐`／`宵夜`（詳見 [commands.md](commands.md) 的「餐次時段規格」）。判定為宵夜且時間早於當天早餐起點時，該筆紀錄的 `date` 會往前推一天（跨午夜歸日）；既有歷史資料不做回溯調整。

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
