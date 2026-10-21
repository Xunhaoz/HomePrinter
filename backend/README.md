# HomePrinter backend

FastAPI service that prints PDFs and photos on the EPSON L6490 over IPP (pyipp), and scans
with its built-in scanner over eSCL (AirScan), without CUPS or SANE.

The printer accepts JPEG, PWG raster and URF, but not PDF, so:
- photos (JPEG, PNG, HEIC, ...) are converted to JPEG, with EXIF rotation applied, and printed one-sided;
- PDFs are rendered with PyMuPDF and encoded by [pwg.py](src/homeprinter/pwg.py) into one
  multi-page PWG raster job (PWG 5102.4). Pages are fitted to the paper, landscape pages are
  turned, and back sides are pre-rotated as the printer's `pwg-raster-document-sheet-back`
  asks, which is what makes duplex work. The printer only allows 1 copy per PWG job, so
  copies are repeated in the document (padded with a blank back when duplex needs it).

## Run

```bash
uv run homeprinter            # listens on 0.0.0.0:8000, API docs at /docs
uv run pytest                 # encoder, layout, schedule and storage tests
```

| env | default | |
|---|---|---|
| `PRINTER_HOST` | `EPSONE62DA3.local` | mDNS name or IP; names resolve to IPv4 |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | where the API listens |
| `SCANNER_PORT` / `SCANNER_PATH` | `443` / `/eSCL` | eSCL endpoint, HTTPS |
| `PDF_DPI` | `300` | PDF raster resolution (the printer takes 300 or 600) |
| `JPEG_QUALITY` | `92` | |
| `MAX_UPLOAD_MB` | `100` | |
| `DATA_DIR` | `data` (`/data` in Docker) | SQLite database and stored files |
| `DEFAULT_TIMEZONE` | `Asia/Taipei` | for schedules that don't name one, and scan file names |
| `SCAN_RETENTION_DAYS` | `7` | scans are deleted after this |
| `ACCESS_CODE` | empty (no login) | login code, see below |
| `SESSION_DAYS` | `30` | how long a login lasts |
| `ROOT_PATH` | empty | path prefix when served behind a proxy, e.g. `/api` |

### Login

With `ACCESS_CODE` set (a few letters, case-insensitive), every endpoint except `/auth/*` and
`/health` needs the session cookie from `POST /auth/login {"code": "..."}`. Wrong codes are
rate-limited (5 per client, 30 in total, per 15 minutes). Without it the API is open, as before;
that's only fine on a trusted LAN. `ROOT_PATH=/api` makes `/docs` work behind the web container's
proxy, and `SESSION_DAYS` (default 30) sets how long a login lasts.

```bash
curl -c cookies -H 'content-type: application/json' -d '{"code":"kitten"}' localhost:8000/auth/login
curl -b cookies localhost:8000/printer
```

### Docker

The [compose file](../compose.yaml) in the repository root runs this service next to the web app;
see the [root README](../README.md).

## API

The full specification, with response shapes and error codes, is in [docs/api.md](../docs/api.md).

| | |
|---|---|
| `GET /printer` | state and ink levels |
| `GET /printer/capabilities` | media, color modes, qualities, scaling, duplex modes |
| `POST /print` | multipart upload, returns a job (202) |
| `GET /test-page` | preview the maintenance test page as it would print now |
| `GET /jobs`, `GET /jobs/{id}` | job status and `pages_done` / `pages_total` |
| `DELETE /jobs/{id}` | cancel |
| `POST /print-schedule` | store a document and print it once or on a cron schedule (201) |
| `GET /print-schedule`, `GET /print-schedule/{id}` | schedules with next run and last result |
| `PATCH /print-schedule/{id}` | `{"enabled": false}` to pause, `true` to resume; `{"name": ...}` |
| `POST /print-schedule/{id}/run` | print it now, timetable unchanged |
| `DELETE /print-schedule/{id}` | delete it and its document |
| `GET /scanner` | scanner state, feeder state, and what each source supports |
| `POST /scan` | start a scan, returns its id (202) |
| `GET /scans`, `GET /scans/{id}` | scan status |
| `GET /scans/{id}/file` | download a finished scan |
| `DELETE /scans/{id}` | delete a scan and its file |

