# 部署指引

記錄 Cloud Run（後端）與 GitHub Pages（前端）的實際部署步驟。
機密憑證帶入方式決策見下方「憑證管理」一節。

## 前置需求

- 已有 Google Cloud 帳號並綁定帳單（免費額度通常足夠本專案低流量使用，但建立專案仍需綁卡）
- 安裝 [gcloud CLI](https://cloud.google.com/sdk/docs/install)（Windows 有 .exe 安裝檔）
- 已有 `credentials/service-account.json`（建立管理 Sheet 時使用的同一把 service account 金鑰）

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

`app/core/sheets.py` 以 `GOOGLE_APPLICATION_CREDENTIALS` 指向的檔案路徑讀取 service account 憑證（`Credentials.from_service_account_file()`），**程式碼不需修改**。Cloud Run 上改以 Secret Manager 掛載檔案的方式帶入：

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

本機未安裝 Docker CLI 時，可使用 `--source .` 讓 Cloud Build 於雲端建置映像檔（`Dockerfile` 已內建於專案）：

```bash
gcloud run deploy platescan \
  --source . \
  --region asia-east1 \
  --allow-unauthenticated \
  --set-secrets="/secrets/service-account.json=sheets-service-account:latest" \
  --set-env-vars="GOOGLE_APPLICATION_CREDENTIALS=/secrets/service-account.json,ADMIN_SHEET_ID=<...>,WEB_BASE_URL=<GitHub Pages 網址>,LINE_CHANNEL_SECRET=<...>,LINE_CHANNEL_ACCESS_TOKEN=<...>,TELEGRAM_BOT_TOKEN=<...>,TELEGRAM_WEBHOOK_SECRET=<...>,GEMINI_API_KEY=<...>"
```

> `--set-env-vars` 每次呼叫會整批覆蓋現有環境變數，**同一條指令內不可重複使用這個 flag**（不會合併，只有最後一次生效／或直接報錯），所有 KEY=VALUE 必須合併在同一個字串內、以逗號分隔。若某個值本身含有逗號，需改用 `--set-env-vars=^;^KEY=VALUE;...` 的自訂分隔字元語法。

部署完成後會得到一個 `https://<service>-xxxxx-xx.a.run.app` 形式的公開網址，記錄下來供下一步註冊 Webhook 使用。

### Cloud Run 背景任務注意事項：CPU 節流

若後端使用 FastAPI `BackgroundTasks` 在 webhook 回應**之後**才執行耗時工作（例如下載照片、呼叫外部 AI API），須注意 Cloud Run 預設「CPU 只在處理請求時分配」——回應送出後 container CPU 會被節流到接近零，背景任務可能跑不動或跑極慢，甚至像卡住一樣沒有任何後續回應。

解法：部署時加上 `--no-cpu-throttling`（或事後用 `gcloud run services update <service> --no-cpu-throttling`），改為「CPU 一律分配」，不需要改任何程式碼。代價是 container 存活期間（不只是處理請求的當下）都會計費 CPU，但只要沒有設定 min-instances，沒流量時仍會 scale to zero，對低流量專案的費用影響通常可忽略（實測估算落在 Cloud Run 每月免費額度內）。

## 部署前端至 GitHub Pages

1. `web/` 目錄內容需可被 GitHub Pages 直接服務（純靜態檔案，無需建置步驟）
2. 若 `web/` 不在 repo 根目錄或 `/docs`，舊版 Pages 的 branch-deploy 不支援任意子目錄作為 source path，需改用 GitHub Actions 部署：新增 `.github/workflows/pages.yml`，用 `actions/upload-pages-artifact`（`path: web`）+ `actions/deploy-pages` 部署，並在 repo 的 Pages 設定改成 `build_type: workflow`（`gh api repos/<owner>/<repo>/pages -X POST -f "build_type=workflow"`）
3. 取得 GitHub Pages 網址後，回填 Cloud Run 的 `WEB_BASE_URL` 環境變數（`gcloud run services update <service> --update-env-vars WEB_BASE_URL=<網址>`）

> GitHub 免費方案的 **private repo 不支援開啟 Pages**，只有 public repo 或付費方案可以。若專案規劃開源，建議一開始就設為 public repo（並依 [architecture.md](architecture.md) 與全域文件治理守則管理好公開/內部文件邊界），避免事後才要轉 public、需要清理 git 歷史。若不想公開整個 repo，可改用 Cloudflare Pages / Netlify / Vercel 等支援連結 private repo 部署的靜態網站服務。

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

LINE Developers Console 仍需手動將「Use webhook」切為 Enabled（無法透過 API 設定）。「Webhook redelivery」建議維持關閉，避免同一張照片因重送被重複計入。

## 整合測試

向 LINE/Telegram 傳送「圖表」指令，確認回覆連結能正確開啟 PWA 並自動帶入 `sheet_id`；並重跑一次完整端對端流程（傳照片→`ok`→辨識→寫入→回覆）確認正式環境可用。
