"""Task 8 — the rotation-and-scrub script.

Runs under system python3 only: pykeepass lives there, not in venv/. This file is
SKIPPED under the venv (that is the expected gate behaviour) and run by hand under
system python3 by the wrap that follows the owner's restart (Task 10). Tests use
tmp dirs and fake passphrases only — never the real ~/.palli/ vault.

Fix round 1 (2026-09-26) added: the crash-window swap (write+fsync all *.new
files before any move, verify-open re-reads the passphrase from disk, retire+
promote wrapped so a failure anywhere rolls all the way back), the expanded
DB scrub scope (memory_index_items/_fts, conversation_compactions, agent_steps),
recursive file globs, and the scrub-old CLI's small extras (mode=rw connect,
--retired name/shape refusal, printing lengths only).

Fix round 2 added: leftover *.new/*.tmp cleanup moved to AFTER proving the
live set opens on its own (round 1's cleanup-at-start reintroduced the exact
defect the script exists to prevent — a crash mid-swap left the ONLY working
key material sitting in the *.new files, which the next run then deleted); a
required db unless --no-db (a missing db used to silently become db=None,
printing zero counts and exiting 0 — indistinguishable from a clean scrub);
per-glob file counts; and the expanded scrub scope (memory_index_items.snippet,
monologue_memory_fts.text_content, user_memory.content) with FTS5 'optimize'
after the updates and a corrected (non-vacuous) FTS MATCH test.
"""
import pytest
pk = pytest.importorskip("pykeepass")

import fcntl
import gzip
import os
import sqlite3
import stat
import sys
from pathlib import Path

import scripts.rotate_vault_keys as rvk
from scripts.rotate_vault_keys import rotate, scrub

OLD_PW = "old-passphrase-abcdefghijklmnop"


def _fresh_vault(tmp_path, name="vault"):
    v = tmp_path / name; v.mkdir()
    (v / ".passphrase").write_text(OLD_PW + "\n"); (v / "vault.key").write_text("b" * 64 + "\n")
    kp = pk.create_database(str(v / "credentials.kdbx"), password=OLD_PW, keyfile=str(v / "vault.key"))
    kp.add_entry(kp.root_group, "t", "u", "p"); kp.save()
    return v


def _opens_with_old_keys(v: Path) -> bool:
    try:
        kp = pk.PyKeePass(str(v / "credentials.kdbx"), password=OLD_PW, keyfile=str(v / "vault.key"))
    except Exception:
        return False
    return len(kp.entries) == 1


def _no_new_files_left(v: Path) -> bool:
    return not any(
        (v / n).exists()
        for n in ("credentials.kdbx.new", "vault.key.new", ".passphrase.new", "credentials.kdbx.tmp")
    )


# --------------------------------------------------------------------------
# rotate() — the happy path
# --------------------------------------------------------------------------

def test_rotate_keeps_entries_and_retires_the_old_keys(tmp_path):
    v = _fresh_vault(tmp_path)
    retired = rotate(v)
    new_pw = (v / ".passphrase").read_text().strip()
    kp = pk.PyKeePass(str(v / "credentials.kdbx"), password=new_pw, keyfile=str(v / "vault.key"))
    assert len(kp.entries) == 1 and new_pw != OLD_PW
    with pytest.raises(pk.exceptions.CredentialsError):
        pk.PyKeePass(str(v / "credentials.kdbx"), password=OLD_PW, keyfile=str(retired / "vault.key"))
    assert (retired / "credentials.kdbx").exists() and (retired / ".passphrase").exists()


def test_rotate_moves_backups_and_sets_final_modes_and_keeps_the_lock(tmp_path):
    v = _fresh_vault(tmp_path)
    backups = v / "backups"; backups.mkdir()
    (backups / "old-backup.kdbx").write_text("stand-in backup content")

    retired = rotate(v)

    assert (retired / "backups" / "old-backup.kdbx").exists()
    assert not (v / "backups").exists()

    for name in ("credentials.kdbx", ".passphrase", "vault.key"):
        mode = stat.S_IMODE(os.stat(v / name).st_mode)
        assert mode == 0o600, f"{name} is {oct(mode)}, expected 0o600"
    assert stat.S_IMODE(os.stat(retired).st_mode) == 0o700

    assert (v / ".lock").exists()
    assert _no_new_files_left(v)


# --------------------------------------------------------------------------
# rotate() — the vault lock
# --------------------------------------------------------------------------

