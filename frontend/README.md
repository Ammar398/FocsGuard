# FocusGuard — student portal

React 19 + TypeScript + Vite + Tailwind 4 + Recharts.

## Running it

Node lives in `.tools/node` at the repository root, because `E:\files` (where it
was first unpacked) is not writable by this account — and that incomplete
extraction is why npm was missing its module tree.

```powershell
$env:Path = "E:\focusguard\.tools\node;$env:Path"

cd E:\focusguard\frontend
npm install
npm run dev          # http://localhost:5173
```

The backend must be running too:

```powershell
cd E:\focusguard
$env:JWT_SECRET_KEY = "<a long random string>"
.venv\Scripts\python.exe -m uvicorn focusguard_backend.main:app --port 8000
```

The dev server proxies `/api` to `http://127.0.0.1:8000`, so the browser sees a
single origin. That is not a way around CORS — it makes development match
production, where the app and the API are served from the same host. Point it
elsewhere with `FOCUSGUARD_API_URL`.

## Scripts

| Command | What it does |
|---|---|
| `npm run dev` | dev server with hot reload |
| `npm run build` | type-check (`tsc -b`) then bundle |
| `npm run lint` | oxlint |
| `npm run preview` | serve the production build locally |

## Layout

```
src/
  api/        client.ts (auth, refresh, fetch) and types.ts (the wire contract)
  auth/       AuthProvider, and the context/hook it uses
  components/ FocusRing, FocusTimeline, ActivityBreakdown, StatCard
  pages/      LoginPage, DashboardPage, SessionPage
  lib/        formatting and the polling hook
```

## Two things worth knowing before you change anything

**Refreshing is single-flight, and that is not an optimisation.** Refresh tokens
rotate, and a token used twice is treated as stolen — the backend revokes the
whole family. Three requests all getting a 401 and all refreshing with the same
stored token look exactly like a replay, and the user would be signed out. See
the header of `src/api/client.ts`.

**The access token is in memory; the refresh token is in `localStorage`.** That
second part is a real weakness, documented rather than glossed over: an XSS here
can read a thirty-day token. httpOnly cookies are the right answer and need a
backend change plus CSRF protection — Phase 23.

See `docs/student_portal.md` for the design decisions behind the screens.
