#!/usr/bin/python3
"""Replace the vault's two keys, then scrub the OLD values from disk.
Runs under system python3 (pykeepass lives there). Never prints a value."""
from __future__ import annotations
import argparse, fcntl, os, re, secrets, sqlite3, stat, tempfile, time
from pathlib import Path

# Fix round 1 (2026-09-26), item on the SSN grammar: `[0-9]` instead of `\d` —
# `\d` also matches Arabic-Indic/fullwidth Unicode digits, which are not an
# SSN. Exact grammars only (owner rule). Same change made in
# core/agent/payload_masking.py's "ssn" pattern.
SSN = re.compile(r"(?<![0-9-])[0-9]{3}-[0-9]{2}-[0-9]{4}(?![0-9-])")

# Batch 8 Task 11 A4: an HTTP bearer token written into a transcript, a tool
# result or a log (09-29: found in one transcript and one tool-results file;
# 10-01: also one verbose log). 20+ token characters, so prose like "Bearer
# tokens" or a short placeholder is never touched; the replacement holds a
# bracket, which the class excludes, so a second run finds nothing.
BEARER = re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{20,}")
BEARER_REDACTED = "Bearer [REDACTED]"

# A byte-level rewrite of a compressed file corrupts it, so scrub() recognizes
# these extensions and leaves the bytes untouched rather than attempting to
# decode and rewrite them as text (controller ruling R7). Batch 8 (task 5):
# retention gzips logs after a few days, so the skip is counted and printed —
# a log zipped before a scrub keeps its old values until it ages out.
_COMPRESSED_SUFFIXES = {".gz", ".xz", ".zip", ".bz2", ".zst"}

# Bounded wait for the vault lock: poll LOCK_EX|LOCK_NB every _LOCK_POLL_SECONDS,
# up to _LOCK_WAIT_SECONDS total, then give up rather than hang forever. Tests
# monkeypatch both down so a "someone else is using the vault" run stays fast.
_LOCK_WAIT_SECONDS = 60.0
_LOCK_POLL_SECONDS = 0.1

# Fix round 1 + round 2: the same values also live in these DB tables/columns
# (found by reading core/sqlite_schema_catalog.py and core/memory_service.py,
# not guessed — memory_index_items.snippet is a 220-char copy of .content,
# core/memory_service.py:1176). Discovered per-DB via PRAGMA table_info before
# use, so an absent table or renamed column is skipped, not fatal. The two
# FTS5 tables (memory_index_fts, monologue_memory_fts) are scrubbed the same
# way as any other table — see the note in scrub() for why — and then
# 'optimize'd; see the note there for what that fixes.
_DB_TEXT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("conversations", "content"),
    ("identity_document_versions", "content"),
    ("memory_index_items", "content"),
    ("memory_index_items", "snippet"),
    ("memory_index_fts", "content"),
    ("conversation_compactions", "summary_text"),
    ("agent_steps", "observation"),
    ("agent_steps", "action_input"),
    ("monologue_memory_fts", "text_content"),
    ("user_memory", "content"),
)


def _acquire_lock(vault_dir: Path):
    """Exclusive, bounded-wait lock on <vault_dir>/.lock. Her vault saves and the
    nightly backup take the same lock, so rotation never runs underneath one of
    them mid-write. Created mode 600 if missing; left in place afterward (never
    moved into retired/)."""
    lock_path = vault_dir / ".lock"
    if not lock_path.exists():
        lock_path.touch()
        os.chmod(lock_path, 0o600)
    fh = open(lock_path, "r+")
    deadline = time.monotonic() + _LOCK_WAIT_SECONDS
    while True:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fh
        except BlockingIOError:
            if time.monotonic() >= deadline:
                fh.close()
                raise SystemExit(
                    f"could not lock {lock_path} within {_LOCK_WAIT_SECONDS}s "
                    "(something else is using the vault); nothing swapped"
                )
            time.sleep(_LOCK_POLL_SECONDS)


