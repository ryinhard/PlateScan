# 部署指引（M8）

記錄 Cloud Run（後端）與 GitHub Pages（前端）的實際部署步驟，供中斷後接續使用。
機密憑證帶入方式決策見下方「憑證管理」一節。

## 前置需求

- 已有 Google Cloud 帳號並綁定帳單（免費額度通常足夠本專案低流量使用，但建立專案仍需綁卡）
- 安裝 [gcloud CLI](https://cloud.google.com/sdk/docs/install)（Windows 有 .exe 安裝檔）
- 已有 `credentials/service-account.json`（M2 建立管理 Sheet 時使用的同一把 service account 金鑰）

## 一次性設定

```bash
gcloud auth login
gcloud config set project <YOUR_PROJECT_ID>
gcloud services enable run.googleapis.com secretmanager.googleapis.com
```

### 新專案首次部署常見卡點：Compute 預設服務帳號權限不足

新建立的 GCP 專案執行 `gcloud run deploy --source .` 時，即使 Compute Engine 預設服務帳號（`<PROJECT_NUMBER>-compute@developer.gserviceaccount.com`）已有專案層級 `roles/editor`，Cloud Build 上傳原始碼時仍可能回傳：

```
ERROR: (gcloud.run.deploy) PERMISSION_DENIED: Build failed because the default service account is missing required IAM permissions. ...
could not resolve source: ... IAM permission denied for service account <PROJECT_NUMBER>-compute@developer.gserviceaccount.com
```

實測 `roles/cloudbuild.builds.builder` 不足以解決，需額外授予 **Cloud Run Builder**（`roles/run.builder`）角色：

```bash
PROJECT_NUMBER=$(gcloud projects describe <YOUR_PROJECT_ID> --format="value(projectNumber)")
gcloud projects add-iam-policy-binding <YOUR_PROJECT_ID> \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/run.builder"
```

授權後可能需等待數十秒到數分鐘才會生效，再重新執行 `gcloud run deploy`。

## 憑證管理：Secret Manager 掛載檔案

`app/core/sheets.py` 目前以 `GOOGLE_APPLICATION_CREDENTIALS` 指向的檔案路徑讀取 service account 憑證（`Credentials.from_service_account_file()`），**程式碼不需修改**。Cloud Run 上改以 Secret Manager 掛載檔案的方式帶入：

```bash
# 建立 secret（只需一次；金鑰輪替時用 `gcloud secrets versions add` 新增版本）
gcloud secrets create sheets-service-account --data-file=credentials/service-account.json

# 授權 Cloud Run 執行時的 Service Account 可讀取此 secret
# <RUNTIME_SA_EMAIL> 預設是 <PROJECT_NUMBER>-compute@developer.gserviceaccount.com，
# 或部署時以 --service-account 指定的自訂 Service Account
gcloud secrets add-iam-policy-binding sheets-service-account \
  --member="serviceAccount:<RUNTIME_SA_EMAIL>" \
  --role="roles/secretmanager.secretAccessor"
```

部署時透過 `--set-secrets` 掛成容器內檔案，並以 `--set-env-vars` 指向該路徑（見下方部署指令）。

## 部署後端至 Cloud Run

本機未安裝 Docker CLI，使用 `--source .` 讓 Cloud Build 於雲端建置映像檔（`Dockerfile` 已於 M8 建立）：

```bash
gcloud run deploy platescan \
  --source . \
  --region asia-east1 \
  --allow-unauthenticated \
  --set-secrets="/secrets/service-account.json=sheets-service-account:latest" \
  --set-env-vars="GOOGLE_APPLICATION_CREDENTIALS=/secrets/service-account.json,ADMIN_SHEET_ID=<...>,WEB_BASE_URL=<GitHub Pages 網址>,LINE_CHANNEL_SECRET=<...>,LINE_CHANNEL_ACCESS_TOKEN=<...>,TELEGRAM_BOT_TOKEN=<...>,TELEGRAM_WEBHOOK_SECRET=<...>,GEMINI_API_KEY=<...>"
```

> `--set-env-vars` 每次呼叫會整批覆蓋現有環境變數，**同一條指令內不可重複使用這個 flag**（不會合併，只有最後一次生效／或直接報錯），所有 KEY=VALUE 必須合併在同一個字串內、以逗號分隔。若某個值本身含有逗號，需改用 `--set-env-vars=^;^KEY=VALUE;...` 的自訂分隔字元語法。

部署完成後會得到一個 `https://platescan-xxxxx-xx.a.run.app` 形式的公開網址，記錄下來供下一步註冊 Webhook 使用。

## 部署前端至 GitHub Pages

1. `web/` 目錄內容需可被 GitHub Pages 直接服務（純靜態檔案，無需建置步驟）
2. Repo 設定 → Pages → Source 選擇對應分支與 `web/` 目錄（或另建 `gh-pages` 分支存放 `web/` 內容）
3. 取得 GitHub Pages 網址後，回填 Cloud Run 的 `WEB_BASE_URL` 環境變數（`gcloud run services update platescan --update-env-vars WEB_BASE_URL=<網址>`）

## 註冊正式 Webhook

```bash
# LINE
curl -X PUT https://api.line.me/v2/bot/channel/webhook/endpoint \
  -H "Authorization: Bearer <LINE_CHANNEL_ACCESS_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"endpoint": "<Cloud Run 網址>/webhook/line"}'

# Telegram
curl -X POST https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook \
  -d "url=<Cloud Run 網址>/webhook/telegram" \
  -d "secret_token=<TELEGRAM_WEBHOOK_SECRET>"
```

LINE Developers Console 仍需手動將「Use webhook」切為 Enabled（無法透過 API 設定，M6 已記錄此限制）。

## 整合測試

依 [TODO.md](TODO.md) M8 最後一項：向 LINE/Telegram 傳送「圖表」指令，確認回覆連結能正確開啟 PWA 並自動帶入 `sheet_id`，重跑一次完整端對端流程。
