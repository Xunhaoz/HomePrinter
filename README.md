# HomePrinter

FastAPI service that prints PDFs and photos on the EPSON L6490 over IPP (pyipp), without CUPS.

The printer only accepts JPEG / PWG raster / URF, not PDF, so:
- photos (JPEG, PNG, HEIC, ...) are converted to JPEG, with EXIF rotation applied;
- each PDF page is rendered to JPEG with PyMuPDF and sent as its own job, one at a time.
  The printer has no multi-document jobs, so **duplex does not work for PDFs**.

## Run

```bash
uv run homeprinter            # listens on 0.0.0.0:8000, API docs at /docs
```

| env | default | |
|---|---|---|
| `PRINTER_HOST` | `EPSONE62DA3.local` | mDNS name or IP; names resolve to IPv4 |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | where the API listens |
| `PDF_DPI` | `300` | PDF render resolution |
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
| `GET /printer/capabilities` | media, color modes, qualities, scaling |
| `POST /print` | multipart upload, returns a job (202) |
| `GET /jobs`, `GET /jobs/{id}` | job progress |
| `DELETE /jobs/{id}` | cancel |

`POST /print` form fields: `file`, `copies` (1-99), `media` (e.g. `iso_a4_210x297mm`,
`na_index-4x6_4x6in`), `color_mode` (`auto`/`color`/`monochrome`), `quality` (`normal`/`high`),
`scaling` (`fit` default, `fill`, `auto`, `auto-fit`, `none`), `pages` (PDF only, `1-3,5`),
`dry_run` (validate with the printer, print nothing).

```bash
curl -F file=@photo.heic -F media=na_index-4x6_4x6in -F scaling=fill -F quality=high localhost:8000/print
curl -F file=@doc.pdf -F pages=1-2 -F color_mode=monochrome localhost:8000/print
```

Jobs are kept in memory and lost on restart.
