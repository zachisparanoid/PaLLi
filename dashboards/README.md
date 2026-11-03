# PaLLi dashboards — local live suite

Read-only statistics pages, separate from the main setup/mission-control website.
Complete the normal `palli_ctl prepare` and `start` setup first so the shared server access
key exists, then run:

```bash
venv/bin/python dashboards/server.py
```

Open `http://YOUR_SERVER_IP:3310` on another device. The browser's sign-in prompt uses
username **palli** and the **server access key** printed by the main launcher as the password.
The pages and statistics APIs all require this credential. They read the DB/data/logs;
they do not start PaLLi or modify memory. No live runtime is imported by the collector.

The listener defaults to all interfaces, following `FRONTEND_HOST` when set. Override
`DASHBOARDS_HOST` to select a different bind, or `DASHBOARDS_PORT` to change port 3310.
For example, `DASHBOARDS_HOST=127.0.0.1` restricts these pages to the server itself.
A network listener refuses to start before an access key has been initialized.

`DASHBOARDS_TRUSTED_NETWORKS` optionally restricts authenticated clients to comma-separated
CIDRs. Blank/unset permits network-wide authenticated access; a malformed restriction does
not open access. Example: `DASHBOARDS_TRUSTED_NETWORKS=192.168.1.0/24,100.64.0.0/10`.
Restart after changing settings, including rotating the shared access key. Environment values override `.env.local` for this standalone
server. Use a trusted LAN, encrypted tailnet or HTTPS proxy, as with the main dashboard.

Pages: `/`, `/vitals`, `/backstage`, `/observatory`, `/replies`; APIs: `/api/stats`, `/api/replies`.

## Files

- `collect.py` — the one collector (reuses scripts/vitals.py parsers with an
  explicit plain-log file list, `palli_YYYY-MM-DD.log*` in any year; the
  verbose twin repeats the day log's lines and the debug one is a slice of
  the verbose one, so both this and `scripts/vitals.py` skip them).
- `server.py` — stdlib ThreadingHTTPServer, GET-only, 0.0.0.0:3310, the access-key and optional peer
  checks above, static dir locked, /api/stats cached 60 s, /api/replies read
  fresh (`collect.replies`: what each reply used, from data/reply_sources/).
- `static/` — suite.css tokens (light/dark/auto), common.js (fetch loop,
  theme toggle, SVG charts), four pages that re-render every minute, and
  replies.html: one table of what each reply drew on, refreshed every minute.