def test_rotate_exits_without_touching_anything_when_the_vault_is_locked(tmp_path, monkeypatch):
    """Controller ruling: rotate() takes an EXCLUSIVE flock on <vault_dir>/.lock
    for the whole run, with a bounded wait — her vault saves and the nightly
    backup take the same lock. A lock already held (by "someone else") must make
    rotate() give up cleanly, with nothing swapped and nothing retired."""
    monkeypatch.setattr(rvk, "_LOCK_WAIT_SECONDS", 0.05)
    monkeypatch.setattr(rvk, "_LOCK_POLL_SECONDS", 0.01)

    v = _fresh_vault(tmp_path)
    lock_path = v / ".lock"
    lock_path.touch()
    holder = open(lock_path, "r+")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
    try:
        before_kdbx = (v / "credentials.kdbx").read_bytes()
        before_pw = (v / ".passphrase").read_text()
        before_kf = (v / "vault.key").read_text()
        with pytest.raises(SystemExit) as excinfo:
            rvk.rotate(v)
        assert excinfo.value.code not in (0, None)
        assert (v / "credentials.kdbx").read_bytes() == before_kdbx
        assert (v / ".passphrase").read_text() == before_pw
        assert (v / "vault.key").read_text() == before_kf
        assert not any(p.name.startswith("retired-") for p in v.iterdir())
        assert _no_new_files_left(v)
    finally:
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()


# --------------------------------------------------------------------------
# rotate() — the crash window: swap failures roll all the way back
# --------------------------------------------------------------------------

def _fail_replace_on(target: Path):
    """Patch Path.replace so the call whose SOURCE is exactly `target` raises —
    every other Path.replace call (including any inside pykeepass's own save())
    behaves normally. Matching on the source path, not a call index, means this
    is immune to any library-internal replace() calls happening earlier."""
    original = Path.replace
    target_str = str(target)

    def flaky(self, other):
        if str(self) == target_str:
            raise OSError(f"simulated failure moving {self}")
        return original(self, other)

    return flaky


@pytest.mark.parametrize("which", ["kdbx", "pwf", "kff", "backups", "new_kdbx", "new_kf", "new_pwf"])
def test_rotate_rolls_back_fully_if_the_swap_fails_partway(tmp_path, monkeypatch, which):
    v = _fresh_vault(tmp_path)
    backups = v / "backups"; backups.mkdir()
    (backups / "old-backup.kdbx").write_text("stand-in backup content")

    target = {
        "kdbx": v / "credentials.kdbx",
        "pwf": v / ".passphrase",
        "kff": v / "vault.key",
        "backups": backups,
        "new_kdbx": v / "credentials.kdbx.new",
        "new_kf": v / "vault.key.new",
        "new_pwf": v / ".passphrase.new",
    }[which]

    monkeypatch.setattr(Path, "replace", _fail_replace_on(target))

    with pytest.raises(OSError):
        rotate(v)

    # The vault must end up either fully old or fully new — this rollback
    # always lands on fully old.
    assert _opens_with_old_keys(v), "the live vault must still open with SOME complete key set"
    assert _no_new_files_left(v), "no *.new file may be left behind"
    assert (v / ".lock").exists()
    assert (v / "backups").is_dir() and (v / "backups" / "old-backup.kdbx").exists(), \
        "backups/ must be back at its original place, not stranded in retired/"
    assert not any(p.name.startswith("retired-") for p in v.iterdir()), \
        "a fully rolled-back run leaves no half-used retired-<stamp> dir behind"


def test_rotate_removes_new_files_when_the_entry_count_check_fails(tmp_path, monkeypatch):
    """'A failed verify leaves no *.new files.' Forces the entry-count check to
    disagree (a lying wrapper around the real, legitimately-saved new vault, not
    a corrupted file) so the SystemExit path — not a Path.replace failure — is
    what's under test here."""
    import pykeepass

    v = _fresh_vault(tmp_path)
    real_cls = pykeepass.PyKeePass

    class _LiesAboutEntries:
        def __init__(self, inner):
            self._inner = inner

        @property
        def entries(self):
            return list(self._inner.entries) + [object()]

    def fake_pykeepass(path, *args, **kwargs):
        inner = real_cls(path, *args, **kwargs)
        if str(path).endswith("credentials.kdbx.new"):
            return _LiesAboutEntries(inner)
        return inner

    monkeypatch.setattr(pykeepass, "PyKeePass", fake_pykeepass)

    before_kdbx = (v / "credentials.kdbx").read_bytes()
    with pytest.raises(SystemExit) as excinfo:
        rotate(v)
    assert excinfo.value.code not in (0, None)

    assert (v / "credentials.kdbx").read_bytes() == before_kdbx
    assert _no_new_files_left(v)
    assert _opens_with_old_keys(v)
    assert not any(p.name.startswith("retired-") for p in v.iterdir())


# --------------------------------------------------------------------------
# rotate() — fix round 2, item 1: a crash mid-swap on a PREVIOUS run must
# leave rotate() refusing to touch anything, not "helpfully" cleaning up the
# only key material that can currently open the live vault.
# --------------------------------------------------------------------------

NEW_PW = "new-passphrase-0123456789abcdefghij"


