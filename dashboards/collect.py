"""Read-only stats collector for the local PaLLi dashboard suite.

Everything here READS: her DB (ro URI), the data/ stores, plain logs.
Nothing in her runtime is imported or touched. Reuses scripts/vitals.py's
line parsers, fed an explicit plain-log file list (palli_YYYY-MM-DD.log*
only, any year — never the debug/verbose twins, which repeat the day log's
lines and would double-count; vitals.py skips them too since Batch 8).
"""
from __future__ import annotations

import collections
import datetime as dt
import glob
import json
import os
import re
import sqlite3
import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import vitals as V  # parse_night_chain / parse_timeouts / parse_reply_governs / parse_moment

PROJECTS_ROOT = Path("/home/palli-work/workspace/projects")
WINDOW_DAYS = 14

_WOKE = re.compile(r"woke_on=\[([^\]]*)\]")
# A dated store's files, by name, in any year (final fix wave, ruling FW-11):
# a "2026-*" glob would have gone blank on 2027-01-01.
DATED_JSONL = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*.jsonl"


def _plain_log_files(min_date: str):
    out = []
    for f in glob.glob(str(REPO / "logs" / "palli_[0-9][0-9][0-9][0-9]-*.log*")):
        b = os.path.basename(f)
        if "debug" in b or "verbose" in b:
            continue
        # keep files that can still hold lines in-window (mtime-based, generous)
        if dt.datetime.fromtimestamp(os.path.getmtime(f)).date().isoformat() >= min_date:
            out.append(f)
    return sorted(out)


def _lines(files):
    # Batch 8, task 5: a daily log over 7 days old is a .gz (retention keeps
    # its mtime, so the window above still picks it); read it as text. A
    # broken one costs only its own lines (EOFError: a truncated .gz).
    for f in files:
        try:
            with V.open_log_text(f) as fh:
                yield from fh
        except (OSError, EOFError):
            continue


def _iso_local(ts):
    if isinstance(ts, (int, float)):
        return dt.datetime.fromtimestamp(ts)
    return dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone()


