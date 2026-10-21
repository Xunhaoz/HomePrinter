# HomePrinter API 規格

後端：FastAPI（[backend/src/homeprinter/app.py](../backend/src/homeprinter/app.py)）。互動式文件在 `/docs`（Swagger UI），機器可讀的規格在 `/openapi.json`。

## 1. 通用規範

### Base URL

| 部署方式 | Base URL |
|---|---|
| 直接跑後端（`uv run homeprinter`） | `http://<host>:8000` |
| Docker Compose（前端 nginx 反向代理） | `https://<你的網域>/api`（nginx 會拿掉 `/api` 前綴再轉給後端，後端用 `ROOT_PATH=/api` 讓 `/api/docs` 正常運作） |

下文的路徑都相對於 Base URL。

### 認證

- 有設定 `ACCESS_CODE` 時，除了 `/auth/*` 和 `/health`，所有 endpoint 都要帶 session cookie `homeprinter_session`，否則回 `401 {"detail": "login required"}`。
- 先用 `POST /auth/login` 換到 cookie。Cookie 設定為 `HttpOnly`、`SameSite=Strict`；連線是 HTTPS 時另加 `Secure`。有效期 `SESSION_DAYS` 天（預設 30）。
- 通行碼不分大小寫，前後空白會被忽略。更換 `ACCESS_CODE` 後，所有既有 session 立即失效。
- 防暴力破解：同一個來源 IP 在 15 分鐘內猜錯 5 次就鎖住，所有來源合計猜錯 30 次就全部鎖住。鎖住期間回 `429`，並附 `Retry-After`（秒）。
- 沒設 `ACCESS_CODE` 時不需要登入（只適合在區網內使用），啟動時 log 會出現警告。

### 請求格式

| 類型 | Content-Type | 用在 |
|---|---|---|
| 上傳檔案和表單 | `multipart/form-data` | `POST /print`、`POST /print-schedule`、`POST /scan` |
| JSON | `application/json` | `POST /auth/login`、`PATCH /print-schedule/{id}` |

上傳上限為 `MAX_UPLOAD_MB`（預設 100 MB），超過回 `413`。

### 回應與錯誤

- 成功時回 JSON；`204` 沒有 body；下載檔案則回二進位內容。
- 錯誤一律是 `{"detail": "<給人看的英文訊息>"}`；欄位驗證失敗（`422`）時，`detail` 是 FastAPI 的錯誤陣列。

| 狀態碼 | 意義 |
|---|---|
| 400 | 檔案或參數不合法（不支援的格式、頁碼超出範圍、照片要求雙面、cron 寫錯、時間已過……） |
| 401 | 沒登入／session 過期，或通行碼錯誤 |
| 404 | 找不到資源 |
| 409 | 狀態衝突：掃描器忙碌、送紙器沒紙、掃描還沒完成、排程已經執行過無法再啟用 |
| 410 | 掃描檔已被刪除 |
| 413 | 檔案太大 |
| 422 | 欄位驗證失敗（型別或列舉值錯誤） |
| 429 | 通行碼錯太多次 |
| 502 | 連不到印表機或掃描器，或裝置回報錯誤 |

### 時間格式

- 列印工作（`/jobs`）的 `created_at`、`finished_at` 是 **Unix epoch 秒數（float）**。
- 排程與掃描的時間是 **ISO 8601 字串**，含時區位移；排程使用它自己的 `timezone`，掃描使用 `DEFAULT_TIMEZONE`。

### ID

ID 都是隨機的 hex 字串，無法猜測。列印工作 ID 為 12 碼，排程和掃描 ID 為 32 碼。

---

## 2. 認證 Auth

### `GET /auth/session`

查詢這個瀏覽器是否已登入。不需要登入就能呼叫。

```json
{ "auth_required": true, "authenticated": false }
```

### `POST /auth/login`

```json
{ "code": "kitten" }
```

| 回應 | 說明 |
|---|---|
| `204` + `Set-Cookie` | 登入成功 |
| `401 {"detail":"wrong code"}` | 通行碼錯誤 |
| `429` + `Retry-After` | 錯太多次，暫時鎖住 |

### `POST /auth/logout`

`204`，清除 cookie。