def _build_new_material(v: Path) -> str:
    """Replicates rotate()'s own pre-swap steps (write vault.key.new, save a
    real credentials.kdbx.new under the new keys, write .passphrase.new) so
    the resulting *.new files are a genuine, openable key set — the same
    shape rotate() itself produces before it ever touches a live file."""
    kp = pk.PyKeePass(str(v / "credentials.kdbx"), password=OLD_PW, keyfile=str(v / "vault.key"))
    new_kf = v / "vault.key.new"
    new_kf.write_text("c" * 64 + "\n")
    kp.password, kp.keyfile = NEW_PW, str(new_kf)
    kp.save(filename=str(v / "credentials.kdbx.new"))
    (v / ".passphrase.new").write_text(NEW_PW + "\n")
    return NEW_PW


def _advance_to_step(v: Path, retired_name: str, stop_after: str) -> Path:
    """Manually replays rotate()'s own retire-then-promote sequence, in the
    SAME order rotate() itself uses, stopping at the exact point named:
      "retire_kdbx"  — step 6: only credentials.kdbx has been retired.
      "promote_kdbx" — step 9: all three retired; credentials.kdbx promoted.
      "promote_kf"   — step 10: ...and vault.key also promoted.
    This is what a real SIGKILL-right-after-that-replace() leaves on disk —
    built directly rather than via a subprocess + os._exit, per the ruling's
    "or construct the files directly" option.
    """
    _build_new_material(v)
    kdbx, pwf, kff = v / "credentials.kdbx", v / ".passphrase", v / "vault.key"
    new_kdbx, new_kf, new_pwf = v / "credentials.kdbx.new", v / "vault.key.new", v / ".passphrase.new"
    retired = v / retired_name
    retired.mkdir(mode=0o700)

    kdbx.replace(retired / "credentials.kdbx")
    if stop_after == "retire_kdbx":
        return retired
    pwf.replace(retired / ".passphrase")
    kff.replace(retired / "vault.key")

    new_kdbx.replace(kdbx)
    if stop_after == "promote_kdbx":
        return retired
    new_kf.replace(kff)
    if stop_after == "promote_kf":
        return retired
    raise ValueError(stop_after)


def _snapshot(v: Path) -> dict:
    return {str(p.relative_to(v)): p.read_bytes() for p in v.rglob("*") if p.is_file()}


def test_rotate_refuses_and_deletes_nothing_after_a_crash_mid_retire(tmp_path):
    """Step 6 (crash_probe step_src='credentials.kdbx'): only kdbx has been
    retired; .passphrase/vault.key are still live and OLD; all three *.new
    files are a complete, already-verified new set. The old (round 1) cleanup
    ran unconditionally at the top of rotate() and would have deleted that
    complete new set for no reason — it must instead refuse and leave
    everything exactly as it is."""
    v = _fresh_vault(tmp_path)
    (v / ".lock").touch()  # an ordinary pre-existing lock file, not under test
    retired = _advance_to_step(v, "retired-20260101-000000", "retire_kdbx")
    before = _snapshot(v)

    with pytest.raises(SystemExit) as excinfo:
        rotate(v)
    assert excinfo.value.code not in (0, None)

    assert _snapshot(v) == before, "an interrupted rotation must have nothing deleted or modified"
    # the OLD generation is still fully reconstructable from what's on disk
    kp = pk.PyKeePass(str(retired / "credentials.kdbx"), password=OLD_PW, keyfile=str(v / "vault.key"))
    assert len(kp.entries) == 1


def test_rotate_refuses_and_deletes_nothing_after_a_crash_right_after_promoting_kdbx(tmp_path):
    """Step 9: the live credentials.kdbx is already the NEW one; the ONLY
    thing on disk that can currently open it is .passphrase.new + vault.key.new
    (both still present, unconsumed). The old cleanup would have deleted
    exactly those two files and left an unrecoverable vault — the reviewer's
    exact finding, proven with an os._exit probe."""
    v = _fresh_vault(tmp_path)
    (v / ".lock").touch()
    _advance_to_step(v, "retired-20260101-000000", "promote_kdbx")
    before = _snapshot(v)

    with pytest.raises(SystemExit) as excinfo:
        rotate(v)
    assert excinfo.value.code not in (0, None)

    assert _snapshot(v) == before

    # exactly the combination the ruling names as the only way to open it
    kp = pk.PyKeePass(str(v / "credentials.kdbx"), password=NEW_PW, keyfile=str(v / "vault.key.new"))
    assert len(kp.entries) == 1


def test_rotate_refuses_and_deletes_nothing_after_a_crash_right_after_promoting_kf(tmp_path):
    """Step 10: live credentials.kdbx AND live vault.key are already the NEW
    ones; only .passphrase.new (still present, unconsumed) completes the set."""
    v = _fresh_vault(tmp_path)
    (v / ".lock").touch()
    _advance_to_step(v, "retired-20260101-000000", "promote_kf")
    before = _snapshot(v)

    with pytest.raises(SystemExit) as excinfo:
        rotate(v)
    assert excinfo.value.code not in (0, None)

    assert _snapshot(v) == before

    kp = pk.PyKeePass(str(v / "credentials.kdbx"), password=NEW_PW, keyfile=str(v / "vault.key"))
    assert len(kp.entries) == 1