def collect() -> dict:
    t0 = dt.datetime.now()
    today = t0.date()
    days_full = [(today - dt.timedelta(days=WINDOW_DAYS - 1 - i)).isoformat() for i in range(WINDOW_DAYS)]
    days = [d[5:] for d in days_full]
    cut7 = (t0 - dt.timedelta(days=7)).timestamp()
    errors: list[str] = []
    out: dict = {"days": days, "generated_at": t0.strftime("%Y-%m-%d %H:%M:%S")}

    def z():
        return [0] * WINDOW_DAYS

    idx = {d: i for i, d in enumerate(days_full)}
    S = out["series"] = {k: z() for k in (
        "mono", "dream", "bench", "bench_empty", "bench_shared", "msgs_him", "msgs_her",
        "say", "hold", "median", "arb_sent", "arb_drop", "arb_fail", "governed")}
    S["median"] = [None] * WINDOW_DAYS

    # ---- monologue: per-day counts + hour grid + 7d hour histogram
    try:
        grid = {d: [0] * 24 for d in days}
        hours7 = [0] * 24
        for f in glob.glob(str(REPO / "data" / "inner_monologue" / DATED_JSONL)):
            if os.path.basename(f)[:10] < days_full[0]:
                continue
            for line in open(f, encoding="utf-8", errors="replace"):
                try:
                    t = _iso_local(json.loads(line).get("timestamp"))
                except Exception:
                    continue
                d = t.date().isoformat()
                if d in idx:
                    S["mono"][idx[d]] += 1
                    grid[d[5:]][t.hour] += 1
                    if t.timestamp() > cut7:
                        hours7[t.hour] += 1
        out["grid"] = grid
        out["hours7"] = hours7
    except Exception as e:
        errors.append(f"monologue: {e}")

    # ---- daydreams / workbench
    try:
        for f in glob.glob(str(REPO / "data" / "daydream" / DATED_JSONL)):
            d = os.path.basename(f)[:10]
            if d in idx:
                S["dream"][idx[d]] = sum(1 for _ in open(f, encoding="utf-8", errors="replace"))
        signoffs = []
        for f in sorted(glob.glob(str(REPO / "data" / "workbench" / DATED_JSONL))):
            d = os.path.basename(f)[:10]
            if d not in idx:
                continue
            for line in open(f, encoding="utf-8", errors="replace"):
                j = json.loads(line)
                S["bench"][idx[d]] += 1
                so = (j.get("signoff") or "").strip()
                if so.startswith("Nothing moved"):
                    S["bench_empty"][idx[d]] += 1
                if str(j.get("shared")) in ("pending", "True", "sent"):
                    S["bench_shared"][idx[d]] += 1
                signoffs.append({"d": d[5:], "mins": round((j.get("duration_s") or 0) / 60),
                                 "shared": str(j.get("shared") or ""), "text": so[:180]})
        out["signoffs"] = signoffs[-6:]
    except Exception as e:
        errors.append(f"bench: {e}")

    # ---- DB: messages, reply medians, stakes, emo
    try:
        db = sqlite3.connect(f"file:{REPO}/data/palli-local.sqlite3?mode=ro", uri=True)
        words = collections.defaultdict(list)
        q = ("select date(timestamp,'unixepoch','localtime') d, role, content from conversations "
             "where timestamp > strftime('%s','now') - ?*86400")
        for d, role, c in db.execute(q, (WINDOW_DAYS + 1,)):
            if d in idx and role in ("user", "assistant"):
                S["msgs_him" if role == "user" else "msgs_her"][idx[d]] += 1
                if role == "assistant" and c:
                    words[d].append(len(str(c).split()))
        for d, w in words.items():
            S["median"][idx[d]] = int(statistics.median(w))
        stakes_open = collections.Counter()
        stakes_resolved = collections.Counter()
        for kind, status, n in db.execute("select kind, status, count(*) from stakes group by kind, status"):
            (stakes_open if status == "open" else stakes_resolved)[kind] += n
        emo_pts, emo_last = [], []
        q = ("select datetime(created_at,'unixepoch','localtime'), weight, needs_support, read_text "
             "from emotional_timeline where created_at > strftime('%s','now') - ?*86400 order by created_at")
        for ts, w, ns, txt in db.execute(q, (WINDOW_DAYS,)):
            emo_pts.append([ts[5:16], round(w, 2)])
            emo_last.append({"d": ts[5:16], "w": round(w, 2), "txt": (txt or "")[:150]})
        ws = [p[1] for p in emo_pts]
        out["emo"] = {"pts": emo_pts, "avg": round(sum(ws) / len(ws), 2) if ws else None,
                      "last": emo_last[-3:][::-1]}
        db.close()
    except Exception as e:
        errors.append(f"db: {e}")
        stakes_open, stakes_resolved = collections.Counter(), collections.Counter()

    # ---- arbiter ledger
    arb_reasons = []
    try:
        for f in sorted(glob.glob(str(REPO / "data" / "arbiter" / DATED_JSONL))):
            d = os.path.basename(f)[:10]
            if d not in idx:
                continue
            for line in open(f):
                j = json.loads(line)
                disp = j.get("disposition", "")
                key = "arb_sent" if disp.startswith("sent") else ("arb_drop" if disp == "stale_dropped" else "arb_fail")
                S[key][idx[d]] += 1
                if j.get("reason"):
                    arb_reasons.append({"d": d[5:], "disp": disp, "lane": j.get("lane") or "",
                                        "reason": j["reason"][:150]})
        out["arb_reasons"] = arb_reasons[-5:][::-1]
    except Exception as e:
        errors.append(f"arbiter: {e}")

    # ---- log-derived: moment, governor, night chain, timeouts, wakes
    try:
        files8 = _plain_log_files((today - dt.timedelta(days=9)).isoformat())
        lines8 = list(_lines(files8))
        for d, sh in V.parse_moment(lines8).items():
            if d in idx:
                S["say"][idx[d]] = sh["say"]
                S["hold"][idx[d]] = sh["hold"]
        for d, g in V.parse_reply_governs(lines8).items():
            if d in idx:
                S["governed"][idx[d]] = g.get("compressed", 0) + g.get("trimmed", 0)
        nights = V.parse_night_chain(lines8)
        out["night"] = [{"d": d[5:], **nights[d]} for d in sorted(nights) if d >= days_full[-7]]
        cut7s = (today - dt.timedelta(days=7)).isoformat()
        cut3s = (today - dt.timedelta(days=3)).isoformat()
        t7 = V.parse_timeouts(l for l in lines8 if l[:10] >= cut7s)
        t3 = V.parse_timeouts(l for l in lines8 if l[:10] >= cut3s)
        out["timeouts7"] = {"attempts": sum(v["attempts"] for v in t7.values()),
                            "gave_up": sum(v["gave_up"] for v in t7.values()),
                            "gave_up_3d": sum(v["gave_up"] for v in t3.values())}
        cut48 = (t0 - dt.timedelta(hours=48)).strftime("%Y-%m-%d %H:%M:%S")
        wakes = collections.Counter()
        for line in lines8:
            if line[:19] >= cut48:
                m = _WOKE.search(line)
                if m:
                    for r in m.group(1).split(","):
                        r = r.strip().strip("'\"")
                        if r:
                            wakes[r] += 1
        house = {"device_arrived", "device_left", "owner_arrived_after_absence"}
        out["wakes"] = [[k, n, 1 if k in house else 0] for k, n in wakes.most_common()]
    except Exception as e:
        errors.append(f"logs: {e}")

    # ---- deeds ring
    try:
        deeds = []
        for line in open(REPO / "data/recent_deeds.jsonl"):
            j = json.loads(line)
            deeds.append({"at": dt.datetime.fromtimestamp(j["at"]).strftime("%m-%d %H:%M"),
                          "kind": j["kind"], "detail": j["detail"][:160]})
        out["deeds"] = deeds
    except Exception as e:
        errors.append(f"deeds: {e}")

    # ---- tool_use from session transcripts (7d)
    try:
        tools = collections.Counter()
        by_sess = collections.Counter()
        cut7d = (today - dt.timedelta(days=7)).isoformat()
        for f in glob.glob(str(REPO / "data/agent_sessions/*.jsonl")):
            if ".bak" in f:
                continue
            base = os.path.basename(f).replace(".jsonl", "")
            for line in open(f, encoding="utf-8", errors="replace"):
                try:
                    j = json.loads(line)
                except Exception:
                    continue
                ts = j.get("timestamp", "")
                if not ts or ts[:10] < cut7d:
                    continue
                c = j.get("content")
                if isinstance(c, list):
                    for b in c:
                        if isinstance(b, dict) and b.get("type") == "tool_use":
                            tools[b.get("name", "?")] += 1
                            by_sess["conversation" if base.startswith("discord") else
                                    ("her bench" if base.startswith("workbench") else "other lanes")] += 1
        out["tools7"] = tools.most_common(14)
        out["tools7_total"] = sum(tools.values())
        out["tools7_split"] = dict(by_sess)
    except Exception as e:
        errors.append(f"tools: {e}")

    # ---- books: fears, quests, forge, lan, regrets
    try:
        fears = {}
        for line in open(REPO / "data/fears/fears.jsonl"):
            j = json.loads(line)
            fid = j.get("id", "?")
            e = fears.setdefault(fid, {"id": fid, "state": "carried", "note": ""})
            if j.get("op") == "face":
                e["state"] = "faced & retired"
                e["note"] = (j.get("note") or "")[:120]
            elif j.get("op") == "touch":
                e["state"] = "carried, softening"
                e["note"] = (j.get("line") or "")[:120]
        quests = {}
        for line in open(REPO / "data/quests/quests.jsonl"):
            j = json.loads(line)
            if j.get("op") == "plant":
                quests[j["id"]] = {"status": "alive", "src": j.get("source", ""),
                                   "text": (j.get("text") or "")[:110]}
            elif j.get("op") == "status" and j["id"] in quests:
                quests[j["id"]]["status"] = j.get("status", "?")
        forge = [{"name": m["name"], "enabled": m.get("enabled", False),
                  "tests": (m.get("test_report") or "").strip(),
                  "ask": (m.get("original_ask") or "")[:120]}
                 for m in json.load(open(REPO / "data/forge/manifest.json"))]
        try:
            lan = json.load(open(REPO / "data/lan_presence.json"))
            lan_devices = len(lan.get("devices", lan)) if isinstance(lan, dict) else len(lan)
        except Exception:
            lan_devices = None
        out["books"] = {
            "stakes_open": dict(stakes_open), "stakes_resolved": dict(stakes_resolved),
            "regrets_open": stakes_open.get("regret", 0),
            "fears": list(fears.values()),
            "quests": list(quests.values()),
            "forge": forge, "lan_devices": lan_devices,
        }
    except Exception as e:
        errors.append(f"books: {e}")

    # ---- her projects (file mtimes)
    try:
        projects = []
        for proj in sorted(os.listdir(PROJECTS_ROOT)):
            pd = PROJECTS_ROOT / proj
            if not pd.is_dir():
                continue
            per_day = collections.Counter()
            total = 0
            for dirpath, _dirs, fs in os.walk(pd):
                for fn in fs:
                    try:
                        m = dt.datetime.fromtimestamp((Path(dirpath) / fn).stat().st_mtime).date().isoformat()
                    except OSError:
                        continue
                    total += 1
                    if m in idx:
                        per_day[m[5:]] += 1
            projects.append([proj, dict(per_day), total])
        projects.sort(key=lambda p: (-sum(p[1].values()), -p[2]))
        out["projects"] = projects
    except Exception as e:
        errors.append(f"projects: {e}")

    # ---- the most recent fully-quiet day (no messages either way, not today)
    try:
        qd = None
        for i in range(WINDOW_DAYS - 2, -1, -1):
            if S["msgs_him"][i] == 0 and S["msgs_her"][i] == 0 and S["mono"][i] > 0:
                qd = {"d": days[i], "mono": S["mono"][i], "dream": S["dream"][i],
                      "bench": S["bench"][i], "bench_empty": S["bench_empty"][i],
                      "say": S["say"][i], "hold": S["hold"][i]}
                break
        out["quiet_day"] = qd
    except Exception as e:
        errors.append(f"quiet: {e}")

    out["errors"] = errors
    out["collect_ms"] = int((dt.datetime.now() - t0).total_seconds() * 1000)
    return out


