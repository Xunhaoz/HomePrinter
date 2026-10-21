# HomePrinter

FastAPI service that prints PDFs and photos on the EPSON L6490 over IPP (pyipp), without CUPS.

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
