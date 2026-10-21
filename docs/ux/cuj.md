# HomePrinter 前端：Critical User Journeys 與畫面規格

> 對象：前端工程師（React + Tailwind + shadcn/ui）。主要裝置 iPhone Safari（含加入主畫面），桌機可用。介面語言繁體中文。
> 依據：`backend/README.md`、`backend/src/homeprinter/{app,jobs,schedules,scans,printer,scanner}.py`（2026-10-21 版本）。
> 更新：登入 API 已實作，規格見 [docs/api.md §2](../api.md#2-認證-auth)。與本文「假設」的差異：body 欄位是 `code`（不是 `passcode`）；`GET /auth/session` 一律回 200 `{auth_required, authenticated}`；`/docs` 維持公開。前端使用 hash 路由（`#/print`），所以沒有 `/login` 路由，未登入時直接顯示登入畫面。

---

## 0. 先讀：會影響 UI 的後端行為

| 行為 | 對 UI 的影響 |
|---|---|
| `POST /print` **不會**先連印表機，印表機離線也回 202 | 送出成功 ≠ 會印。工作頁要搭配 `GET /printer` 判斷「排隊中且印表機連不上」。後端會每 5 秒重試，最多 10 分鐘，之後才變 `failed` |
| 列印工作存在記憶體，服務重啟就消失 | `GET /jobs/{id}` 可能 404 → 顯示「找不到此工作（服務可能已重新啟動）」，不要當成一般錯誤 |
| 照片只能單面；`pages` 只適用 PDF | 選到照片時**隱藏**「雙面」和「頁碼範圍」，不是只停用，避免送出後收到 400 |
| PDF 的 `pages_total` 在 rendering 結束後會重算（雙面需要補空白背面，加上份數） | 進度條在 `rendering` 時用不定進度，到 `printing` 才顯示 `pages_done / pages_total` |
| 取消（`DELETE /jobs/{id}`）是非同步的：`printing` 中的工作會先回原狀態，之後才變 `canceled` | 按下取消後按鈕變「取消中…」，繼續輪詢直到結束狀態 |
| 掃描同時只能一個；印表機忙碌、送紙器沒紙也回 409 | 409 有 3 種原因，依 `detail` 文字分流（見 §3.7） |
| 平台掃描一次只掃一頁；送紙器可多頁（PDF 合併；JPEG 會變 zip） | 送紙器預設 PDF；選 JPEG 時提示「多頁會打包成 zip」 |
| 掃描檔保留 `SCAN_RETENTION_DAYS`（預設 7 天），過期刪除；下載過期檔回 410 | 列表顯示「N 天後刪除」 |
| 排程建立時就驗證檔案和選項 | 建立排程的 400 錯誤要像列印表單一樣顯示在欄位旁 |
| 後端錯誤 `detail` 是英文 | 前端要有錯誤訊息對照表（§3.7），未知錯誤顯示中文通用文字並可展開原文 |

**狀態中文對照（全站統一，做成共用 `<StatusBadge>`）**

| 類型 | 值 | 文字 | Badge 色調 |
|---|---|---|---|
| job | `queued` | 排隊中 | secondary（灰） |
| job | `rendering` | 處理檔案中 | secondary + spinner |
| job | `printing` | 列印中 | default（主色）+ spinner |
| job | `completed` | 已完成（`dry_run` 時為「試印完成」） | 綠 |
| job | `failed` | 失敗 | destructive |
| job | `canceled` | 已取消 | outline |
| scan | `scanning` | 掃描中 | 主色 + spinner |
| scan | `completed` | 完成 | 綠 |
| scan | `failed` | 失敗 | destructive |
| schedule `last_status` | `submitted` | 已送出列印 | 綠 |
| schedule `last_status` | `failed` | 失敗 | destructive |
| schedule `last_status` | `missed` | 已錯過（服務當時未運作） | 黃 |
| schedule `enabled` | `true` / `false` | 啟用中 / 已暫停 | — |
| printer `state` | `idle` / `processing` / `stopped` / `unknown` | 待機 / 工作中 / 已停止 / 未知 | 綠 / 主色 / 紅 / 灰 |

---

## 1. 資訊架構

### 1.1 路由與導覽

手機底部 Tab bar 放 **4 個**（不超過 5 個，每個都是高頻任務）：

| Tab | 路由 | 圖示（lucide） | 目的 |
|---|---|---|---|
| 首頁 | `/` | `Home` | 一眼看印表機是否可用、墨水量、進行中的工作；最近的列印/掃描紀錄 |
| 列印 | `/print` | `Printer` | 選檔案 → 設定 → 送出。主要任務 |
| 掃描 | `/scan` | `ScanLine` | 設定 → 掃描 → 下載/分享；下方是 7 天內的掃描紀錄 |
| 排程 | `/schedules` | `CalendarClock` | 排程列表、新增、暫停/恢復、立即列印 |

非 Tab 頁（push 進入，左上返回）：

| 路由 | 進入點 | 內容 |
|---|---|---|
| `/login` | 未登入或 401 | 通行碼登入 |
| `/jobs` | 首頁「列印紀錄 › 全部」 | 所有列印工作（記憶體內最多保留 100 筆已結束工作） |
| `/jobs/:id` | 送出列印後自動導向；列表點選 | 進度、取消、錯誤原因 |
| `/scans/:id` | 掃描開始後原地展開；列表點選 | 掃描進度、預覽、分享/下載/刪除 |
| `/schedules/new` | 排程頁右上「＋」 | 新增排程表單 |
| `/schedules/:id` | 點排程卡片 | 詳情、暫停/恢復、立即列印、刪除、上次結果 |

「設定」不做成 Tab：首頁右上角 `⋯` 開啟 Sheet，內含「登出」、印表機型號/位址、App 版本。

### 1.2 斷點

| 寬度 | 導覽 | 版面 |
|---|---|---|
| `< 768px`（手機） | 底部 Tab bar（固定） | 單欄；主要操作按鈕固定在 Tab bar 上方（sticky action bar） |
| `768–1023px`（iPad 直式） | 底部 Tab bar | 單欄，最大寬 `640px` 置中 |
| `≥ 1024px`（桌機） | 左側 Sidebar（寬 `240px`，shadcn `Sidebar`），底部顯示印表機狀態小卡；Tab bar 隱藏 | 列印頁兩欄（左：檔案與預覽；右：選項與送出）；首頁卡片 2–3 欄 grid；列表頁可用 Table |

### 1.3 各頁目的與內容摘要

- **首頁**：狀態卡（印表機 state + message + `state_reasons` 摘要）、墨水卡（各色長條）、進行中工作（輪詢）、最近 5 筆列印、最近 3 筆掃描、快捷按鈕「列印照片」「列印 PDF」「掃描」。
- **列印**：檔案選擇 → 檔案卡（縮圖/檔名/頁數）→ 選項（依檔案類型動態）→ 摘要（「共 N 頁，約用 M 張紙」）→ 送出。
- **掃描**：預設組合（文件 / 照片 / 自訂）→ 來源（平台 / 送紙器，附送紙器狀態）→ 進階選項 → 掃描按鈕 → 進行中卡片 → 結果卡片（分享/下載）→ 掃描紀錄。
- **排程**：卡片列表（名稱、下次執行時間、重複規則白話、上次結果、啟用 Switch）。

---

## 2. Critical User Journeys

每條 CUJ 的「點擊數」從 App 開啟（已登入）起算，不含系統檔案選擇器內的操作。

### CUJ-1 首次登入並加入主畫面

**情境**：家人第一次拿到網址（外網），要在 iPhone 上登入並把它放到主畫面當 App 用。

| # | 使用者動作 | 系統回應 | API |
|---|---|---|---|
| 1 | 開啟網址 | 未登入 → 導向 `/login`，通行碼欄位自動 focus（iOS 不會自動彈鍵盤，屬正常） | `GET /auth/session` → 401（假設） |
| 2 | 輸入通行碼（約 6 個英文字母），按鍵盤「前往」或「登入」 | 按鈕顯示 spinner、停用；成功後導向 `next` 參數或 `/`；iCloud 鑰匙圈詢問是否儲存密碼 | `POST /auth/login {passcode}` → 204 + `Set-Cookie`（假設） |
| 3 | 首頁首次顯示 | 在 Safari（非 standalone）時顯示一次性提示卡：「加入主畫面：點分享 → 加入主畫面」，可關閉，關閉後記在 `localStorage` | `GET /printer`、`GET /jobs`、`GET /scans` |
| 4 | 從主畫面開啟 | standalone 模式；**cookie 與 Safari 不共用**，需再登入一次（鑰匙圈可自動填入） | 同 1–2 |

**成功指標**：首次登入 ≤ 2 次點擊、≤ 20 秒；從主畫面再次開啟時不需重新登入（session cookie 需有 `Max-Age`，建議 30 天）。

**失敗點與 UI 應對**

| 失敗 | UI |
|---|---|
| 通行碼錯誤（401） | 欄位下方紅字「通行碼不正確」，清空欄位並保持 focus；不要說明是哪個字錯 |
| 嘗試過多（429，假設後端有限流） | 「嘗試次數過多，請 N 分鐘後再試」，按鈕停用並倒數（若回 `Retry-After`） |
| 網路斷線 | 「無法連線到伺服器，請確認網路」+「重試」 |
| 使用中 session 過期（任何 API 回 401） | 全域攔截 → 導向 `/login?next=<目前路徑>`，Toast「登入已過期，請重新登入」；列印表單若已選檔，盡量保留在記憶體（不保證） |

---

### CUJ-2 用手機印一張照片

**情境**：從相簿挑一張照片印出來（給長輩、貼冰箱、作業用）。

| # | 使用者動作 | 系統回應 | API |
|---|---|---|---|
| 1 | 首頁點「列印照片」或 Tab「列印」→「選擇照片」 | 開啟 iOS 選單（照片圖庫 / 拍照 / 選擇檔案） | — |
| 2 | 在相簿選 1 張 | 顯示照片縮圖卡；選項區切換為**照片模式**：隱藏雙面、頁碼範圍；套用照片預設（§5） | 首次進入列印頁時取 `GET /printer/capabilities`（快取於記憶體）、`GET /printer`（取 `media_ready`） |
| 3 | （可選）改紙張/份數 | 紙張改成 4x6 時，縮放自動改「填滿」，品質改「高」（使用者手動改過則不覆蓋） | — |
| 4 | 點「列印」 | 按鈕顯示上傳進度（`XMLHttpRequest.upload.onprogress`，HEIC 可能數 MB）；202 後導向 `/jobs/:id` | `POST /print`（multipart） |
| 5 | 看進度 | 每 2 秒輪詢；`printing` 顯示「列印中 0/1」；`completed` 顯示綠勾與「已完成，請到印表機取件」 | `GET /jobs/{id}` |

**成功指標**：4 次點擊內送出（列印 → 選擇照片 → 選照片 → 列印）；從開 App 到送出 ≤ 20 秒（不含上傳）；照片預設不需修改即可印出的比例 ≥ 80%。

**失敗點與 UI 應對**

| 失敗 | UI |
|---|---|
| 檔案太大（413） | 在檔案卡上顯示「檔案超過 100 MB」；前端送出前先比對大小（上限值可寫死或由後端提供） |
| 不支援的格式（400 `not a readable image or PDF`） | 檔案卡紅框「無法讀取這個檔案，請選擇 JPEG、PNG、HEIC 或 PDF」 |
| 圖片過大（400 `image is too large...`） | 「照片解析度太高，請先縮小或截圖後再印」 |
| 上傳中斷（網路） | 按鈕還原，Toast「上傳失敗」+「重試」；保留已選檔案 |
| 紙張與紙匣不符（`media_ready` 不含所選紙張） | 送出前在紙張欄位下方黃字提示「紙匣目前是 A4，請先換成 4x6 相片紙」，不擋送出 |
| 印表機離線 | 見 CUJ-7 |

**P1（非必要）**：支援一次選多張照片 → 依序呼叫多次 `POST /print`（每張一個 job），完成後導向 `/jobs` 而非單一工作頁。

---

### CUJ-3 印 PDF：雙面、指定頁

**情境**：手機收到一份 12 頁的 PDF（LINE/Email/檔案 App），只要印第 1–4 頁和第 7 頁，雙面、黑白。

| # | 使用者動作 | 系統回應 | API |
|---|---|---|---|
| 1 | 列印 Tab →「選擇 PDF」 | 開啟 iOS 檔案 App（`accept="application/pdf"`） | — |
| 2 | 選 PDF | 檔案卡顯示檔名、大小、頁數（前端以 pdf.js 動態載入讀頁數；讀不到就不顯示頁數）；選項切換為 **PDF 模式** | — |
| 3 | 「頁面」選「自訂範圍」，輸入 `1-4,7` | 即時驗證格式與是否超過總頁數；顯示「共 5 頁」 | — |
| 4 | 「雙面」選「長邊翻頁」 | 摘要更新「5 頁，雙面，約用 3 張紙」 | — |
| 5 | 「色彩」選「黑白」 | — | — |
| 6 | 點「列印」 | 上傳進度 → 導向 `/jobs/:id` | `POST /print`（`pages=1-4,7`、`sides=two-sided-long-edge`、`color_mode=monochrome`） |
| 7 | 看進度 | `rendering`：「處理檔案中…」不定進度；`printing`：`pages_done/pages_total` 進度條（`pages_total` 可能因補空白背面變成 6） | `GET /jobs/{id}` 每 2 秒 |

**成功指標**：≤ 7 次點擊；頁碼範圍輸入錯誤在送出前被攔截的比例 100%（有頁數時）；雙面方向選錯的回報為 0（靠選項說明）。

**選項文案**：
- 單面
- 雙面・長邊翻頁 —「一般直式文件，像書一樣左右翻」
- 雙面・短邊翻頁 —「橫式或上下翻的文件」

**頁碼輸入**：`<Input inputmode="text" placeholder="例：1-3,5">`；iOS 數字鍵盤沒有逗號與連字號，所以不用 `inputmode="numeric"`。

**失敗點與 UI 應對**

| 失敗 | UI |
|---|---|
| 頁碼格式錯（400 `bad page range`） | 欄位紅字「頁碼格式不正確，例：1-3,5」 |
| 超出頁數（400 `is outside 1-N`） | 「這份 PDF 只有 N 頁」 |
| PDF 有密碼（400 `password-protected PDFs...`） | 檔案卡紅字「不支援有密碼的 PDF，請先解除密碼」 |
| 印表機不支援某選項（`printer does not support...`） | 工作頁顯示失敗，建議「改用預設設定再試一次」按鈕（帶回列印頁、保留檔案、重設選項） |
| 長文件轉檔久 | `rendering` 超過 30 秒顯示補充文字「頁數較多，處理需要一點時間」 |

---

### CUJ-4 掃描文件並在手機上存檔／分享

**情境**：把一張紙本文件（或一疊放在送紙器）掃成 PDF，存到「檔案」或用 LINE 傳出去。

| # | 使用者動作 | 系統回應 | API |
|---|---|---|---|
| 1 | Tab「掃描」 | 顯示預設組合「文件」；來源區顯示平台/送紙器；送紙器顯示「已放紙／沒有紙」 | `GET /scanner`（取 `state`、`adf_state`、`busy`、`sources`） |
| 2 | 把文件放上玻璃面板（或送紙器），點「開始掃描」 | 按鈕變停用；202 後出現進行中卡片：「掃描中… 已經過 12 秒（約需 15–30 秒）」 | `POST /scan` |
| 3 | 等待 | 每 2 秒輪詢；完成時卡片變成結果卡（檔名、頁數、大小），**同時在背景預先下載檔案成 Blob** | `GET /scans/{id}` → `completed` 後 `GET /scans/{id}/file` |
| 4 | 點「分享／儲存」 | 呼叫 `navigator.share({ files: [file] })` 開啟 iOS 分享面板（儲存到檔案、LINE、AirDrop、郵件；JPEG 有「儲存影像」） | — |
| 5 | （需要多頁、平台）點「再掃一頁」 | 用同一組設定再掃（平台每次一頁；後端不會合併，會是另一個檔案） | `POST /scan` |

**成功指標**：從開 App 到分享面板出現 ≤ 3 次點擊（掃描 → 開始掃描 → 分享）；總時間 ≤ 45 秒（300 dpi）；分享在 iOS Safari 與主畫面模式都可用。

**失敗點與 UI 應對**

| 失敗 | UI |
|---|---|
| 掃描忙碌（409 `scanner is busy...`） | 見 CUJ-7 |
| 送紙器沒紙（409 `the document feeder is empty`） | 來源區顯示「送紙器沒有紙，請放好文件或改用平台」，一鍵「改用平台」 |
| 掃描失敗（`status: failed`） | 結果卡紅色，顯示中文化 `error`，「重試」用相同設定 |
| 服務重啟中斷（`interrupted by a service restart`） | 「掃描被中斷，請重新掃描」 |
| 超過 60 秒仍 `scanning` | 補充文字「比預期久，高解析度或多頁時可能需要數分鐘」；不自動放棄（後端每頁最多等 300 秒） |
| `navigator.share` 不支援或使用者取消 | 取消（`AbortError`）不顯示錯誤；不支援時退回「下載」連結 |
| 檔案已過期（410） | 「此掃描已超過保存期限並被刪除」並從列表移除 |

**預設組合**

| 組合 | source | color_mode | resolution | format | size | 用途 |
|---|---|---|---|---|---|---|
| 文件（預設） | 自動：送紙器有紙則 feeder，否則 flatbed | `grayscale` | 200 | pdf | a4 | 一般文件，檔案小、清楚 |
| 彩色文件 | 同上 | `color` | 300 | pdf | a4 | 有印章、彩色圖表 |
| 照片 | flatbed | `color` | 600 | jpeg | max | 翻拍相片 |
| 自訂 | 展開全部選項 | | | | | |

> 後端 API 本身的預設是 color/300/pdf/a4；前端「文件」組合刻意改灰階 200 dpi，檔案約小 3–5 倍，手機傳 LINE 較快。

---

### CUJ-5 設定每週噴頭檢查排程

**情境**：噴墨機太久不印會堵塞噴頭。想每週六早上自動印一張彩色測試頁。

| # | 使用者動作 | 系統回應 | API |
|---|---|---|---|
| 1 | Tab「排程」→ 右上「＋」 | 開啟 `/schedules/new`；頂部提供範本「每週噴頭保養」 | `GET /print-schedule`（列表） |
| 2 | 點範本「每週噴頭保養」 | 自動填入：名稱「噴頭保養」、重複「每週」、星期六、10:00、彩色、A4、1 份、品質一般；檔案欄位帶入前端內建的彩色測試頁 PDF（`/assets/nozzle-check.pdf`，以 `fetch` 取得 Blob 後放進 FormData） | — |
| 3 | （可選）改星期／時間 | 下方即時顯示白話：「每週六 10:00（台北時間）」與「下次執行：10/24（六）10:00」（前端自算，送出後以後端 `next_run_at` 為準） | — |
| 4 | 點「建立排程」 | 成功 → 回列表，新卡片高亮，Toast「已建立，下次列印：10/24（六）10:00」 | `POST /print-schedule`（`kind=cron`、`cron=0 10 * * 6`、`timezone=Asia/Taipei`、`name`、`file` + 列印選項） |
| 5 | （可選）點卡片 →「立即列印一次」測試 | Toast「已送出列印」＋連到工作頁 | `POST /print-schedule/{id}/run` → 讀 `last_job` 導向 `/jobs/:id` |

**重複規則 UI（不讓使用者寫 cron）**

| 選項 | 欄位 | 產生的 cron |
|---|---|---|
| 只一次 | 日期時間（`<input type="datetime-local">`） | `kind=once`、`run_at=2026-10-24T10:00`（不帶時區偏移，搭配 `timezone`） |
| 每天 | 時間 | `M H * * *` |
| 每週 | 星期（可複選，ToggleGroup 日一二三四五六）＋時間 | `M H * * 6` 或 `M H * * 1,3,5` |
| 每月 | 日期（1–28）＋時間 | `M H D * *` |
| 進階 | cron 文字框（5 欄位） | 原樣送出；後端驗證 |

`timezone` 送 `Intl.DateTimeFormat().resolvedOptions().timeZone`；取不到時不送（後端預設 `Asia/Taipei`）。

**排程卡片內容**：名稱、白話規則、`next_run_at`（「下次：10/24（六）10:00」；暫停時顯示「已暫停」）、上次結果（`last_status` badge + `last_run_at` 相對時間）、啟用 Switch。

**成功指標**：用範本建立 ≤ 3 次點擊（＋ → 範本 → 建立）、≤ 30 秒；使用者不需要知道 cron。

**失敗點與 UI 應對**

| 失敗 | UI |
|---|---|
| `run_at is in the past`（400） | 日期欄位紅字「時間已經過了」；前端送出前先檢查 |
| cron 不合法（400，只在進階模式會發生） | 欄位紅字「格式不正確，例：0 9 * * 1（每週一 9:00）」 |
| 檔案/選項不合法（400，與 `/print` 相同） | 同 CUJ-2/3 的欄位錯誤 |
| 恢復已過期的一次性排程（PATCH 409） | Switch 彈回關閉，Toast「這個一次性排程已執行或時間已過，請新增一個」 |
| `last_status: missed` | 卡片黃色 badge「已錯過」＋說明「當時服務未運作」 |
| `last_status: failed` | 卡片紅色 badge，詳情頁顯示 `last_error` |
| `last_job` 連結 404（服務重啟） | 詳情頁顯示「工作紀錄已不存在」，不顯示錯誤頁 |
| 刪除排程 | AlertDialog 確認「刪除排程與其檔案？此動作無法復原」→ `DELETE /print-schedule/{id}` |

> 想試排程又不想浪費紙：表單「進階」內有「試印（不出紙）」開關（`dry_run=true`）。

---

### CUJ-6 查看墨水量、列印進度與取消

**情境**：送出一份 30 頁文件後發現印錯檔，要取消；或順便看墨水夠不夠。

| # | 使用者動作 | 系統回應 | API |
|---|---|---|---|
| 1 | 開啟首頁 | 狀態卡、墨水卡、進行中工作卡（有非結束狀態的 job 時才出現） | `GET /printer`、`GET /jobs` |
| 2 | 看墨水 | 每色一條水平長條（顏色取 `ink[].color`），右側百分比；≤ 15% 顯示黃色「偏低」，≤ 5% 紅色「請補充」；`level < 0` 顯示「未知」 | — |
| 3 | 點進行中工作 | `/jobs/:id`：檔名、狀態、進度條、選項摘要（份數、單/雙面、頁碼）、建立時間 | `GET /jobs/{id}` 每 2 秒 |
| 4 | 點「取消列印」 | AlertDialog「取消這份列印？已送到印表機的頁面可能仍會印出」→ 確認後按鈕變「取消中…」 | `DELETE /jobs/{id}` |
| 5 | 等待 | 持續輪詢直到 `canceled`（或剛好 `completed`）；顯示最終結果 | `GET /jobs/{id}` |

**輪詢規則**
- 工作頁：`queued/rendering/printing` 時每 2 秒；到結束狀態停止。
- 首頁：有進行中工作時 `GET /jobs` 每 3 秒；否則每 30 秒。`GET /printer` 每 15 秒。
- 頁面不可見（`document.visibilityState === 'hidden'`）時全部暫停；回到前景立即刷新一次（standalone 模式沒有下拉重新整理）。
- 建議用 TanStack Query 的 `refetchInterval`（以回傳狀態決定間隔）。

**成功指標**：從開 App 到看到墨水量 0 次點擊；取消 ≤ 3 次點擊（工作卡 → 取消 → 確認）；狀態更新延遲 ≤ 3 秒。

**失敗點與 UI 應對**

| 失敗 | UI |
|---|---|
| 取消時已完成 | 回傳 `completed`，顯示「已印完，無法取消」 |
| 工作 404 | 「找不到此工作（服務可能已重新啟動）」＋「回首頁」 |
| `GET /printer` 502 | 墨水卡顯示「無法取得墨水量」，狀態卡顯示離線（CUJ-7） |
| `failed` | 紅色區塊顯示中文化 `error`，按鈕「重新列印」（回列印頁需重新選檔，因後端已清掉檔案資料） |

---

### CUJ-7 處理錯誤：印表機離線、掃描忙碌、卡紙缺紙

**情境**：印表機沒開機/斷 Wi-Fi，或別人正在掃描，或缺紙。使用者要知道「發生什麼事、我該做什麼、要不要等」。

| # | 情況 | 系統偵測 | UI 回應 |
|---|---|---|---|
| 1 | 印表機離線 | `GET /printer` → 502（`cannot talk to printer` / `cannot resolve printer host`） | 首頁狀態卡紅色：「印表機目前離線 — 請確認印表機已開機並連上 Wi-Fi」＋「重新檢查」；列印頁頂部顯示同樣的 Alert，**仍允許送出**（後端會自動重試 10 分鐘） |
| 2 | 離線時送出列印 | job 停在 `queued` 超過 10 秒且 `GET /printer` 失敗 | 工作頁黃色提示：「等待印表機連線，系統會自動重試最多 10 分鐘」；仍可取消 |
| 3 | 重試逾時 | job → `failed`，`error` 含 `cannot talk to printer` | 「印表機一直沒有回應，請開機後重新列印」 |
| 4 | 掃描忙碌 | `POST /scan` 409 `scanner is busy...` 或 `GET /scanner` 的 `busy: true` | 掃描按鈕停用並顯示「另一個掃描正在進行，完成後即可使用」；若該掃描在紀錄中，提供「查看進行中的掃描」；每 5 秒重查 `GET /scanner`，可用時自動恢復按鈕 |
| 5 | 掃描器非待機 | 409 `scanner is Processing` 等 | 「印表機正在忙（可能正在列印），請稍後再試」＋「重試」 |
| 6 | 缺紙／卡紙／蓋子開啟 | `GET /printer` 的 `state_reasons` | 依對照表顯示（見下），首頁狀態卡變黃/紅 |
| 7 | 伺服器整體掛掉 | fetch 失敗 / 5xx（非 502） | 全頁錯誤狀態：「無法連線到 HomePrinter 服務」＋「重試」 |

**`state_reasons` 對照（只列常見，其餘顯示原字串；`none` 不顯示）**

| reason（去掉 `-error`/`-warning`/`-report` 後綴比對） | 文字 | 等級 |
|---|---|---|
| `media-empty`, `media-needed` | 紙匣沒有紙 | 錯誤 |
| `media-jam` | 卡紙，請依印表機面板指示排除 | 錯誤 |
| `door-open`, `cover-open` | 印表機蓋子未關 | 錯誤 |
| `marker-supply-low` | 墨水偏低 | 警告 |
| `marker-supply-empty` | 墨水用盡 | 錯誤 |
| `marker-waste-almost-full` / `marker-waste-full` | 廢墨收集盒將滿 / 已滿 | 警告 / 錯誤 |
| `offline`, `shutdown` | 印表機離線 | 錯誤 |
| `paused` | 印表機已暫停 | 警告 |

**成功指標**：每個錯誤狀態都有「原因 + 下一步」兩部分；離線時使用者不需離開 App 就能重試；客訴「不知道有沒有印」為 0。

---

## 3. 畫面狀態清單

共通規則：
- **載入中**用 shadcn `Skeleton`，保持最終版面形狀，不用全頁 spinner。首次載入 > 300ms 才顯示骨架，避免閃爍。
- **錯誤**分三層：欄位錯誤（欄位下方紅字）、區塊錯誤（卡片內 `Alert`）、全頁錯誤（置中插圖 + 說明 + 「重試」）。
- **成功**用 `Toast`（sonner），位置手機在頂部（避開底部 Tab 與 home indicator），桌機右下。
- **進行中**一律可見地顯示「上次更新 / 已經過時間」，讓使用者知道輪詢仍在運作。

### 3.1 登入 `/login`

| 狀態 | 呈現 |
|---|---|
| 預設 | Logo、標題「HomePrinter」、通行碼欄位、「登入」按鈕（欄位空時停用） |
| 送出中 | 按鈕 spinner +「登入中…」，欄位唯讀 |
| 錯誤 | 欄位下紅字（401 / 429 / 網路，見 CUJ-1） |
| 成功 | 直接導向，不另顯示 |

### 3.2 首頁 `/`

| 區塊 | 空狀態 | 載入中 | 錯誤 | 成功/一般 | 進行中（輪詢） |
|---|---|---|---|---|---|
| 印表機狀態卡 | — | 骨架 | 離線紅卡 +「重新檢查」 | 綠點「待機」+ 型號 + `message` | `processing`：主色「工作中」 |
| 墨水卡 | `ink` 為空：「印表機未回報墨水量」 | 4 條骨架 | 「無法取得墨水量」 | 各色長條 + 百分比 | 每 15 秒更新 |
| 進行中工作 | 不顯示此區塊 | — | — | — | 卡片：檔名、`StatusBadge`、進度條，點擊進詳情 |
| 最近列印 | 「還沒有列印紀錄」+「列印第一份文件」按鈕 | 3 列骨架 | 區塊 Alert +「重試」 | 最多 5 筆 +「全部」 | — |
| 最近掃描 | 「還沒有掃描紀錄」 | 骨架 | 區塊 Alert | 最多 3 筆（點擊進 `/scans/:id`） | `scanning` 項目帶 spinner |

### 3.3 列印 `/print`

| 狀態 | 呈現 |
|---|---|
| 空（未選檔） | 兩個大按鈕卡：「選擇照片」（`Image` 圖示，說明：JPEG、PNG、HEIC）、「選擇 PDF」（`FileText`，說明：可雙面、選頁）；桌機另支援拖放區 |
| 載入選項 | `capabilities` 未回來時，選項區骨架；**檔案仍可先選** |
| 選項載入失敗（502） | 以內建預設選項（A4/Letter/4x6、彩色/黑白、一般/高）繼續，頂部 Alert「無法連線印表機，選項可能不完整」 |
| 已選照片 | 縮圖（`URL.createObjectURL`，讀不出縮圖則顯示檔案圖示）、檔名、大小、「更換」；選項：紙張、縮放、份數、色彩、品質；進階：試印 |
| 已選 PDF | 檔案圖示（P1：第一頁縮圖）、檔名、頁數；選項：頁面（全部/自訂）、雙面、份數、紙張、色彩、品質、縮放；進階：試印 |
| 欄位錯誤 | 欄位下紅字；送出按鈕停用直到修正 |
| 上傳中 | sticky 送出按鈕變進度條「上傳中 45%」，可「取消上傳」（`xhr.abort()`）；選項唯讀 |
| 送出失敗 | 400 → 對應欄位或檔案卡；413 → 檔案卡；其他 → Toast + 重試 |
| 成功 | 導向 `/jobs/:id`，Toast「已送出列印」 |

摘要列（送出按鈕上方，一行）：「彩色 · A4 · 單面 · 1 份」，PDF 加「5 頁 / 3 張紙」。

### 3.4 工作 `/jobs`、`/jobs/:id`

| 狀態 | `/jobs` | `/jobs/:id` |
|---|---|---|
| 空 | 「還沒有列印紀錄」+ 前往列印 | — |
| 載入中 | 列表骨架 | 卡片骨架 |
| 404 | — | 「找不到此工作（服務可能已重新啟動）」 |
| 錯誤 | 全頁錯誤 + 重試 | 區塊 Alert，保留最後一次成功資料並標示「資料可能過時」 |
| 進行中 | 列中顯示 badge + 小進度條 | `queued`：「排隊中」（前面還有工作時顯示「前面還有 N 份」，由 `GET /jobs` 計算）；`rendering`：不定進度；`printing`：`pages_done/pages_total`；取消按鈕 |
| 取消中 | — | 按鈕「取消中…」停用 |
| 完成 | 綠 badge | 綠勾大圖示 +「已完成」/「試印完成（未出紙）」、完成時間 |
| 失敗 | 紅 badge | 紅色 Alert + 中文化錯誤 + 「重新列印」 |
| 已取消 | outline badge | 「已取消」+ 已印頁數（若 > 0） |

### 3.5 掃描 `/scan`、`/scans/:id`

| 狀態 | 呈現 |
|---|---|
| 載入中 | 來源區骨架；「開始掃描」按鈕停用 |
| 掃描器離線（`GET /scanner` 502/5xx） | 頂部紅 Alert「無法連線掃描器」+ 重試；按鈕停用 |
| 忙碌（`busy: true`） | 按鈕停用 + 說明（CUJ-7 #4） |
| 預設 | 組合 ToggleGroup、來源（平台 / 送紙器 + 狀態文字）、進階（色彩、解析度、格式、大小）、「開始掃描」 |
| 選項限制 | 解析度依 `sources[source].resolutions` 動態列出；送紙器不顯示 1200；送紙器 + JPEG 顯示「多頁會打包成 zip」 |
| 進行中 | 卡片：spinner、「掃描中… 已經過 N 秒（約需 15–30 秒）」、所選設定摘要；**沒有取消按鈕**（後端不支援取消掃描） |
| 完成 | 結果卡：JPEG 顯示縮圖、PDF 顯示圖示＋頁數、檔名、大小、「N 天後刪除」；按鈕「分享／儲存」（主要）、「下載」、「再掃一頁」、「刪除」 |
| 失敗 | 紅卡 + 中文化錯誤 + 「重試」 |
| 掃描紀錄空 | 「最近 7 天沒有掃描紀錄」 |
| 掃描紀錄 | 列表：時間、頁數、大小、badge；左滑或 `⋯` 選單刪除（AlertDialog 確認） |

### 3.6 排程 `/schedules`、`/schedules/new`、`/schedules/:id`

| 狀態 | 呈現 |
|---|---|
| 空 | 插圖 +「還沒有排程」+ 說明「噴墨印表機久未使用容易堵塞，建議設定每週保養」+「建立每週噴頭保養」按鈕（直接帶範本） |
| 載入中 | 卡片骨架 ×2 |
| 錯誤 | 全頁錯誤 + 重試 |
| 一般 | 卡片列表；暫停的卡片降低不透明度 |
| Switch 切換中 | Switch 立即切換（樂觀更新），失敗時彈回並 Toast |
| 新增：送出中 | 「建立中…」（含檔案上傳） |
| 新增：錯誤 | 欄位錯誤（CUJ-5） |
| 詳情 | 規則、下次執行、時區、檔名、列印選項、上次結果（含 `last_error`、`last_job` 連結）；按鈕「立即列印一次」「暫停/恢復」「刪除」 |
| 一次性已執行 | `enabled=false` 且 `next_run_at=null` 且有 `last_run_at`：badge「已完成」，Switch 隱藏 |

### 3.7 錯誤訊息對照表（`detail` 子字串 → 中文）

建議實作 `translateError(detail: string): string`，以 `includes`/正規式比對，順序由具體到一般。

| `detail` 包含 | 中文 |
|---|---|
| `two-sided printing needs a PDF` | 照片只能單面列印 |
| `'pages' only applies to PDFs` | 只有 PDF 可以指定頁碼 |
| `empty file` | 檔案是空的 |
| `not a readable image or PDF` | 無法讀取這個檔案，請選擇 JPEG、PNG、HEIC 或 PDF |
| `not a readable PDF` | PDF 檔案損毀或無法讀取 |
| `password-protected PDFs` | 不支援有密碼的 PDF |
| `image is too large` | 照片解析度太高 |
| `bad page range` | 頁碼格式不正確，例：1-3,5 |
| `is outside 1-` | 頁碼超出範圍（PDF 只有 N 頁，N 從訊息擷取） |
| `unknown paper size` | 不支援這個紙張大小 |
| `larger than` | 檔案太大 |
| `cannot talk to printer` / `cannot resolve printer host` | 無法連線到印表機 |
| `printer is busy` | 印表機忙碌中 |
| `printer is not accepting jobs` | 印表機目前不接受工作 |
| `does not support` | 印表機不支援所選的設定 |
| `scanner is busy` | 另一個掃描正在進行 |
| `the document feeder is empty` | 送紙器沒有紙 |
| `scanner is ` + 其他狀態 | 掃描器忙碌中，請稍後再試 |
| `cannot talk to scanner` / `cannot resolve scanner host` | 無法連線到掃描器 |
| `the scan produced no pages` | 沒有掃到任何頁面，請確認文件放置正確 |
| `interrupted by a service restart` | 掃描被中斷，請重新掃描 |
| `run_at is in the past` | 時間已經過了 |
| `invalid cron expression` | 重複規則格式不正確 |
| `already ran or its time has passed` | 這個一次性排程已執行或時間已過 |
| 其他 | 發生錯誤，請再試一次（可展開「詳細資訊」看原文） |

---

## 4. iPhone 專屬注意事項

### 4.1 Viewport 與 safe-area

```html
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
```

- 底部 Tab bar：`h-[calc(49px+env(safe-area-inset-bottom))] pb-[env(safe-area-inset-bottom)]`，圖示 + 文字區固定 49px，home indicator 區域只放背景色。
- 主內容：`pb-[calc(49px+env(safe-area-inset-bottom)+16px)]`；若有 sticky 送出列，再加上其高度。
- sticky 送出列放在 Tab bar **上方**（`bottom: calc(49px + env(safe-area-inset-bottom))`），不要讓主要按鈕落在 home indicator 的手勢區。
- 頂部 header：`pt-[env(safe-area-inset-top)]`（standalone + `black-translucent` 時內容會延伸到狀態列下）。
- 左右：橫向時 `px-[max(16px,env(safe-area-inset-left))]`。
- 高度用 `100dvh` / `min-h-dvh`，不要用 `100vh`（Safari 網址列伸縮會跳動）。
- 鍵盤開啟時（`visualViewport` 高度明顯小於 `window.innerHeight`）隱藏 Tab bar 與 sticky 列，避免它們浮在鍵盤上方遮住欄位。

### 4.2 觸控目標與元件調整

- 所有可點元素 ≥ 44×44px。shadcn `Button` 預設 `h-9`/`h-10` 不夠：手機上一律 `h-11`，主要按鈕 `h-12 w-full`。
- `Switch`、`Checkbox`、`RadioGroupItem` 本體小：整列包在 `<label>` 裡，列高 ≥ 44px，點整列可切換。
- 2–4 個選項（單/雙面、色彩、品質、縮放、平台/送紙器）用 `ToggleGroup` 分段控制，不要用下拉。
- 長選項清單（紙張大小）：手機用原生 `<select>`（iOS 滾輪選擇器，最順手），桌機才用 shadcn `Select`。
- 手機上 Dialog 改用 `Drawer`（vaul）從底部滑出；`AlertDialog` 保持置中。
- 不依賴 hover/tooltip 傳達必要資訊；說明文字直接放在選項下方（`text-sm text-muted-foreground`）。
- 移除點擊灰框：`-webkit-tap-highlight-color: transparent`，但保留 `:active` 視覺回饋與 `focus-visible` 樣式。

### 4.3 輸入框

- 所有 `input`/`select`/`textarea` 字級 **≥ 16px**（Tailwind `text-base`），否則 Safari focus 時會自動放大頁面。shadcn `Input` 預設 `text-sm`（部分版本 `md:text-sm`），要改成 `text-base`，桌機可 `md:text-sm`。
- 不要用 `maximum-scale=1` 來擋縮放（無障礙問題）。
- 通行碼欄位：

```html
<input type="password" name="password" autocomplete="current-password"
  autocapitalize="none" autocorrect="off" spellcheck="false"
  enterkeyhint="go" class="text-base">
<!-- 讓 iCloud 鑰匙圈能正確儲存：放一個隱藏的帳號欄位 -->
<input type="text" name="username" autocomplete="username" value="homeprinter" hidden>
```

  提供「顯示」眼睛按鈕（44px）。建議後端比對時不分大小寫，降低輸入失敗率。
- 份數：`<Input type="number" inputmode="numeric" min=1 max=99>` 搭配左右 `−`/`＋` 按鈕（44px），手機上多半只按加減。
- 頁碼：`inputmode="text"`（需要逗號與連字號）。
- 排程時間：原生 `<input type="time">`、`<input type="datetime-local">`（iOS 原生滾輪）；Safari 不一定擋 `min`，前端自行驗證未來時間。

### 4.4 檔案選擇

| 按鈕 | 寫法 | iOS 行為 |
|---|---|---|
| 選擇照片 | `<input type="file" accept="image/*">`（**不要**加 `capture`） | 跳出選單：照片圖庫 / 拍照 / 選擇檔案 |
| 拍照列印（可選） | `accept="image/*" capture="environment"` | 直接開相機 |
| 選擇 PDF | `accept="application/pdf,.pdf"` | 開啟「檔案」App |

- iOS 可能把 HEIC 自動轉成 JPEG 上傳，也可能保留 HEIC；後端兩種都能處理，前端不需要轉檔。
- 縮圖：Safari 可直接顯示 HEIC 的 Object URL；`<img onError>` 時改顯示檔案圖示（桌機 Chrome 看不到 HEIC 是正常的）。
- `<input>` 用視覺隱藏（`sr-only`），由大按鈕卡 `<label htmlFor>` 觸發，確保點擊區夠大且在使用者手勢內觸發。
- 同一個檔案重選不會觸發 `change`：每次選完把 `input.value = ''`。
- iOS Safari 不支援 Web Share Target，**無法**從其他 App「分享到 HomePrinter」；使用者只能從 App 內選檔。文件中提示「從 LINE 收到的 PDF，先『儲存到檔案』再到這裡選」。

### 4.5 下載／分享掃描檔

iOS Safari 對下載的支援有限，主要路徑用 Web Share API：

1. 掃描 `completed` 後**立即**在背景 `fetch('/scans/{id}/file', { credentials: 'include' })` 轉成 `Blob`，包成 `new File([blob], filename, { type: media_type })`。檔名取 `GET /scans/{id}` 的 `filename`（`scan-20261021-153000.pdf`）。
2. 使用者點「分享／儲存」時，**同步**呼叫 `navigator.share({ files: [file] })`。`share()` 必須在使用者手勢內呼叫；若先 `await fetch` 再 share，大檔案常因手勢過期丟出 `NotAllowedError`，所以一定要預先下載。
3. 呼叫前檢查 `navigator.canShare?.({ files: [file] })`；不支援時退回下載連結。
4. `AbortError`（使用者關掉分享面板）不算錯誤，不顯示任何訊息。
5. 退回方案：`<a href="/scans/{id}/file" download={filename}>`。iOS Safari 會詢問是否下載，存到「檔案 › 下載項目」。注意：主畫面（standalone）模式下載體驗較差（可能在 App 內開預覽且沒有明顯返回），所以 standalone 時優先 share、隱藏「下載」按鈕或改為「在新分頁開啟」。
6. zip（送紙器 + JPEG 多頁）：分享到「檔案」後可在檔案 App 內點開解壓；UI 提示一句即可。

### 4.6 加入主畫面（PWA）

```html
<link rel="manifest" href="/manifest.webmanifest">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="印表機">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="theme-color" content="#ffffff" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#0a0a0a" media="(prefers-color-scheme: dark)">
<link rel="apple-touch-icon" href="/apple-touch-icon.png"> <!-- 180×180，不透明背景，不要自帶圓角 -->
```

`manifest.webmanifest`：`name: "HomePrinter"`、`short_name: "印表機"`、`display: "standalone"`、`start_url: "/"`、`scope: "/"`、`lang: "zh-Hant-TW"`、icons 192/512（含 `maskable`）。

注意事項：
- **主畫面 App 的 cookie 與 Safari 分開**：加入主畫面後需再登入一次。session cookie 必須有 `Max-Age`/`Expires`（建議 30 天、每次使用時延長），否則關閉 App 就登出。cookie 屬性：`HttpOnly; Secure; SameSite=Lax; Path=/`。
- standalone 沒有網址列、沒有上一頁按鈕、沒有下拉重新整理：每個非 Tab 頁都要有返回按鈕；資料頁要自動輪詢或提供重新整理按鈕。
- 外部連結（例如 API 文件）在 standalone 中會開到 Safari，必要時加 `target="_blank"`。
- 不需要 Service Worker 離線快取（無網路時本來就無法列印）；若要做，只快取靜態資源，**絕不**快取 API 回應與掃描檔。
- 偵測 standalone：`window.matchMedia('(display-mode: standalone)').matches || navigator.standalone`，用來決定是否顯示「加入主畫面」提示卡。

### 4.7 其他

- 上傳進度：`fetch` 沒有上傳進度事件，`POST /print`、`POST /print-schedule` 用 `XMLHttpRequest`（或 axios）以 `upload.onprogress` 顯示。
- iPhone 照片常 3–8 MB，行動網路上傳可能數十秒：上傳期間避免畫面被鎖定，上傳中離開頁面時用 `beforeunload` 提醒（standalone 無效，至少在 UI 內擋 Tab 切換前確認）。
- 深色模式：跟隨系統（shadcn 的 CSS 變數 + `prefers-color-scheme`），`html` 與 `body` 都要設背景色，避免 safe-area 區出現白邊。
- 時間顯示：後端回傳帶偏移的 ISO 字串（排程/掃描），用 `Intl.DateTimeFormat('zh-TW', ...)` 顯示；job 的 `created_at`/`finished_at` 是 Unix 秒數，要 `* 1000`。相對時間用 `Intl.RelativeTimeFormat('zh-TW')`（「3 分鐘前」）。

---

## 5. 預設值建議

### 5.1 照片：預設「依紙匣目前的紙」，不寫死

L6490 只有一個前方紙匣，家用情境絕大多數時間放的是 **A4 普通紙**；4x6 相片紙是偶爾才換上去的。若照片預設 4x6，紙匣裡是 A4 時，印表機會報紙張不符或印在 A4 角落，第一次使用就失敗。

| 條件 | media | scaling | quality | color_mode |
|---|---|---|---|---|
| `GET /printer` 的 `media_ready` 是 4x6 / 相片類尺寸 | `na_index-4x6_4x6in` | `fill`（滿版，相片紙裁切少量邊緣可接受） | `high` | `color` |
| `media_ready` 是 A4 或取不到（**預設情況**） | `iso_a4_210x297mm` | `fit`（整張照片完整印出、不裁切） | `high` | `color` |

- 提供一個明顯的快捷 Chip「4x6 相片紙（滿版）」，一鍵切成上表第一列。
- 使用者手動切到 4x6 時，自動把縮放改為 `fill`；切回 A4 時改回 `fit`（除非使用者手動改過縮放）。
- `none`（原尺寸）收在「縮放」選項最後，說明「依照片原始尺寸，可能被裁切或很小」。

### 5.2 PDF

| 欄位 | 預設 | 理由 |
|---|---|---|
| 紙張 | `media_ready`，否則 A4 | 台灣家用文件以 A4 為主 |
| 縮放 | `fit` | 不切到邊緣內容；與後端預設一致 |
| 雙面 | 單面 | 雙面較慢，且單頁文件雙面沒意義；但**記住上一次選擇**（localStorage） |
| 頁面 | 全部 | — |
| 色彩 | `auto`（介面顯示「自動」） | 交給印表機判斷；想省彩色墨水時手動選「黑白」 |
| 品質 | `normal` | 文件不需要高品質，速度快、省墨 |
| 份數 | 1 | — |
| 試印 | 關（收在「進階」） | 一般使用者不需要；給測試排程用 |

記憶規則：只記「雙面」「色彩」兩項偏好（最常被重複修改），其他每次回到預設，避免上次印照片的設定污染下次印文件。

### 5.3 掃描

見 CUJ-4 預設組合：「文件」= 灰階、200 dpi、PDF、A4；來源自動選擇（送紙器有紙用送紙器，否則平台）。理由：家用掃描多為單據、合約、作業，要傳 LINE/Email，檔案小比極致清晰重要；需要彩色時一鍵切「彩色文件」。

### 5.4 排程

- 新增排程頁頂部範本「每週噴頭保養」：每週六 10:00、彩色、A4、普通品質、1 份、內建彩色測試頁。
  - 選週末上午：家裡通常有人，可以順便看測試頁是否有斷線；紙匣普遍是 A4。
  - 必須彩色：要讓四色墨水都流過噴頭，黑白測試頁無法防止彩色噴頭乾掉。
- 新增一般排程時，預設「只一次」、時間為「明天 08:00」（最常見用途是隔天早上要用的通知單/作業）。
- 時區預設瀏覽器時區，畫面上顯示「（台北時間）」讓使用者確認。

### 5.5 輪詢間隔總表

| 對象 | 條件 | 間隔 |
|---|---|---|
| `GET /jobs/{id}` | 非結束狀態 | 2 秒 |
| `GET /jobs`（首頁） | 有進行中工作 / 無 | 3 秒 / 30 秒 |
| `GET /scans/{id}` | `scanning` | 2 秒 |
| `GET /scanner` | 掃描頁顯示中且 `busy` / 一般 | 5 秒 / 不輪詢（進頁面、送出前取一次） |
| `GET /printer` | 首頁或列印頁顯示中 | 15 秒 |
| `GET /print-schedule` | 排程頁 | 不輪詢（進頁面、操作後重抓） |
| 所有 | 頁面隱藏 | 暫停；回前景立即刷新 |

---

## 附錄：登入 API 假設（待後端定案）

| 端點 | 用途 | 回應 |
|---|---|---|
| `POST /auth/login` `{ "passcode": "abcdef" }` | 登入 | 204 + `Set-Cookie`；401 錯誤；429 限流（含 `Retry-After`） |
| `POST /auth/logout` | 登出 | 204，清 cookie |
| `GET /auth/session` | 啟動時檢查是否已登入 | 200 / 401 |

其他所有 API 未登入時回 401，前端全域攔截導向 `/login?next=...`。`next` 只接受站內相對路徑（以 `/` 開頭且不是 `//`），避免開放重導向。