def _reply_sources_dir() -> Path:
    """The folder core/reply_sources.py writes, by its own rule
    (PALLI_REPLY_SOURCES_DIR, else data/reply_sources/), read here with the
    standard library only."""
    raw = os.environ.get("PALLI_REPLY_SOURCES_DIR")
    return Path(raw) if raw else REPO / "data" / "reply_sources"


def replies(days: int = 3) -> dict:
    """What each reply used (R15, memory that reaches, T13): the last
    ``days`` days of data/reply_sources/ (today included), newest first, for
    the /replies page. Read fresh on every request; a line that can't be
    read is skipped, a file that can't be read is named in ``errors``."""
    days = max(1, int(days or 1))
    today = dt.date.today()
    folder = _reply_sources_dir()
    rows: list = []
    errors: list[str] = []
    for back in range(days):
        path = folder / f"{today - dt.timedelta(days=back):%Y-%m-%d}.jsonl"
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except FileNotFoundError:
            continue
        except OSError as e:
            errors.append(f"replies: {path.name}: {type(e).__name__}")
            continue
        day_rows = []
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                day_rows.append(row)
        rows.extend(reversed(day_rows))
    return {"days": days, "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "rows": rows, "errors": errors}


if __name__ == "__main__":
    print(json.dumps(collect(), ensure_ascii=False, indent=1)[:4000])
