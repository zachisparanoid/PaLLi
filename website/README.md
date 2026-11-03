# PaLLi Local Dashboard

This directory contains the local Next.js dashboard for PaLLi Local.

It is not the center of the product. It is the local mission-control and
account/identity surface that mirrors the running instance.

## Supported Local Runtime Path

Use the repo launcher from the repository root:

```bash
scripts/palli_ctl.sh prepare
scripts/palli_ctl.sh start
scripts/palli_ctl.sh status
```

That path:

- builds the website with the local backend proxy target
- runs the dashboard in production mode via `next start`
- keeps the backend and frontend under one launcher state file

Open:

- `http://localhost:3000/dashboard`
- `http://localhost:3000/account`
- `http://localhost:3000/account/memory`
- `http://localhost:3000/account/identity`

## Development

If you specifically need a development server, you can still run:

```bash
cd website
npm install
NEXT_PUBLIC_API_PROXY_TARGET=http://127.0.0.1:8000 npm run dev
```

But that is now a development-only path. The supported local product path is
the launcher-managed production dashboard.

## Environment

The dashboard expects the backend to be available at the API proxy target.

Relevant values come from the repo root env:

- `BACKEND_URL`
- `FRONTEND_URL`
- `NEXT_PUBLIC_API_PROXY_TARGET` set by the launcher

## Commands

- `npm run build`
- `npm run start`
- `npm run dev`
- `npm run lint`