`POST /print` form fields:

| field | values |
|---|---|
| `file` | PDF or image; leave out with `test_page` |
| `test_page` | `true` prints a test page generated on the spot instead: the time, printer info and ink levels, plus solid bands, fine lines and gradients in every ink to keep the print head from clogging |
| `copies` | 1-99 |
| `media` | e.g. `iso_a4_210x297mm`, `na_index-4x6_4x6in` (default: the printer's) |
| `color_mode` | `auto`, `color`, `monochrome` (monochrome PDFs are sent as grayscale) |
| `quality` | `normal`, `high` |
| `scaling` | `fit` (default, inside the margins), `fill`, `none`; `auto`/`auto-fit` act as `fit` for PDFs |
| `sides` | PDF only: `one-sided`, `two-sided-long-edge`, `two-sided-short-edge` |
| `pages` | PDF only: `1-3,5` |
| `dry_run` | `true` renders and validates with the printer, prints nothing |

```bash
curl -F file=@photo.heic -F media=na_index-4x6_4x6in -F scaling=fill -F quality=high localhost:8000/print
curl -F file=@doc.pdf -F sides=two-sided-long-edge -F color_mode=monochrome localhost:8000/print
```

Print jobs are kept in memory and lost on restart; schedules and scans are in the database.

### Print schedules

`POST /print-schedule` takes the same fields as `/print` plus:

| field | values |
|---|---|
| `kind` | `once` or `cron` |
| `run_at` | `once`: e.g. `2026-10-22T09:00`, in `timezone` unless it has an offset |
| `cron` | `cron`: `minute hour day month weekday`, e.g. `0 9 * * 1` = Mondays 09:00 |
| `timezone` | IANA name, default `DEFAULT_TIMEZONE` |
| `name` | optional label |

A `test_page=true` schedule stores no document; it generates a fresh test page at every run.
The document and options are checked when the schedule is created, so a bad file fails then,
not at 3 a.m. When a schedule is due (checked every 30 s) it submits a normal print job;
`last_status` and `last_job` show what happened. A one-time schedule disables itself after it
runs. Runs missed while the service was down are printed if less than an hour late, otherwise
recorded as `missed`. Use `dry_run=true` to try a schedule without printing.

```bash
# Weekly nozzle check, so the ink doesn't dry in the print head
curl -F file=@testpage.pdf -F kind=cron -F "cron=0 9 * * 1" -F name="nozzle check" localhost:8000/print-schedule
curl -F file=@notice.pdf -F kind=once -F run_at=2026-10-22T08:30 localhost:8000/print-schedule
```

### Scanning

`POST /scan` form fields, all optional:

| field | values |
|---|---|
| `source` | `flatbed` (default) or `feeder` (the document feeder, one-sided only) |
| `color_mode` | `color` (default), `grayscale`, `bw` |
| `resolution` | `100`, `200`, `300` (default), `600`; `1200` on the flatbed only |
| `format` | `pdf` (default) or `jpeg` |
| `size` | `a4` (default), `letter`, `legal`, `max` (whole scan area) |

The request returns at once with an `id` and `status: scanning`. Poll `GET /scans/{id}` until
it's `completed` (about 15-30 s at 100-300 dpi) or `failed`, then download from
`GET /scans/{id}/file`; `X-Scan-Pages` says how many pages it holds. Feeder scans with several
pages come back as one PDF, or as a zip of JPEGs. Scans are kept for `SCAN_RETENTION_DAYS`.

The flatbed scans whatever is on the glass at that moment, so scans aren't queued: while one
runs, another gets 409, as does a feeder scan with no paper loaded.

```bash
curl -F color_mode=grayscale localhost:8000/scan        # -> {"id": "3bd9...", "status": "scanning", ...}
curl localhost:8000/scans/3bd9...                       # until "status": "completed"
curl -OJ localhost:8000/scans/3bd9.../file
```
