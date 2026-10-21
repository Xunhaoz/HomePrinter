# HomePrinter

Print, scan and schedule prints on the home EPSON L6490 from a phone, at home or away.

| | |
|---|---|
| [backend/](backend/) | FastAPI service: IPP printing, eSCL scanning, schedules, passcode login ([README](backend/README.md)) |
| [frontend/](frontend/) | shadcn/ui web app, built for iPhone ([README](frontend/README.md)) |
| [docs/api.md](docs/api.md) | API specification |
| [docs/ux/](docs/ux/) | user stories and critical user journeys |

## Run with Docker

```bash
cp .env.example .env     # then set ACCESS_CODE, e.g. six letters
docker compose up -d --build
```

The app is at `http://<this machine>:8088` (`WEB_PORT`). The `web` container serves the app and
forwards `/api/*` to the `backend` container, which isn't published on its own. API docs are at
`/api/docs`.

| `.env` | default | |
|---|---|---|
| `ACCESS_CODE` | (required) | login code, case-insensitive; changing it logs everyone out |
| `SESSION_DAYS` | `30` | how long a login lasts |
| `PRINTER_HOST` | `192.168.0.126` | the printer's IP; reserve it in the router's DHCP settings |
| `WEB_PORT` | `8088` | |

### Exposing it to the internet

Put an HTTPS reverse proxy or tunnel (Cloudflare Tunnel, Tailscale Funnel, Caddy, ...) in front
of `WEB_PORT`; don't forward the plain HTTP port. Over HTTPS the session cookie is marked
`Secure` (the proxy must send `X-Forwarded-Proto: https`, which those do).

The login is deliberately simple: one shared code. Wrong guesses are limited to 5 per client and
30 in total per 15 minutes, so a six-letter code (about 300 million combinations) can't be
brute-forced; the flip side is that someone hammering the login can lock everyone out for 15
minutes. `/api/docs` itself is public but shows only the API's shape.

## Develop

```bash
cd backend && ACCESS_CODE=kitten uv run homeprinter     # :8000
cd frontend && npm install && npm run dev                # :5173
```
