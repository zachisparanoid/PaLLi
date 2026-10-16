"""Secrets redaction from a denylist he controls (item 7c, 2026-08-19).

His desktop password sat in plaintext in her session transcript (37×), the
DB history, the verbose/debug logs (~190×) and one present-moment record —
and in the live payload to DeepSeek on every turn — because a secret typed
in Discord flows everywhere a message flows. data/secrets_denylist.txt
(one literal per line, his hand, mode 600, gitignored) is redacted at the
three chokepoints: her transcript (Session.append), the DB history rows
(core.turn.persistence), and every log record (a root-logger filter).
Empty denylist = nothing happens (the default; this is his list)."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from core import secrets_redaction as sr


def _deny(tmp_path, *literals):
    p = tmp_path / "secrets_denylist.txt"
    p.write_text("# comment line\n" + "\n".join(literals) + "\n", encoding="utf-8")
    return p


def test_redact_is_case_insensitive_and_keeps_everything_else(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "DENYLIST_PATH", _deny(tmp_path, "Hunter2Alpha", "cor-rect-horse"))
    out = sr.redact('the password is "hunter2alpha". and cor-rect-horse too; ok')
    assert out == 'the password is "[REDACTED]". and [REDACTED] too; ok'
    assert sr.redact("nothing here") == "nothing here"
    assert sr.redact("") == ""


def test_empty_or_missing_denylist_is_a_no_op(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "DENYLIST_PATH", tmp_path / "nope.txt")
    assert sr.redact("Hunter2Alpha") == "Hunter2Alpha"
    monkeypatch.setattr(sr, "DENYLIST_PATH", _deny(tmp_path))
    assert sr.redact("Hunter2Alpha") == "Hunter2Alpha"


def test_denylist_reloads_on_change(tmp_path, monkeypatch):
    p = _deny(tmp_path, "one")
    monkeypatch.setattr(sr, "DENYLIST_PATH", p)
    assert sr.redact("one two") == "[REDACTED] two"
    import os, time
    p.write_text("two\n", encoding="utf-8")
    st = p.stat(); os.utime(p, (st.st_atime + 5, st.st_mtime + 5))
    assert sr.redact("one two") == "one [REDACTED]"


def test_logging_filter_redacts_msg_and_args(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "DENYLIST_PATH", _deny(tmp_path, "Hunter2Alpha"))
    rec = logging.LogRecord("x", logging.INFO, __file__, 1, "raw content: %s (%s)", ("Hunter2Alpha", "fine"), None)
    assert sr.RedactingFilter().filter(rec) is True
    assert rec.getMessage() == "raw content: [REDACTED] (fine)"
    rec2 = logging.LogRecord("x", logging.INFO, __file__, 1, "plain Hunter2Alpha", None, None)
    sr.RedactingFilter().filter(rec2)
    assert rec2.getMessage() == "plain [REDACTED]"


@pytest.mark.asyncio
async def test_session_append_redacts_text_and_tool_results(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "DENYLIST_PATH", _deny(tmp_path, "Hunter2Alpha"))
    from core.agent.session import AgentMessage, Session, TextBlock, ToolResultBlock
    s = await Session.open_or_create("discord:1", root=tmp_path / "sessions")
    await s.append(AgentMessage(role="user", content=[TextBlock(text="pw is Hunter2Alpha ok")]))
    await s.append(AgentMessage(role="tool", content=[ToolResultBlock(tool_use_id="c1", content="echo Hunter2Alpha")]))
    assert s.messages[0].content[0].text == "pw is [REDACTED] ok"
    assert s.messages[1].content[0].content == "echo [REDACTED]"
    disk = (tmp_path / "sessions").rglob("*.jsonl")
    assert all("Hunter2Alpha" not in p.read_text(encoding="utf-8") for p in disk)


@pytest.mark.asyncio
async def test_persistence_redacts_history_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "DENYLIST_PATH", _deny(tmp_path, "Hunter2Alpha"))
    import core.turn.persistence as per
    import core.conversation_history_store as chs
    from core.turn.types import Turn, TurnMetadata
    written = []

    class _Store:
        def __init__(self, brain): pass
        async def store_conversation(self, **kw):
            written.append(kw)
    monkeypatch.setattr(chs, "ConversationHistoryStore", _Store)
    turn = Turn(surface="discord_text", owner_id=1, content="x", metadata=TurnMetadata(channel_id=5))
    await per._persist_user_side_with_content(turn, "my pw Hunter2Alpha")
    await per._persist_assistant_side_to_history(turn=turn, response_text="got Hunter2Alpha")
    assert written and all("Hunter2Alpha" not in str(w.get("content")) for w in written)
    assert "[REDACTED]" in str(written[0].get("content"))


# ─── the secrets she holds are redacted by construction (2026-09-17) ─────────
# The first Telegram boot logged her bot token in plain text twelve times:
# httpx logs every request URL at INFO, and Telegram's API puts the token IN
# the URL (api.telegram.org/bot<token>/getMe). The denylist is his hand and
# did not carry it. A credential the application itself holds is not a guess
# — it is a known literal — so every non-empty *_TOKEN / *_API_KEY / *_SECRET
# value on her config joins the redaction set without anyone typing it.


def _fresh(monkeypatch, tmp_path):
    import core.secrets_redaction as sr
    monkeypatch.setattr(sr, "DENYLIST_PATH", tmp_path / "denylist.txt")
    sr.reset_for_tests()
    return sr


def test_a_config_token_is_redacted_from_an_httpx_style_url(monkeypatch, tmp_path):
    sr = _fresh(monkeypatch, tmp_path)
    from core.config import config
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "1234567890:AAFakeTokenForTheTestOnly_abcDEF", raising=False)
    sr.reset_for_tests()
    url = 'HTTP Request: POST https://api.telegram.org/bot1234567890:AAFakeTokenForTheTestOnly_abcDEF/getMe "HTTP/1.1 200 OK"'
    out = sr.redact(url)
    assert "AAFakeTokenForTheTestOnly" not in out and "[REDACTED]" in out
    assert out.startswith("HTTP Request: POST https://api.telegram.org/bot")


def test_every_secret_bearing_config_value_is_covered(monkeypatch, tmp_path):
    sr = _fresh(monkeypatch, tmp_path)
    from core.config import config
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "sk-fake-deepseek-key-for-the-test", raising=False)
    monkeypatch.setattr(config, "DISCORD_TOKEN", "FakeDiscordTokenValue.abcdef.ghijkl", raising=False)
    monkeypatch.setattr(config, "JWT_SECRET", "a-fake-jwt-secret-that-is-long", raising=False)
    sr.reset_for_tests()
    text = "keys: sk-fake-deepseek-key-for-the-test / FakeDiscordTokenValue.abcdef.ghijkl / a-fake-jwt-secret-that-is-long"
    out = sr.redact(text)
    assert out == "keys: [REDACTED] / [REDACTED] / [REDACTED]"


def test_empty_or_short_config_values_never_redact_ordinary_text(monkeypatch, tmp_path):
    """A blank key or a placeholder like 'x' must not turn the filter into a
    text shredder."""
    sr = _fresh(monkeypatch, tmp_path)
    from core.config import config
    monkeypatch.setattr(config, "EXA_API_KEY", "", raising=False)
    monkeypatch.setattr(config, "OPENAI_API_KEY", "x", raising=False)
    monkeypatch.setattr(config, "VOICE_PASSIVE_POLICY", "quiet", raising=False)   # not a secret, despite the PASS in its name
    sr.reset_for_tests()
    assert sr.redact("x marks the quiet spot") == "x marks the quiet spot"


def test_the_logging_filter_redacts_a_config_token(monkeypatch, tmp_path):
    import logging
    sr = _fresh(monkeypatch, tmp_path)
    from core.config import config
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "1234567890:AAFakeTokenForTheTestOnly_abcDEF", raising=False)
    sr.reset_for_tests()
    rec = logging.LogRecord("httpx", logging.INFO, __file__, 1,
                            'HTTP Request: %s %s "%s"', ("POST", "https://api.telegram.org/bot1234567890:AAFakeTokenForTheTestOnly_abcDEF/getMe", "HTTP/1.1 200 OK"), None)
    assert sr.RedactingFilter().filter(rec) is True
    assert "AAFakeTokenForTheTestOnly" not in rec.getMessage() and "[REDACTED]" in rec.getMessage()


def test_the_logging_filter_redacts_non_string_args_like_httpx_url(monkeypatch, tmp_path):
    """httpx logs 'HTTP Request: %s %s "%s"' with the URL as an httpx.URL object,
    not a str — the 12:38 boot on 2026-09-17 still wrote the token eleven
    times after the first fix, because the filter only scrubbed str args."""
    import logging
    sr = _fresh(monkeypatch, tmp_path)
    from core.config import config
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "1234567890:AAFakeTokenForTheTestOnly_abcDEF", raising=False)
    sr.reset_for_tests()

    class FakeURL:
        def __str__(self):
            return "https://api.telegram.org/bot1234567890:AAFakeTokenForTheTestOnly_abcDEF/getMe"

    rec = logging.LogRecord("httpx", logging.INFO, __file__, 1, 'HTTP Request: %s %s "%s"',
                            ("POST", FakeURL(), "HTTP/1.1 200 OK"), None)
    assert sr.RedactingFilter().filter(rec) is True
    assert "AAFakeTokenForTheTestOnly" not in rec.getMessage() and "bot[REDACTED]/getMe" in rec.getMessage()


# ── a credential that is not on the config yet (first door, task 2, 2026-09-17) ──


def test_a_covered_value_is_redacted_only_inside_the_block(monkeypatch, tmp_path):
    """A brain key a stranger pastes on the setup page is not saved anywhere
    while it is being tested — that is the point, nothing is written until the
    probe passes — so the "every credential her own config holds" rule cannot
    reach it at the one moment it is travelling through a provider."""
    sr = _fresh(monkeypatch, tmp_path)

    assert sr.redact("key sk-test-not-a-real-key here") == "key sk-test-not-a-real-key here"
    with sr.covering("sk-test-not-a-real-key"):
        assert sr.redact("key sk-test-not-a-real-key here") == "key [REDACTED] here"
    assert sr.redact("key sk-test-not-a-real-key here") == "key sk-test-not-a-real-key here"


def test_the_cover_is_lifted_even_when_the_block_raises(monkeypatch, tmp_path):
    sr = _fresh(monkeypatch, tmp_path)

    with pytest.raises(RuntimeError):
        with sr.covering("sk-test-not-a-real-key"):
            raise RuntimeError("the probe blew up")

    assert sr.redact("sk-test-not-a-real-key") == "sk-test-not-a-real-key"


def test_two_callers_covering_the_same_value_do_not_uncover_it_early(monkeypatch, tmp_path):
    """Ref-counted: two requests can be testing the same key at once, and the
    first one to finish must not strip the cover off the second."""
    sr = _fresh(monkeypatch, tmp_path)

    outer = sr.covering("sk-test-not-a-real-key")
    inner = sr.covering("sk-test-not-a-real-key")
    outer.__enter__()
    inner.__enter__()
    inner.__exit__(None, None, None)
    assert sr.redact("sk-test-not-a-real-key") == "[REDACTED]"
    outer.__exit__(None, None, None)
    assert sr.redact("sk-test-not-a-real-key") == "sk-test-not-a-real-key"


def test_a_short_value_is_never_covered(monkeypatch, tmp_path):
    """The same floor the config secrets keep: the filter must never be able to
    shred ordinary text because somebody typed "x" in the key field."""
    sr = _fresh(monkeypatch, tmp_path)

    with sr.covering("x"):
        assert sr.redact("x marks the spot") == "x marks the spot"


def test_the_logging_filter_redacts_a_covered_value(monkeypatch, tmp_path):
    import logging
    sr = _fresh(monkeypatch, tmp_path)

    rec = logging.LogRecord("aiohttp", logging.INFO, __file__, 1, "POST %s", ("https://api.deepseek.com/v1?key=sk-test-not-a-real-key",), None)
    with sr.covering("sk-test-not-a-real-key"):
        assert sr.RedactingFilter().filter(rec) is True
    assert "sk-test-not-a-real-key" not in rec.getMessage()
    assert "[REDACTED]" in rec.getMessage()


# ── a credential that has just been PUT on the config (final wave, 2026-09-17) ──


def test_a_credential_put_on_the_config_is_redacted_once_refresh_says_so(monkeypatch, tmp_path):
    """The handover between the two rules, made explicit.

    The literal set is rebuilt lazily and nothing watches `config`, so a key
    the setup API has just saved was not redacted while the cache stayed warm
    — it became redacted only when some unrelated cache-key change came along
    (in the API's case `covering()`'s exit, one line later). `refresh()` is
    that caller saying so.
    """
    sr = _fresh(monkeypatch, tmp_path)
    from core.config import config
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "", raising=False)

    assert sr.redact("sk-test-not-a-real-key") == "sk-test-not-a-real-key"   # warms the cache

    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "sk-test-not-a-real-key", raising=False)
    sr.refresh()

    assert sr.redact("sk-test-not-a-real-key") == "[REDACTED]"


def test_refresh_also_lets_a_credential_go(monkeypatch, tmp_path):
    """The rollback direction: a failed swap puts the config back, and what is
    no longer a credential must stop shredding text that happens to match it."""
    sr = _fresh(monkeypatch, tmp_path)
    from core.config import config
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "sk-test-not-a-real-key", raising=False)
    sr.refresh()
    assert sr.redact("sk-test-not-a-real-key") == "[REDACTED]"

    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "", raising=False)
    sr.refresh()

    assert sr.redact("sk-test-not-a-real-key") == "sk-test-not-a-real-key"


def test_refresh_reads_nothing_and_leaves_the_pattern_to_the_next_caller(monkeypatch, tmp_path):
    """The filter runs on every log record, so `refresh()` has to be an int and
    a lock and nothing else: no file read, no config read, no rebuild."""
    sr = _fresh(monkeypatch, tmp_path)
    from core.config import config
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "sk-test-not-a-real-key", raising=False)

    def _never(*args, **kwargs):
        raise AssertionError("refresh() rebuilt the pattern instead of invalidating it")

    with monkeypatch.context() as no_reads:
        no_reads.setattr(sr, "_load_literals", _never)
        no_reads.setattr(sr, "_config_secret_literals", _never)
        sr.refresh()   # nothing is read here…

    assert sr.redact("sk-test-not-a-real-key") == "[REDACTED]"   # …and the next caller pays


# ── the vault's own keys are known literals (Batch 1, 2026-09-25) ──────────


def _vault(tmp_path, passphrase="correct-horse-battery-staple-99", keyfile="a" * 64):
    v = tmp_path / "vault"
    v.mkdir()
    (v / ".passphrase").write_text(passphrase + "\n", encoding="utf-8")
    (v / "vault.key").write_text(keyfile + "\n", encoding="utf-8")
    return v


def test_vault_key_files_are_redacted_by_construction(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "DENYLIST_PATH", tmp_path / "nope.txt")
    monkeypatch.setattr(sr, "VAULT_DIR", _vault(tmp_path))
    sr.reset_for_tests()
    out = sr.redact("pw is correct-horse-battery-staple-99 and key " + "a" * 64 + " done")
    assert out == "pw is [REDACTED] and key [REDACTED] done"


def test_vault_literals_reload_when_the_files_change(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "DENYLIST_PATH", tmp_path / "nope.txt")
    v = _vault(tmp_path)
    monkeypatch.setattr(sr, "VAULT_DIR", v)
    sr.reset_for_tests()
    assert "[REDACTED]" in sr.redact("correct-horse-battery-staple-99")
    (v / ".passphrase").write_text("brand-new-passphrase-2026-xyz\n", encoding="utf-8")
    import os, time
    os.utime(v / ".passphrase", (time.time() + 5, time.time() + 5))
    assert "[REDACTED]" in sr.redact("brand-new-passphrase-2026-xyz")
    assert "correct-horse-battery-staple-99" in sr.redact("correct-horse-battery-staple-99")


def test_vault_literal_reloads_when_size_changes_at_the_same_mtime(tmp_path, monkeypatch):
    """Fix round 1 (task review, plan-mandated). The rotation (Task 8) moves
    the old `.passphrase` aside and recreates it empty before writing the new
    text — a write that lands in the same kernel timestamp tick leaves mtime
    unchanged either way. A cache key keyed on mtime alone would keep serving
    the OLD pattern here and never catch the new, differently-sized
    passphrase; forcing the mtime back to its old value (via `os.utime(...,
    ns=...)`) isolates that the fix is the size term, not the mtime term."""
    monkeypatch.setattr(sr, "DENYLIST_PATH", tmp_path / "nope.txt")
    v = _vault(tmp_path)
    monkeypatch.setattr(sr, "VAULT_DIR", v)
    sr.reset_for_tests()
    assert "[REDACTED]" in sr.redact("correct-horse-battery-staple-99")

    old_mtime_ns = (v / ".passphrase").stat().st_mtime_ns
    new_literal = "a-brand-new-passphrase-of-a-different-length-2026"
    (v / ".passphrase").write_text(new_literal + "\n", encoding="utf-8")
    import os
    os.utime(v / ".passphrase", ns=(old_mtime_ns, old_mtime_ns))  # same tick as before

    assert sr.redact(new_literal) == "[REDACTED]"


def test_missing_vault_dir_is_a_no_op(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "DENYLIST_PATH", tmp_path / "nope.txt")
    monkeypatch.setattr(sr, "VAULT_DIR", tmp_path / "absent")
    sr.reset_for_tests()
    assert sr.redact("hello there") == "hello there"


def test_the_suite_never_points_the_vault_dir_at_the_real_home():
    """Final fix wave (2026-09-26). tests/conftest.py sets PALLI_VAULT_DIR to
    a path under the suite's tmp root BEFORE core.secrets_redaction is first
    imported; a conftest reorder (or an early import) would silently point
    VAULT_DIR back at ~/.palli/vault — the owner's live vault keys, read by
    every redact() the suite makes. No monkeypatch here: this is the value
    the whole suite runs with. Path arithmetic only — nothing under the real
    home is touched, not even stat()ed."""
    import os

    real_home_palli = Path(os.path.abspath(Path.home() / ".palli"))
    vault_dir = Path(os.path.abspath(os.path.expanduser(str(sr.VAULT_DIR))))
    assert vault_dir != real_home_palli
    assert real_home_palli not in vault_dir.parents, vault_dir