def test_rotate_prints_diagnostics_when_refusing(tmp_path, capsys):
    v = _fresh_vault(tmp_path)
    (v / ".lock").touch()
    _advance_to_step(v, "retired-20260101-000000", "promote_kdbx")

    with pytest.raises(SystemExit) as excinfo:
        rotate(v)
    assert excinfo.value.code not in (0, None)

    out = capsys.readouterr().out
    assert "credentials.kdbx" in out  # the names of what's present in the vault dir
    assert "retired-20260101-000000" in out
    assert "nothing was changed" in out
    assert NEW_PW not in out and OLD_PW not in out  # never a value


# --------------------------------------------------------------------------
# scrub() — file scope
# --------------------------------------------------------------------------

def test_scrub_replaces_old_literals_and_ssns(tmp_path):
    f = tmp_path / "s.jsonl"; f.write_text('{"t": "old-passphrase-abcdefghijklmnop and 123-45-6789"}\n')
    counts = scrub([f], ["old-passphrase-abcdefghijklmnop"], ssn=True, db=None)
    text = f.read_text()
    assert "old-passphrase" not in text and "123-45-6789" not in text and "[REDACTED]" in text and "[MASKED:ssn]" in text
    assert counts["literals"] == 1 and counts["ssn"] == 1


def test_scrub_skips_compressed_files(tmp_path):
    """R7 (controller ruling): a byte-level rewrite of a compressed file corrupts
    it, so scrub() must recognize the extension and leave the bytes untouched —
    not even attempt to decode them as text."""
    f = tmp_path / "s.jsonl.gz"
    payload = b'{"t": "old-passphrase-abcdefghijklmnop and 123-45-6789"}\n'
    with gzip.open(f, "wb") as fh:
        fh.write(payload)
    before = f.read_bytes()
    counts = scrub([f], ["old-passphrase-abcdefghijklmnop"], ssn=True, db=None)
    after = f.read_bytes()
    assert after == before, "a .gz file must be left byte-identical"
    assert counts["files"] == 0 and counts["literals"] == 0 and counts["ssn"] == 0


def test_fullwidth_digit_ssn_shape_is_not_masked_by_scrub():
    """Same exact-grammar fix as core/agent/payload_masking.py: `[0-9]`, not
    `\\d`, so fullwidth Unicode digits are not mistaken for an SSN."""
    fullwidth_ssn = "１２３-４５-６７８９"
    assert rvk.SSN.search(fullwidth_ssn) is None


# --------------------------------------------------------------------------
# scrub() — DB scope (fix round 1, item 2)
# --------------------------------------------------------------------------

_SECRET = "old-passphrase-abcdefghijklmnop"
_N_FTS_ROWS = 5  # per-row commits below; see the note in the FTS test for why


def _build_test_db(path: Path) -> None:
    con = sqlite3.connect(str(path))
    try:
        con.executescript(
            """
            CREATE TABLE conversations (id INTEGER PRIMARY KEY, content TEXT);
            CREATE TABLE identity_document_versions (id INTEGER PRIMARY KEY, content TEXT);
            CREATE TABLE memory_index_items (item_id TEXT PRIMARY KEY, content TEXT, snippet TEXT);
            CREATE VIRTUAL TABLE memory_index_fts USING fts5(
                owner_user_id UNINDEXED, item_id UNINDEXED, source_kind UNINDEXED,
                title, content, tags
            );
            CREATE TABLE conversation_compactions (compaction_id TEXT PRIMARY KEY, summary_text TEXT);
            CREATE TABLE agent_steps (id INTEGER PRIMARY KEY, observation TEXT, action_input TEXT);
            CREATE VIRTUAL TABLE monologue_memory_fts USING fts5(
                entry_id UNINDEXED, source_kind, source_path UNINDEXED, timestamp UNINDEXED,
                text_content, sentiment_tag, surfaced_candidate, salience UNINDEXED,
                tokenize = 'porter unicode61'
            );
            CREATE TABLE user_memory (id INTEGER PRIMARY KEY, content TEXT);
            -- a table NOT in _DB_TEXT_COLUMNS at all, to prove untouched tables are left alone
            CREATE TABLE untouched (id INTEGER PRIMARY KEY, content TEXT);
            """
        )
        secret = _SECRET
        con.execute("INSERT INTO conversations (content) VALUES (?)", (f"said {secret} once",))
        con.execute("INSERT INTO identity_document_versions (content) VALUES (?)", (f"stored {secret}",))
        con.execute(
            "INSERT INTO memory_index_items (item_id, content, snippet) VALUES ('i1', ?, ?)",
            (f"remembered {secret} in full", f"remembered {secret}"),
        )
        con.execute("INSERT INTO conversation_compactions (compaction_id, summary_text) VALUES ('c1', ?)", (f"summary of {secret}",))
        con.execute("INSERT INTO agent_steps (observation, action_input) VALUES (?, ?)", (f"saw {secret}", f'{{"v": "{secret}"}}'))
        con.execute("INSERT INTO user_memory (content) VALUES (?)", (f"noted {secret}",))
        con.execute("INSERT INTO untouched (content) VALUES (?)", (f"also has {secret} but is not in scope",))
        con.commit()
        # One INSERT + COMMIT per row (not one batch) — this is the shape that
        # leaves the old literal physically present in an un-merged FTS5
        # segment after the UPDATE, which is exactly what 'optimize' has to
        # clean up. A single bulk insert+commit does not reproduce it (there
        # was never more than one segment to begin with).
        for i in range(_N_FTS_ROWS):
            con.execute(
                "INSERT INTO memory_index_fts (owner_user_id, item_id, source_kind, title, content, tags) "
                "VALUES (1, ?, 'k', 'T', ?, 'tag')",
                (f"i{i}", f"remembered {secret} item {i}"),
            )
            con.commit()
            con.execute(
                "INSERT INTO monologue_memory_fts (entry_id, source_kind, text_content, sentiment_tag, surfaced_candidate) "
                "VALUES (?, 'k', ?, 's', 0)",
                (f"e{i}", f"felt {secret} moment {i}"),
            )
            con.commit()
    finally:
        con.close()


