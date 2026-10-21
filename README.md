# HomePrinter

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
uv run pytest                 # encoder and layout tests
```

| env | default | |
|---|---|---|
| `PRINTER_HOST` | `EPSONE62DA3.local` | mDNS name or IP; names resolve to IPv4 |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | where the API listens |
| `SCANNER_PORT` / `SCANNER_PATH` | `443` / `/eSCL` | eSCL endpoint, HTTPS |
| `PDF_DPI` | `300` | PDF raster resolution (the printer takes 300 or 600) |
| `JPEG_QUALITY` | `92` | |
| `MAX_UPLOAD_MB` | `100` | |

There is no authentication: anyone on the LAN who can reach the port can print.

### Docker

```bash
docker compose up -d --build
```

`.local` names don't resolve inside the container, so [compose.yaml](compose.yaml) passes the
printer's IP (`PRINTER_HOST`, default `192.168.0.126`). Reserve that IP for the printer in the
router's DHCP settings, or override it: `PRINTER_HOST=192.168.0.50 docker compose up -d`.

## API

| | |
|---|---|
| `GET /printer` | state and ink levels |
| `GET /printer/capabilities` | media, color modes, qualities, scaling, duplex modes |
| `POST /print` | multipart upload, returns a job (202) |
| `GET /jobs`, `GET /jobs/{id}` | job status and `pages_done` / `pages_total` |
| `DELETE /jobs/{id}` | cancel |
| `GET /scanner` | scanner state, feeder state, and what each source supports |
| `POST /scan` | scan and return the file |

`POST /print` form fields:

| field | values |
|---|---|
| `file` | PDF or image |
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

Jobs are kept in memory and lost on restart.

### Scanning

`POST /scan` form fields, all optional:

| field | values |
|---|---|
| `source` | `flatbed` (default) or `feeder` (the document feeder, one-sided only) |
| `color_mode` | `color` (default), `grayscale`, `bw` |
| `resolution` | `100`, `200`, `300` (default), `600`; `1200` on the flatbed only |
| `format` | `pdf` (default) or `jpeg` |
| `size` | `a4` (default), `letter`, `legal`, `max` (whole scan area) |

The request waits until the scan is done (about 15-30 s at 100-300 dpi) and returns the file
as an attachment; `X-Scan-Pages` says how many pages it holds. Feeder scans with several pages
come back as one PDF, or as a zip of JPEGs. The flatbed scans whatever is on the glass. One
scan runs at a time; a second gets 409, as does a feeder scan with no paper loaded.

```bash
curl -OJ -F color_mode=grayscale localhost:8000/scan
curl -OJ -F source=feeder -F format=pdf localhost:8000/scan
```