def _fsync_path(path: Path) -> None:
    """fsync a file's or a directory's data to disk. A read-only fd is enough:
    fsync flushes the inode's dirty pages regardless of how the fd was opened,
    and on POSIX a directory can only be opened O_RDONLY anyway (needed to
    fsync the rename entries themselves after the swap)."""
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_new_file(path: Path, data: bytes, *, exclusive: bool = False) -> None:
    """Write+fsync one of the *.new files. `exclusive=True` (O_EXCL) refuses to
    clobber a file that's already there — used for .passphrase.new, the one
    file whose accidental overwrite would be a lost key with no way back."""
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    fd = os.open(str(path), flags, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def rotate(vault_dir: Path) -> Path:
    from pykeepass import PyKeePass
    from pykeepass.exceptions import CredentialsError

    # (a) Every file/dir this function creates lands 0600/0700 from the
    # moment it's created, not via a chmod afterward that a crash could beat.
    old_umask = os.umask(0o077)
    try:
        lock_fh = _acquire_lock(vault_dir)
        try:
            kdbx, pwf, kff = vault_dir / "credentials.kdbx", vault_dir / ".passphrase", vault_dir / "vault.key"
            new_kdbx = vault_dir / "credentials.kdbx.new"
            new_kf = vault_dir / "vault.key.new"
            new_pwf = vault_dir / ".passphrase.new"
            tmp_kdbx = vault_dir / "credentials.kdbx.tmp"  # pykeepass's own save() temp file

            # Fix round 2, item 1: deleting *.new/*.tmp BEFORE checking the
            # live set reintroduced the exact defect this script exists to
            # prevent. Proven by SIGKILL-after-a-specific-replace() probes: if
            # a previous run died right after promoting credentials.kdbx.new
            # onto credentials.kdbx (but before promoting the other two), the
            # live kdbx is the NEW one and the ONLY thing on disk that can
            # currently open it is credentials.kdbx (live) + .passphrase.new +
            # vault.key.new — blind cleanup here would delete precisely those
            # two files and leave an unrecoverable vault. Symmetrically, if a
            # previous run died mid-retire (old kdbx already moved into
            # retired/, before any promote), the *.new files are a COMPLETE,
            # already-verified new key set — blind cleanup would throw that
            # away too. So: touch nothing until the live set is proven to be
            # a complete, currently-consistent generation on its own.
            missing = [p.name for p in (kdbx, pwf, kff) if not p.exists()]
            live_pw: str | None = None
            live_opens = False
            if not missing:
                try:
                    live_pw = pwf.read_text(encoding="utf-8").strip()
                    PyKeePass(str(kdbx), password=live_pw, keyfile=str(kff))
                    live_opens = True
                except Exception:
                    live_opens = False

            if missing or not live_opens:
                present = sorted(p.name for p in vault_dir.iterdir())
                retired_dirs = sorted(
                    p.name for p in vault_dir.iterdir() if p.is_dir() and p.name.startswith("retired-")
                )
                print(f"vault dir contains: {present}")
                print(f"retired-* dirs present: {retired_dirs}")
                print(
                    "a previous rotation was interrupted — restore by hand from the newest "
                    "retired-* dir, or finish it with the *.new files; nothing was changed"
                )
                raise SystemExit(1)

            # Only now — the live set just proved itself complete and
            # self-consistent — is a leftover *.new/*.tmp file guaranteed to
            # be stale garbage from an already-resolved earlier attempt.
            for stale in (new_kdbx, new_kf, new_pwf, tmp_kdbx):
                stale.unlink(missing_ok=True)

            old_pw, old_kf = live_pw, kff
            kp = PyKeePass(str(kdbx), password=old_pw, keyfile=str(old_kf))
            n = len(kp.entries)
            new_pw = secrets.token_urlsafe(40)[:50]

            # One guarded region covers writing the *.new files, the verify-
            # open, creating retired-<stamp>/, and the retire+promote swap —
            # ANY failure anywhere in here removes the *.new/*.tmp files, and
            # any retire already done is walked back (so mkdir failing, e.g.
            # disk full, is covered by the same cleanup as a failed replace()
            # deep in the swap; see (e) below).
            retired: Path | None = None
            retired_pairs: list[tuple[Path, Path]] = []  # (original, retired_copy), in the order moved
            old_backups = vault_dir / "backups"
            backups_retired = False
            try:
                # (b) Write + fsync all three *.new files BEFORE any swap.
                _write_new_file(new_kf, (secrets.token_hex(32) + "\n").encode("utf-8"))
                _fsync_path(new_kf)

                kp.password, kp.keyfile = new_pw, str(new_kf)
                kp.save(filename=str(new_kdbx))
                os.chmod(new_kdbx, 0o600)
                _fsync_path(new_kdbx)

                # O_EXCL: under our own lock, this file must not already exist.
                _write_new_file(new_pwf, (new_pw + "\n").encode("utf-8"), exclusive=True)
                _fsync_path(new_pwf)

                # (c) Verify-open re-reads the passphrase from the file on
                # disk, not the in-memory new_pw string — this is the check
                # that the PERSISTED value actually works.
                reread_pw = new_pwf.read_text(encoding="utf-8").strip()
                check = PyKeePass(str(new_kdbx), password=reread_pw, keyfile=str(new_kf))
                if len(check.entries) != n:
                    raise SystemExit(f"entry count changed ({n} -> {len(check.entries)}); nothing swapped")
                try:
                    PyKeePass(str(new_kdbx), password=old_pw, keyfile=str(old_kf))
                except CredentialsError:
                    pass
                else:
                    raise SystemExit("the old keys still open the new vault; nothing swapped")

                # (d) The swap: retire the old three (+ backups/), then promote.
                retired = vault_dir / f"retired-{time.strftime('%Y%m%d-%H%M%S')}"
                retired.mkdir(mode=0o700)
                for original in (kdbx, pwf, kff):
                    dest = retired / original.name
                    original.replace(dest)
                    retired_pairs.append((original, dest))
                if old_backups.is_dir():
                    old_backups.replace(retired / "backups")
                    backups_retired = True
                for src, dest in ((new_kdbx, kdbx), (new_kf, kff), (new_pwf, pwf)):
                    src.replace(dest)
                _fsync_path(vault_dir)
            except BaseException:
                # (e) The vault must end up either fully old or fully new — on
                # ANY failure here we choose fully old: move back whatever was
                # already retired. Because replace() atomically overwrites its
                # destination, moving a retired copy back onto a path that a
                # .new file already got promoted into removes/replaces that
                # promoted file as a side effect — exactly "remove any
                # promoted new file that would shadow it".
                for original, dest in reversed(retired_pairs):
                    if dest.exists():
                        dest.replace(original)
                if backups_retired and retired is not None and (retired / "backups").exists() and not old_backups.exists():
                    (retired / "backups").replace(old_backups)
                # (f) Every abort path before/during the swap removes the
                # *.new/*.tmp files — this now also covers retired.mkdir()
                # failing, since that's inside this same guarded region.
                for stale in (new_kdbx, new_kf, new_pwf, tmp_kdbx):
                    stale.unlink(missing_ok=True)
                if retired is not None:
                    try:
                        retired.rmdir()
                    except OSError:
                        pass
                raise

            os.chmod(pwf, 0o600)  # belt-and-braces; replace() already carried this mode over
            print(f"rotated: {n} entries; old keys in {retired}")
            return retired
        finally:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
            lock_fh.close()
    finally:
        os.umask(old_umask)


def _rewrite_atomically(p: Path, data: bytes) -> None:
    """Second read F6 (2026-10-04): a scrubbed file is written to a temporary
    file in the same folder, flushed and synced, then put over the original
    with os.replace — the core/retention.py `_act_gzip` pattern. The old
    read_bytes/write_bytes rewrite truncated first, so a crash or a full disk
    in between left her transcript (or a moment, or a monologue) cut short or
    empty. The original's mode is kept; on any failure the temporary file is
    removed and the original is untouched."""
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=f".{p.name}.", suffix=".scrub-tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            os.fchmod(fh.fileno(), stat.S_IMODE(p.stat().st_mode))
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# Second read F6: her port. A real scrub refuses while her process listens
# on it — see main().
HER_PORT = 8000


def _her_pid(repo: Path, port: int) -> int | None:
    """Her pid when her main.py from `repo` holds `port`, else None — A3's own
    reader (core/her_listener.py, standard library only, so it imports under
    system python3 like the rest of this script)."""
    import sys

    root = str(Path(__file__).resolve().parents[1])
    if root not in sys.path:
        sys.path.insert(0, root)
    from core import her_listener

    return her_listener.her_listener_pid(int(port), project_root=Path(repo))


def _existing_db_columns(con: sqlite3.Connection) -> list[tuple[str, str]]:
    """Only the (table, column) pairs from _DB_TEXT_COLUMNS that actually exist
    in THIS db. PRAGMA table_info returns an empty result for a table that
    isn't there (no exception raised), so an absent table or a renamed column
    is skipped gracefully rather than failing the whole scrub."""
    found = []
    for table, col in _DB_TEXT_COLUMNS:
        cols = {row[1] for row in con.execute(f"PRAGMA table_info({table})").fetchall()}
        if col in cols:
            found.append((table, col))
    return found


def _is_fts5_table(con: sqlite3.Connection, table: str) -> bool:
    row = con.execute("SELECT sql FROM sqlite_master WHERE name = ?", (table,)).fetchone()
    return bool(row and row[0] and "fts5" in row[0].lower())


def scrub(paths, literals, *, ssn: bool, db: Path | None, bearer: bool = False,
          dry_run: bool = False) -> dict:
    """Replace the old literals (and SSNs, and bearer tokens when asked) in
    `paths` and the db's text columns.

    `dry_run` (A4) counts exactly what a real run would change and writes
    nothing: no file is rewritten, the db is opened read-only. `files` and
    `db_rows` then mean "would change"."""
    counts = {"literals": 0, "ssn": 0, "bearer": 0, "files": 0, "db_rows": 0,
              "compressed_skipped": 0}

    def fix(text: str) -> str:
        for lit in sorted(literals, key=len, reverse=True):
            c = text.count(lit); counts["literals"] += c
            text = text.replace(lit, "[REDACTED]")
        if ssn:
            text, c = SSN.subn("[MASKED:ssn]", text); counts["ssn"] += c
        if bearer:
            text, c = BEARER.subn(BEARER_REDACTED, text); counts["bearer"] += c
        return text

    for p in paths:
        if p.suffix.lower() in _COMPRESSED_SUFFIXES:
            counts["compressed_skipped"] += 1
            continue
        raw = p.read_bytes(); text = raw.decode("utf-8", errors="surrogateescape")
        new = fix(text)
        if new != text:
            if not dry_run:
                _rewrite_atomically(p, new.encode("utf-8", errors="surrogateescape"))
            counts["files"] += 1

    if db is not None:
        # Never auto-creates an empty database: mode=rw fails loudly on a
        # missing/typo'd path instead of quietly scrubbing nothing.
        # db.resolve() makes it absolute first — as_uri() requires that.
        # A dry run opens it read-only (mode=ro fails the same way on a
        # missing path) and never UPDATEs, optimizes or commits.
        if dry_run:
            con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
            try:
                for table, col in _existing_db_columns(con):
                    for _rowid, content in con.execute(f"SELECT rowid, {col} FROM {table}"):
                        if isinstance(content, str) and content and fix(content) != content:
                            counts["db_rows"] += 1
            finally:
                con.close()
            return counts
        con = sqlite3.connect(db.resolve().as_uri() + "?mode=rw", uri=True)
        # A page SQLite frees on UPDATE/DELETE is only unlinked from the
        # b-tree by default — the OLD bytes stay sitting in that page until
        # something overwrites it, which is exactly how a leftover copy of a
        # scrubbed value can still be read straight off the disk. Ubuntu's
        # SQLite build happens to default this on, which is the only reason
        # the raw-byte removal has worked so far; the reviewer turned it off
        # in a large-row WAL probe and found 13 copies of the old value still
        # in the file. Force it, don't rely on the build default.
        con.execute("PRAGMA secure_delete=ON")
        try:
            touched_fts_tables: set[str] = set()
            for table, col in _existing_db_columns(con):
                # The two FTS5 tables here (memory_index_fts,
                # monologue_memory_fts) are plain (non "content=external")
                # FTS5 tables per core/sqlite_schema_catalog.py — each holds
                # its own copy of the text, kept in sync only by application
                # code (core/memory_store.py's upsert_memory_index_fts /
                # clear_memory_index_fts for the first; nothing writes the
                # second yet), not by any SQL trigger. There is nothing to
                # "rebuild" from and no trigger to rely on, so each is
                # rewritten directly like any other table: FTS5 supports
                # ordinary UPDATE-by-rowid on a table it owns the content of,
                # and that correctly updates its internal index (verified: a
                # MATCH for the old literal finds nothing afterward, a MATCH
                # for the replacement text finds the row).
                rows = con.execute(f"SELECT rowid, {col} FROM {table}").fetchall()
                changed_here = False
                for rowid, content in rows:
                    if not isinstance(content, str) or not content:
                        continue
                    new = fix(content)
                    if new != content:
                        con.execute(f"UPDATE {table} SET {col} = ? WHERE rowid = ?", (new, rowid))
                        counts["db_rows"] += 1
                        changed_here = True
                if changed_here and _is_fts5_table(con, table):
                    touched_fts_tables.add(table)
            # An UPDATE against an FTS5 table removes the old value from the
            # column AND from the row it indexes, but a MERGED-away copy of
            # the old term can still exist physically in an older, not-yet-
            # merged segment — 'optimize' forces the merge. Confirmed on a
            # 50-row table built from individual inserts (the shape that
            # creates multiple segments): raw db-file bytes still contained
            # 2 fragments of the old literal after the UPDATEs and before
            # optimize, 0 after.
            for table in sorted(touched_fts_tables):
                con.execute(f"INSERT INTO {table}({table}) VALUES('optimize')")
            con.commit()
        finally:
            con.close()
    return counts


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("rotate"); r.add_argument("--vault-dir", default=str(Path.home() / ".palli" / "vault"))
    s = sub.add_parser("scrub-old")
    s.add_argument("--retired", help="the retired-<stamp> dir holding the old keys (optional with --bearer)")
    s.add_argument("--ssn", action="store_true")
    s.add_argument("--bearer", action="store_true",
                   help="also replace 'Bearer <20+ token chars>' with 'Bearer [REDACTED]'")
    s.add_argument("--dry-run", action="store_true",
                   help="count per glob (and in the db) what would change; write nothing. "
                        "Run it before retention gzips a log: a compressed file is never rewritten")
    s.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    s.add_argument("--no-db", action="store_true", help="skip the app db entirely (otherwise its absence is an error)")
    s.add_argument("--port", type=int, default=HER_PORT,
                   help="her backend port: a real scrub refuses while her process listens on it")
    a = ap.parse_args()
    if a.cmd == "rotate":
        rotate(Path(a.vault_dir)); return 0

    if not a.retired and not a.bearer:
        raise SystemExit("scrub-old needs --retired (the old keys), --bearer, or both")
    repo = Path(a.repo)
    literals: list[str] = []
    if a.retired:
        retired = Path(a.retired)
        # Refuse anything that isn't plainly a retired-<stamp> dir holding the old
        # vault — a wrong --retired (the live vault dir, an arbitrary folder)
        # would otherwise happily scrub the wrong literals from everything.
        if not retired.name.startswith("retired-"):
            raise SystemExit(f"--retired must be a 'retired-<stamp>' directory, got {retired.name!r}")
        if not (retired / "credentials.kdbx").exists():
            raise SystemExit(f"--retired {retired} does not contain credentials.kdbx; refusing")

        literals = [(retired / n).read_text(encoding="utf-8", errors="ignore").strip() for n in (".passphrase", "vault.key")]
        literals = [l for l in literals if len(l) >= 8]
    print(f"scrub-old: {len(literals)} literal(s), lengths {sorted(len(l) for l in literals)}"
          f"{'; bearer tokens: on' if a.bearer else ''}")
    if a.dry_run:
        print("DRY RUN: counting only, nothing is written")
    else:
        # Second read F6 (2026-10-04): never rewrite her records under her.
        # data/agent_sessions is written by her running process, and so are
        # her logs and moments: with the atomic rewrite, a handler that holds
        # a log open would go on writing to the replaced (unlinked) file, and
        # a session append between the read and the replace would be lost.
        # So a real run waits for her to be stopped; a dry run writes nothing
        # and still counts. Checked before anything is touched.
        her = _her_pid(repo, a.port)
        if her is not None:
            raise SystemExit(
                f"her process (pid {her}) is listening on :{a.port} from {repo} — "
                "data/agent_sessions and her logs are hers while she runs. Stop her "
                "first, or use --dry-run to count without writing.")

    # Printed per glob (not just the combined total) so a wrong --repo shows
    # zeros loudly instead of a single quiet-looking combined count.
    glob_patterns = (
        "data/agent_sessions/**/*.jsonl",
        "data/agent_sessions/**/tool_results/*.txt",
        "logs/palli*.log*",
        "data/present_moment/*.jsonl",
        "data/inner_monologue/*.jsonl",
    )
    # The db is required unless explicitly skipped — a missing db used to
    # silently become db=None, so running against a wrong --repo (one with no
    # db at all) printed zero counts and exited 0, indistinguishable from a
    # genuinely clean scrub. Checked before any file is touched.
    db_path = repo / "data" / "palli-local.sqlite3"
    if a.no_db:
        print("DB: SKIPPED (--no-db)")
        db_arg = None
    elif not db_path.exists():
        raise SystemExit(f"DB not found at {db_path} — pass --no-db to skip it, or check --repo")
    else:
        db_arg = db_path

    # Scrubbed glob by glob (A4) so the counts — matches, not just files —
    # are printed per glob, in a dry run and a real one alike.
    verb = "would change" if a.dry_run else "changed"
    counts: dict = {}

    def _add(part: dict) -> None:
        for k, v in part.items():
            counts[k] = counts.get(k, 0) + v

    for pattern in glob_patterns:
        matched = sorted(repo.glob(pattern))
        part = scrub(matched, literals, ssn=a.ssn, db=None, bearer=a.bearer, dry_run=a.dry_run)
        print(f"glob {pattern!r}: {len(matched)} file(s) — bearer {part['bearer']}, "
              f"literals {part['literals']}, ssn {part['ssn']}; {verb} {part['files']} file(s)")
        _add(part)
    if db_arg is not None:
        part = scrub([], literals, ssn=a.ssn, db=db_arg, bearer=a.bearer, dry_run=a.dry_run)
        print(f"db: {part['db_rows']} row(s) {verb} — bearer {part['bearer']}, "
              f"literals {part['literals']}, ssn {part['ssn']}")
        _add(part)
    print(f"compressed: {counts['compressed_skipped']} file(s) skipped (never rewritten; "
          "scrub before retention zips a log)")
    print({k: v for k, v in counts.items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