def test_scrub_db_covers_the_expanded_table_scope_and_keeps_the_fts_index_consistent(tmp_path):
    db = tmp_path / "app.sqlite3"
    _build_test_db(db)
    secret = _SECRET
    # FTS phrase query: the literal's hyphens become spaces, matching the
    # tokenizer's own word boundaries — a query for the hyphens-stripped run
    # glued together (what round 1's test did) never matches anything and
    # would have passed on a completely broken scrub.
    phrase = '"' + secret.replace("-", " ") + '"'

    con = sqlite3.connect(str(db))
    before_fts_hits = con.execute("SELECT rowid FROM memory_index_fts WHERE memory_index_fts MATCH ?", (phrase,)).fetchall()
    before_mono_hits = con.execute("SELECT rowid FROM monologue_memory_fts WHERE monologue_memory_fts MATCH ?", (phrase,)).fetchall()
    con.close()
    assert before_fts_hits and before_mono_hits, "the fixture must actually contain the literal before scrubbing"

    counts = scrub([], [secret], ssn=False, db=db)

    con = sqlite3.connect(str(db))
    try:
        for table, col in (
            ("conversations", "content"),
            ("identity_document_versions", "content"),
            ("memory_index_items", "content"),
            ("memory_index_items", "snippet"),
            ("conversation_compactions", "summary_text"),
            ("user_memory", "content"),
        ):
            (value,) = con.execute(f"SELECT {col} FROM {table}").fetchone()
            assert secret not in value and "[REDACTED]" in value, f"{table}.{col} was not scrubbed"

        observation, action_input = con.execute("SELECT observation, action_input FROM agent_steps").fetchone()
        assert secret not in observation and "[REDACTED]" in observation
        assert secret not in action_input and "[REDACTED]" in action_input

        # the FTS5 index itself: a hit before, no hit after, on both tables
        after_fts_hits = con.execute("SELECT rowid FROM memory_index_fts WHERE memory_index_fts MATCH ?", (phrase,)).fetchall()
        after_mono_hits = con.execute("SELECT rowid FROM monologue_memory_fts WHERE monologue_memory_fts MATCH ?", (phrase,)).fetchall()
        assert after_fts_hits == [], "memory_index_fts still MATCHes the old literal"
        assert after_mono_hits == [], "monologue_memory_fts still MATCHes the old literal"

        # a table outside the named scope is left untouched
        (untouched_value,) = con.execute("SELECT content FROM untouched").fetchone()
        assert secret in untouched_value
    finally:
        con.close()

    # Not just the query layer: the raw db FILE must not contain a leftover
    # copy of the old literal's bytes anywhere else, including a fragment left
    # in an un-merged FTS5 segment. This is what 'optimize' fixes — without
    # it, this is exactly what the reviewer's probe caught (2 raw hits of
    # "abcdefghijklmnop" before optimize, 0 after). Exactly ONE occurrence is
    # expected to remain: the `untouched` table's row, deliberately left alone
    # and asserted above — a blanket zero-count would conflict with proving
    # that row is untouched, so this counts instead of forbidding outright.
    raw = db.read_bytes()
    assert raw.count(secret.encode()) == 1
    assert raw.count(b"abcdefghijklmnop") == 1

    # 6 single-column tables/rows + agent_steps' 2 columns + N rows in each of
    # the two FTS5 tables
    assert counts["db_rows"] == 6 + 2 + _N_FTS_ROWS + _N_FTS_ROWS