### `GET /health`

`{"ok": true}`，不需要登入，給 Docker healthcheck 用，不會出現在 `/docs`。

---

## 3. 印表機 Printer

### `GET /printer`

印表機狀態與墨水量。

```json
{
  "model": "EPSON L6490 Series",
  "state": "idle",
  "state_reasons": ["none"],
  "message": null,
  "media_ready": "iso_a4_210x297mm",
  "ink": [
    { "name": "Black ink", "level": 80, "color": "#000000" },
    { "name": "Cyan ink", "level": 64, "color": "#00FFFF" }
  ]
}
```

| 欄位 | 說明 |
|---|---|
| `state` | `idle`、`processing`、`stopped`、`unknown` |
| `state_reasons` | IPP 的 printer-state-reasons，例如 `media-empty-error`、`marker-supply-low-warning` |
| `ink[].level` | 0–100；IPP 規範中 `-1`、`-2`、`-3` 代表未知 |

錯誤：`502` 表示連不到印表機。

### `GET /printer/capabilities`

```json
{
  "media": ["iso_a4_210x297mm", "na_index-4x6_4x6in", "na_letter_8.5x11in", "..."],
  "media_default": "iso_a4_210x297mm",
  "color_modes": ["auto", "color", "monochrome"],
  "qualities": ["normal", "high"],
  "scaling": ["auto", "auto-fit", "fill", "fit", "none"],
  "sides_pdf": ["one-sided", "two-sided-long-edge", "two-sided-short-edge"]
}
```

---

## 4. 列印 Print & Jobs

### `POST /print` → `202`

`multipart/form-data`：

| 欄位 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `file` | file | **必填** | PDF 或圖片（JPEG、PNG、HEIC……） |
| `copies` | int 1–99 | `1` | 份數 |
| `media` | string | 印表機預設 | 紙張，例如 `iso_a4_210x297mm`、`na_index-4x6_4x6in` |
| `color_mode` | `auto` \| `color` \| `monochrome` | 印表機預設 | 黑白的 PDF 會以灰階送出 |
| `quality` | `normal` \| `high` | 印表機預設 | |
| `scaling` | `fit` \| `fill` \| `none` \| `auto` \| `auto-fit` | `fit` | `fit` 縮放到邊界內；`fill` 裁切填滿；`none` 原尺寸 |
| `sides` | `one-sided` \| `two-sided-long-edge` \| `two-sided-short-edge` | `one-sided` | **僅 PDF**。直式文件翻頁用 long-edge，上下翻用 short-edge |
| `pages` | string | 全部 | **僅 PDF**，例如 `1-3,5` |
| `dry_run` | bool | `false` | 轉檔後向印表機驗證，但不實際列印 |

回應是一個 Job 物件，見下文。錯誤：`400` 表示檔案或選項不合法，`413` 表示檔案太大。

### Job 物件

```json
{
  "id": "a1b2c3d4e5f6",
  "filename": "doc.pdf",
  "kind": "pdf",
  "status": "printing",
  "dry_run": false,
  "pages": [1, 2, 3],
  "copies": 2,
  "sides": "two-sided-long-edge",
  "pages_done": 2,
  "pages_total": 6,
  "printer_job_id": 123,
  "error": null,
  "created_at": 1792555200.12,
  "finished_at": null
}
```

| 欄位 | 說明 |
|---|---|
| `kind` | `pdf` 或 `image` |
| `status` | `queued` → `rendering`（僅 PDF）→ `printing` → `completed`，或是 `failed`、`canceled` |
| `pages` | 實際要印的頁碼（從 1 起算）；照片為 `null` |
| `pages_done` / `pages_total` | 進度，`pages_total` 已經乘上份數 |

列印工作**只存在記憶體裡**，服務重啟後就會消失；最多保留最近 100 筆已結束的工作。

### `GET /jobs`

列出所有 Job，最新的在前。

### `GET /jobs/{id}`

取得單一 Job。找不到時回 `404`。

### `DELETE /jobs/{id}`

取消並回傳 Job。`queued` 的工作會立刻變成 `canceled`；`printing` 的工作會請印表機取消；已結束的工作不受影響。

---

