# PaLLi dashboards — local live suite

Read-only stat pages for this machine, your tailnet and, if you name it, your
own private network. The one piece of her runtime imported is the network
check (`core/trusted_networks.py`, standard library only); nothing of hers is
touched. The collector reads her DB (ro), data/ stores, and plain logs,
cached 60 s.

    tmux new-session -d -s palli-dash \
      "while true; do venv/bin/python dashboards/server.py; sleep 2; done"

Then: http://localhost:3310 — or your tailnet address, or a LAN address you
have let in, e.g. http://100.101.102.103:3310 · http://192.0.2.28:3310
(example addresses).
(/ suite home · /vitals · /backstage · /observatory · /replies · /api/stats ·
/api/replies)

## Who may open them

The server listens on 0.0.0.0:3310 but answers only:

- this machine (`127.0.0.0/8`, `::1`);
- the tailnet (`100.64.0.0/10`, Tailscale's range);
- the extra ranges in `DASHBOARDS_TRUSTED_NETWORKS`, comma-separated CIDRs,
  read from the environment if it is set there, else from the repo's
  `.env.local`:

      DASHBOARDS_TRUSTED_NETWORKS=192.0.2.0/24

Every other peer gets `403` with one fixed line, before the request reaches a
handler, so nothing of hers is read for it. The first two are always in: a
blank setting adds nothing, and a token that is not a network is left out
(the server names it on stderr at startup) and never widens the list. Restart
the server after changing the setting; it prints the ranges it answers.

## Files

- `collect.py` — the one collector (reuses scripts/vitals.py parsers with an
  explicit plain-log file list, `palli_YYYY-MM-DD.log*` in any year; the
  verbose twin repeats the day log's lines and the debug one is a slice of
  the verbose one, so both this and `scripts/vitals.py` skip them).
- `server.py` — stdlib ThreadingHTTPServer, GET-only, 0.0.0.0:3310, the peer
  check above, static dir locked, /api/stats cached 60 s, /api/replies read
  fresh (`collect.replies`: what each reply used, from data/reply_sources/).
- `static/` — suite.css tokens (light/dark/auto), common.js (fetch loop,
  theme toggle, SVG charts), four pages that re-render every minute, and
  replies.html: one table of what each reply drew on, refreshed every minute.