def test_scrub_skips_absent_tables_and_columns_gracefully(tmp_path):
    """A db with only SOME of the named tables must not raise — PRAGMA table_info
    on a table that isn't there returns nothing, and that pair is skipped."""
    db = tmp_path / "partial.sqlite3"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE conversations (id INTEGER PRIMARY KEY, content TEXT)")
    con.execute("INSERT INTO conversations (content) VALUES ('has old-passphrase-abcdefghijklmnop in it')")
    con.commit(); con.close()

    counts = scrub([], ["old-passphrase-abcdefghijklmnop"], ssn=False, db=db)
    assert counts["db_rows"] == 1

    con = sqlite3.connect(str(db))
    (value,) = con.execute("SELECT content FROM conversations").fetchone()
    con.close()
    assert "old-passphrase" not in value


def test_scrub_never_creates_an_empty_db(tmp_path):
    """Small extra: scrub-old opens the db with mode=rw so a missing/typo'd path
    fails loudly instead of silently scrubbing nothing against a fresh empty db."""
    missing = tmp_path / "does-not-exist.sqlite3"
    with pytest.raises(sqlite3.OperationalError):
        scrub([], ["whatever"], ssn=False, db=missing)
    assert not missing.exists()


# --------------------------------------------------------------------------
# main() / CLI — small extras, exercised only against tmp dirs
# --------------------------------------------------------------------------

def _run_main(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["rotate_vault_keys.py", *argv])
    return rvk.main()


def test_scrub_old_refuses_a_retired_dir_with_the_wrong_name(tmp_path, monkeypatch):
    bad = tmp_path / "not-a-retired-dir"; bad.mkdir()
    (bad / "credentials.kdbx").write_text("stand-in")
    with pytest.raises(SystemExit) as excinfo:
        _run_main(monkeypatch, ["scrub-old", "--retired", str(bad), "--repo", str(tmp_path)])
    assert excinfo.value.code not in (0, None)


def test_scrub_old_refuses_a_retired_dir_without_credentials_kdbx(tmp_path, monkeypatch):
    bad = tmp_path / "retired-20260101-000000"; bad.mkdir()
    with pytest.raises(SystemExit) as excinfo:
        _run_main(monkeypatch, ["scrub-old", "--retired", str(bad), "--repo", str(tmp_path)])
    assert excinfo.value.code not in (0, None)


def test_scrub_old_prints_lengths_not_values(tmp_path, monkeypatch, capsys):
    retired = tmp_path / "retired-20260101-000000"; retired.mkdir()
    (retired / "credentials.kdbx").write_text("stand-in")
    (retired / ".passphrase").write_text(OLD_PW + "\n")
    (retired / "vault.key").write_text(("k" * 64) + "\n")
    repo = tmp_path / "repo"; repo.mkdir()

    _run_main(monkeypatch, ["scrub-old", "--retired", str(retired), "--repo", str(repo), "--no-db"])

    out = capsys.readouterr().out
    assert OLD_PW not in out and ("k" * 64) not in out
    assert "2 literal" in out
    assert str(len(OLD_PW)) in out and "64" in out


def test_scrub_old_globs_are_recursive_under_agent_sessions(tmp_path, monkeypatch):
    retired = tmp_path / "retired-20260101-000000"; retired.mkdir()
    (retired / "credentials.kdbx").write_text("stand-in")
    (retired / ".passphrase").write_text(OLD_PW + "\n")
    (retired / "vault.key").write_text(("k" * 64) + "\n")

    repo = tmp_path / "repo"
    nested = repo / "data" / "agent_sessions" / "2026-09-26" / "sessionA"
    nested.mkdir(parents=True)
    (nested / "turns.jsonl").write_text(f'{{"t": "{OLD_PW}"}}\n')
    tool_results = nested / "tool_results"; tool_results.mkdir()
    (tool_results / "out.txt").write_text(f"contains {OLD_PW} in a tool result\n")

    _run_main(monkeypatch, ["scrub-old", "--retired", str(retired), "--repo", str(repo), "--no-db"])

    assert OLD_PW not in (nested / "turns.jsonl").read_text()
    assert OLD_PW not in (tool_results / "out.txt").read_text()


# --------------------------------------------------------------------------
# main() / CLI — fix round 2, item 2: the db is required unless --no-db
# --------------------------------------------------------------------------

def _retired_fixture(tmp_path) -> Path:
    retired = tmp_path / "retired-20260101-000000"; retired.mkdir()
    (retired / "credentials.kdbx").write_text("stand-in")
    (retired / ".passphrase").write_text(OLD_PW + "\n")
    (retired / "vault.key").write_text(("k" * 64) + "\n")
    return retired