## 5. 排程列印 Print Schedules

### `POST /print-schedule` → `201`

除了 `POST /print` 的所有欄位外，另外接受：

| 欄位 | 型別 | 說明 |
|---|---|---|
| `kind` | `once` \| `cron` | **必填** |
| `run_at` | string | `kind=once` 時必填，例如 `2026-10-22T09:00`。沒帶時區位移時，以 `timezone` 解讀；不能是過去的時間 |
| `cron` | string | `kind=cron` 時必填，5 個欄位：`分 時 日 月 週`，例如 `0 9 * * 1` 代表每週一 09:00 |
| `timezone` | string | IANA 時區名稱，預設為 `DEFAULT_TIMEZONE` |
| `name` | string | 顯示名稱 |

文件和選項在建立排程時就會驗證。錯誤：`400`。

### Schedule 物件

```json
{
  "id": "…32 hex…",
  "name": "nozzle check",
  "kind": "cron",
  "run_at": null,
  "cron": "0 9 * * 1",
  "timezone": "Asia/Taipei",
  "enabled": true,
  "next_run_at": "2026-10-26T09:00:00+08:00",
  "filename": "testpage.pdf",
  "options": { "copies": 1, "media": null, "color_mode": null, "quality": null,
               "scaling": "fit", "sides": "one-sided", "pages": null, "dry_run": false },
  "last_run_at": "2026-10-19T09:00:00+08:00",
  "last_status": "submitted",
  "last_error": null,
  "last_job": "/jobs/a1b2c3d4e5f6",
  "created_at": "2026-10-01T12:00:00+08:00"
}
```

| 欄位 | 說明 |
|---|---|
| `last_status` | `submitted`（已送出 Job）、`failed`、`missed`（服務停機期間錯過，且延遲超過 1 小時）或 `null` |
| `last_job` | 最近一次送出的 Job 的路徑；服務重啟後這個 Job 可能已經不存在 |

排程每 30 秒檢查一次。一次性排程執行後會自動停用。

### `GET /print-schedule`

列出所有排程。

### `GET /print-schedule/{id}`

取得單一排程。找不到時回 `404`。

### `PATCH /print-schedule/{id}`

JSON body，兩個欄位都可省略：

```json
{ "enabled": false, "name": "新名稱" }
```

錯誤：`404`；`409` 表示一次性排程已經執行過，無法再啟用。

### `POST /print-schedule/{id}/run`

立刻列印一次，不影響原本的時間表。送出失敗時回 `400`，`detail` 是錯誤原因。

### `DELETE /print-schedule/{id}` → `204`

刪除排程和它保存的文件。

---

## 6. 掃描 Scanner & Scans

### `GET /scanner`

```json
{
  "state": "Idle",
  "adf_state": "ScannerAdfEmpty",
  "busy": false,
  "sources": {
    "flatbed": { "max_width": 2550, "max_height": 3508, "resolutions": [100, 200, 300, 600, 1200],
                 "color_modes": ["RGB24", "Grayscale8", "BlackAndWhite1"], "formats": ["application/pdf", "image/jpeg"] },
    "feeder":  { "max_width": 2550, "max_height": 4200, "resolutions": [100, 200, 300, 600], "...": "..." }
  }
}
```

| 欄位 | 說明 |
|---|---|
| `state` | eSCL 狀態：`Idle`、`Processing`、`Testing`、`Stopped`、`Down` |
| `adf_state` | 送紙器有紙時為 `ScannerAdfLoaded`，沒紙時為 `ScannerAdfEmpty` |
| `busy` | 這個服務是否正在掃描 |
| `max_width` / `max_height` | 單位是 1/300 英寸 |

錯誤：`502`。

### `POST /scan` → `202`

`multipart/form-data`，所有欄位皆可省略：

| 欄位 | 值 | 預設 |
|---|---|---|
| `source` | `flatbed` \| `feeder` | `flatbed` |
| `color_mode` | `color` \| `grayscale` \| `bw` | `color` |
| `resolution` | `100` \| `200` \| `300` \| `600` \| `1200`（`1200` 僅限 flatbed） | `300` |
| `format` | `pdf` \| `jpeg` | `pdf` |
| `size` | `a4` \| `letter` \| `legal` \| `max` | `a4` |

