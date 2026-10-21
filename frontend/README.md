# HomePrinter web app

React + Vite + Tailwind with [shadcn/ui](https://ui.shadcn.com) components (`src/components/ui`,
added with `npx shadcn@latest add <name>`). Built mobile-first for iPhone Safari, with a sidebar
layout from 1024 px up. Traditional Chinese UI.

```bash
npm install
npm run dev        # http://localhost:5173, /api is proxied to the backend on :8000
npm run build      # type-check and build into dist/
```

Set `API_TARGET=http://localhost:8001` if the backend runs elsewhere. Start the backend with an
`ACCESS_CODE` to see the login screen.

| | |
|---|---|
| `src/lib/api.ts` | typed client for every endpoint in [docs/api.md](../docs/api.md) |
| `src/lib/format.ts` | Chinese labels: paper sizes, states, cron text, translated error messages |
| `src/hooks/use-poll.ts` | polling that pauses in background tabs and speeds up while work is running |
| `src/pages/` | 首頁, 列印, 掃描, 排程, and the login screen |
| `public/nozzle-check.pdf` | the colour test page used by the weekly nozzle-check template |
| `scripts/gen-assets.py` | regenerates the icons and the test page (`uv run --project ../backend python scripts/gen-assets.py public`) |
| `nginx.conf` | serves the build and proxies `/api/` to the backend in Docker |

UX background: [user stories](../docs/ux/user-stories.md) and [CUJs](../docs/ux/cuj.md).