def test_scrub_old_requires_the_db_unless_no_db_is_given(tmp_path, monkeypatch):
    """A wrong --repo (one with no db at all) used to silently become db=None,
    print zero counts, and exit 0 — indistinguishable from a genuinely clean
    scrub. It must now be a loud, non-zero failure instead."""
    retired = _retired_fixture(tmp_path)
    repo = tmp_path / "repo"; repo.mkdir()  # deliberately no data/palli-local.sqlite3

    with pytest.raises(SystemExit) as excinfo:
        _run_main(monkeypatch, ["scrub-old", "--retired", str(retired), "--repo", str(repo)])
    assert excinfo.value.code not in (0, None)


def test_scrub_old_no_db_flag_skips_cleanly_and_says_so(tmp_path, monkeypatch, capsys):
    retired = _retired_fixture(tmp_path)
    repo = tmp_path / "repo"; repo.mkdir()

    rc = _run_main(monkeypatch, ["scrub-old", "--retired", str(retired), "--repo", str(repo), "--no-db"])

    assert rc == 0
    assert "DB: SKIPPED (--no-db)" in capsys.readouterr().out


def test_scrub_old_prints_a_count_per_glob(tmp_path, monkeypatch, capsys):
    """So a wrong --repo shows zeros loudly instead of one quiet combined total."""
    retired = _retired_fixture(tmp_path)
    repo = tmp_path / "repo"
    sess = repo / "data" / "agent_sessions"; sess.mkdir(parents=True)
    (sess / "a.jsonl").write_text("x"); (sess / "b.jsonl").write_text("y")

    _run_main(monkeypatch, ["scrub-old", "--retired", str(retired), "--repo", str(repo), "--no-db"])

    out = capsys.readouterr().out
    assert "'data/agent_sessions/**/*.jsonl': 2 file(s)" in out
    assert "'logs/palli*.log*': 0 file(s)" in out
    assert "'data/present_moment/*.jsonl': 0 file(s)" in out


# --------------------------------------------------------------------------
# Batch 8, task 5 (ruling T5-5): once retention gzips the logs, the scrub
# still never rewrites a compressed file — and now says how many it skipped
# --------------------------------------------------------------------------

def test_scrub_counts_the_compressed_files_it_skipped(tmp_path):
    gz = tmp_path / "palli_verbose_2026-09-18.log._2026-09-21.gz"
    with gzip.open(gz, "wb") as fh:
        fh.write(OLD_PW.encode())
    before = gz.read_bytes()
    plain = tmp_path / "s.jsonl"; plain.write_text(f'{{"t": "{OLD_PW}"}}\n')
    counts = scrub([gz, plain], [OLD_PW], ssn=False, db=None)
    assert counts["compressed_skipped"] == 1 and counts["files"] == 1
    assert gz.read_bytes() == before


def test_scrub_old_says_how_many_compressed_files_it_skipped(tmp_path, monkeypatch, capsys):
    retired = _retired_fixture(tmp_path)
    repo = tmp_path / "repo"
    logs = repo / "logs"; logs.mkdir(parents=True)
    (logs / "palli_verbose_2026-09-18.log").write_text("plain\n")
    for day in ("2026-09-21", "2026-09-22"):
        with gzip.open(logs / f"palli_verbose_2026-09-18.log._{day}.gz", "wb") as fh:
            fh.write(b"zipped\n")

    _run_main(monkeypatch, ["scrub-old", "--retired", str(retired), "--repo", str(repo), "--no-db"])

    out = capsys.readouterr().out
    assert "compressed: 2 file(s) skipped" in out


# --------------------------------------------------------------------------
# Batch 8, task 11 (A4, ruling A4): the scrub learns `Bearer <token>`. The
# same file set (transcripts, tool_results/, logs, present moment, inner
# monologue) and the db; `--retired` optional when `--bearer` is given; a
# `--dry-run` that writes nothing and prints counts per glob. Every token
# below is a made-up stand-in.
# --------------------------------------------------------------------------

TOKEN = "fakeTOKEN0123456789abcdefXYZ._-~+/="          # 36 chars, the whole class
SHORT = "tooShort123"                                   # under 20: never touched


def test_scrub_redacts_a_bearer_token_and_only_when_asked(tmp_path):
    f = tmp_path / "s.jsonl"
    f.write_text(f'{{"h": "Authorization: Bearer {TOKEN}", "s": "Bearer {SHORT}"}}\n')

    # Off by default: today's scrub does not know the shape.
    counts = scrub([f], [], ssn=False, db=None)
    assert TOKEN in f.read_text() and counts["files"] == 0

    counts = scrub([f], [], ssn=False, db=None, bearer=True)
    text = f.read_text()
    assert TOKEN not in text
    assert "Authorization: Bearer [REDACTED]" in text
    assert f"Bearer {SHORT}" in text
    assert counts["bearer"] == 1 and counts["files"] == 1

    # Idempotent: the replacement is not a token.
    assert scrub([f], [], ssn=False, db=None, bearer=True)["bearer"] == 0