請求會立刻回傳 Scan 物件，`status` 為 `scanning`。

| 錯誤 | 原因 |
|---|---|
| `400` | 這個來源不支援指定的解析度、色彩或格式 |
| `409` | 已有掃描在進行、掃描器不是 Idle，或送紙器沒紙 |
| `502` | 連不到掃描器 |

### Scan 物件

```json
{
  "id": "…32 hex…",
  "status": "completed",
  "source": "flatbed", "color_mode": "color", "resolution": 300, "format": "pdf", "size": "a4",
  "filename": "scan-2026-10-21-1530.pdf",
  "media_type": "application/pdf",
  "pages": 1,
  "size_bytes": 523441,
  "error": null,
  "created_at": "2026-10-21T15:30:00+08:00",
  "finished_at": "2026-10-21T15:30:22+08:00",
  "expires_at": "2026-10-28T15:30:00+08:00",
  "download": "/scans/…/file"
}
```

| 欄位 | 說明 |
|---|---|
| `status` | `scanning`、`completed` 或 `failed` |
| `download` | 只有 `completed` 時才有值。這是相對於後端的路徑；經由代理存取時要加上 `/api` 前綴 |
| `expires_at` | 過了這個時間，掃描會被自動刪除（`SCAN_RETENTION_DAYS`） |

**流程**：`POST /scan` → 每 1–2 秒輪詢一次 `GET /scans/{id}`（通常需要 15–30 秒）→ `GET /scans/{id}/file`。

### `GET /scans`

列出所有掃描，最新的在前。

### `GET /scans/{id}`

取得單一掃描。找不到時回 `404`。

### `GET /scans/{id}/file`

下載掃描檔。`Content-Type` 為 `application/pdf`、`image/jpeg`，或 `application/zip`（送紙器多頁的 JPEG 會打包成 zip）。回應帶有 `Content-Disposition: attachment; filename=…` 和 `X-Scan-Pages`。

| 錯誤 | 原因 |
|---|---|
| `409` | 掃描還沒完成或已失敗 |
| `410` | 檔案已不存在 |

### `DELETE /scans/{id}` → `204`

刪除掃描紀錄和檔案。掃描還在進行時回 `409`。

---

## 7. 環境變數

| 變數 | 預設 | 說明 |
|---|---|---|
| `ACCESS_CODE` | 空（不需要登入） | 登入通行碼，建議使用約 6 個英文字母，不分大小寫 |
| `SESSION_DAYS` | `30` | 登入有效天數 |
| `ROOT_PATH` | 空 | 反向代理的路徑前綴（Compose 中為 `/api`） |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | uvicorn 信任哪些代理送來的 `X-Forwarded-For`；這會影響登入限流判斷的來源 IP |
| `PRINTER_HOST` | `EPSONE62DA3.local` | 印表機的 IP 或 mDNS 名稱 |
| `PRINTER_PORT` / `PRINTER_PATH` | `631` / `/ipp/print` | IPP |
| `SCANNER_PORT` / `SCANNER_PATH` | `443` / `/eSCL` | eSCL |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | 後端監聽位址 |
| `PDF_DPI` | `300` | PDF 點陣化解析度（`300` 或 `600`） |
| `JPEG_QUALITY` | `92` | |
| `MAX_UPLOAD_MB` | `100` | |
| `DATA_DIR` | `data`（Docker 中為 `/data`） | SQLite 和檔案的存放位置 |
| `DEFAULT_TIMEZONE` | `Asia/Taipei` | |
| `SCAN_RETENTION_DAYS` | `7` | |

## 8. 變更規範

- 新增 endpoint 時，要掛在 `app.py` 的 `api` router 上（預設需要登入）。只有確定要公開的 endpoint 才能掛在 `app` 上。
- 列舉值用 `Literal` 宣告，讓 `/openapi.json` 和 Swagger 能顯示所有可用的值。
- 錯誤一律用 `HTTPException(status, "英文訊息")`，狀態碼依第 1 節的表格選擇。
- 修改 API 時要同步更新本文件，以及前端的 [frontend/src/lib/api.ts](../frontend/src/lib/api.ts)。