def test_scrub_dry_run_counts_and_writes_nothing(tmp_path):
    f = tmp_path / "out.txt"
    f.write_text(f"Bearer {TOKEN}\nagain: Bearer\t{TOKEN}\n")
    before = f.read_bytes()

    counts = scrub([f], [], ssn=False, db=None, bearer=True, dry_run=True)

    assert f.read_bytes() == before
    assert counts["bearer"] == 2 and counts["files"] == 1


def _bearer_repo(tmp_path) -> Path:
    repo = tmp_path / "repo"
    sess = repo / "data" / "agent_sessions" / "2026-09-29" / "sessionB"
    (sess / "tool_results").mkdir(parents=True)
    (sess / "turns.jsonl").write_text(f'{{"t": "curl -H \\"Authorization: Bearer {TOKEN}\\""}}\n')
    (sess / "tool_results" / "r.txt").write_text(f"Bearer {TOKEN}\nBearer {TOKEN}\n")
    logs = repo / "logs"; logs.mkdir()
    (logs / "palli_verbose_2026-09-23.log").write_text(f"x Bearer {TOKEN}\ny Bearer {SHORT}\n")
    return repo


def _bearer_files(repo: Path) -> list[Path]:
    return [
        repo / "data/agent_sessions/2026-09-29/sessionB/turns.jsonl",
        repo / "data/agent_sessions/2026-09-29/sessionB/tool_results/r.txt",
        repo / "logs/palli_verbose_2026-09-23.log",
    ]


def test_scrub_old_bearer_needs_no_retired_dir(tmp_path, monkeypatch, capsys):
    repo = _bearer_repo(tmp_path)

    rc = _run_main(monkeypatch, ["scrub-old", "--bearer", "--repo", str(repo), "--no-db"])

    assert rc == 0
    for p in _bearer_files(repo):
        assert TOKEN not in p.read_text(), p
        assert "Bearer [REDACTED]" in p.read_text(), p
    assert f"Bearer {SHORT}" in (repo / "logs/palli_verbose_2026-09-23.log").read_text()
    assert TOKEN not in capsys.readouterr().out


def test_scrub_old_bearer_dry_run_prints_counts_per_glob_and_writes_nothing(tmp_path, monkeypatch, capsys):
    repo = _bearer_repo(tmp_path)
    before = {p: p.read_bytes() for p in _bearer_files(repo)}

    rc = _run_main(monkeypatch, ["scrub-old", "--bearer", "--dry-run", "--repo", str(repo), "--no-db"])

    assert rc == 0
    assert {p: p.read_bytes() for p in _bearer_files(repo)} == before
    out = capsys.readouterr().out
    assert TOKEN not in out
    assert "DRY RUN" in out
    assert "'data/agent_sessions/**/*.jsonl': 1 file(s) — bearer 1" in out, out
    assert "'data/agent_sessions/**/tool_results/*.txt': 1 file(s) — bearer 2" in out, out
    assert "'logs/palli*.log*': 1 file(s) — bearer 1" in out, out
    assert "'data/present_moment/*.jsonl': 0 file(s) — bearer 0" in out, out


def test_scrub_old_bearer_dry_run_counts_db_rows_without_writing(tmp_path, monkeypatch, capsys):
    repo = _bearer_repo(tmp_path)
    db = repo / "data" / "palli-local.sqlite3"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE conversations (id INTEGER PRIMARY KEY, content TEXT)")
    con.execute("INSERT INTO conversations (content) VALUES (?)", (f"Bearer {TOKEN}",))
    con.execute("INSERT INTO conversations (content) VALUES (?)", ("nothing here",))
    con.commit(); con.close()
    before = db.read_bytes()

    _run_main(monkeypatch, ["scrub-old", "--bearer", "--dry-run", "--repo", str(repo)])

    assert db.read_bytes() == before
    out = capsys.readouterr().out
    assert "db: 1 row(s)" in out, out

    _run_main(monkeypatch, ["scrub-old", "--bearer", "--repo", str(repo)])
    con = sqlite3.connect(db)
    rows = [r[0] for r in con.execute("SELECT content FROM conversations ORDER BY id")]
    con.close()
    assert rows == ["Bearer [REDACTED]", "nothing here"]


def test_scrub_old_needs_retired_or_bearer(tmp_path, monkeypatch):
    repo = tmp_path / "repo"; repo.mkdir()
    with pytest.raises(SystemExit) as excinfo:
        _run_main(monkeypatch, ["scrub-old", "--repo", str(repo), "--no-db"])
    assert excinfo.value.code not in (0, None)


def test_scrub_old_retired_with_bearer_does_both(tmp_path, monkeypatch):
    retired = _retired_fixture(tmp_path)
    repo = _bearer_repo(tmp_path)
    log = repo / "logs/palli_verbose_2026-09-23.log"
    log.write_text(log.read_text() + f"old {OLD_PW}\n")

    _run_main(monkeypatch, ["scrub-old", "--retired", str(retired), "--bearer", "--repo", str(repo), "--no-db"])

    text = log.read_text()
    assert TOKEN not in text and OLD_PW not in text
