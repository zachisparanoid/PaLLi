"""envfile — the brain switch's env writer. Byte-preserving: `.env.local` is
CRLF and both existing writers (palli_config.core, palli_ctl) converted it to
LF (plan finding 6). This one changes only the keys it is asked to.

2026-09-17 (first door, task 2): it is now THE writer — `palli_ctl configure`
and the setup API both go through it, so the CLI and the door cannot disagree
about what `.env.local` means."""
from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

from palli_config.envfile import atomic_write_text, parse_env, set_env_keys

CRLF = (
    "# her brain\r\n"
    "LLM_PROVIDER=deepseek\r\n"
    "LLM_RESPONSE_MODEL=deepseek-v4-flash\r\n"
    "\r\n"
    "OPENAI_API_KEY=sk-test\r\n"
    "  # indented comment stays\r\n"
    "CADENCE_MODEL=\r\n"
)


def test_parse_env_last_occurrence_wins_and_quotes_are_stripped():
    text = "A=1\nB='two'\nA=3\n# C=hidden\nD=\"four five\"\n"
    assert parse_env(text) == {"A": "3", "B": "two", "D": "four five"}


def test_set_env_keys_replaces_in_place_and_keeps_crlf():
    out = set_env_keys(CRLF, {"LLM_PROVIDER": "claude", "LLM_RESPONSE_MODEL": "sonnet"})
    assert "\r\n" in out and "\n" not in out.replace("\r\n", "")
    lines = out.split("\r\n")
    assert lines[0] == "# her brain"
    assert lines[1] == "LLM_PROVIDER=claude"
    assert lines[2] == "LLM_RESPONSE_MODEL=sonnet"
    assert lines[4] == "OPENAI_API_KEY=sk-test"          # untouched, same position
    assert lines[5] == "  # indented comment stays"
    assert out.endswith("\r\n")


def test_set_env_keys_appends_missing_keys_at_the_end():
    out = set_env_keys("A=1\n", {"CLAUDE_CLI_MODEL": "sonnet"})
    assert out == "A=1\nCLAUDE_CLI_MODEL=sonnet\n"


def test_set_env_keys_appends_after_a_missing_trailing_newline():
    out = set_env_keys("A=1", {"B": "2"})
    assert out == "A=1\nB=2\n"


def test_set_env_keys_none_deletes_and_duplicates_collapse():
    text = "A=1\nB=2\nA=9\n"
    out = set_env_keys(text, {"A": "5", "B": None})
    assert out == "A=5\n"


def test_set_env_keys_quotes_values_that_need_it():
    out = set_env_keys("", {"X": "two words", "Y": "a#b"})
    assert out == 'X="two words"\nY="a#b"\n'


def test_set_env_keys_is_a_no_op_for_identical_values():
    assert set_env_keys(CRLF, {"LLM_PROVIDER": "deepseek"}) == CRLF


def test_atomic_write_text_writes_bytes_and_replaces(tmp_path: Path):
    target = tmp_path / ".env.local"
    target.write_bytes(b"OLD=1\r\n")
    atomic_write_text(target, "NEW=2\r\n")
    assert target.read_bytes() == b"NEW=2\r\n"
    assert [p.name for p in tmp_path.iterdir()] == [".env.local"]  # no temp file left behind


@pytest.mark.parametrize("bad", ["line1\nline2", "trailing\n", "cr\rhere", "crlf\r\ninside"])
def test_set_env_keys_refuses_values_with_line_breaks(bad):
    """Review finding (2026-09-04): a raw line break inside a value splits one KEY=VALUE record
    across two physical lines and corrupts the file for good on the next edit. Refused up front —
    and nothing else in the update is applied either."""
    with pytest.raises(ValueError) as err:
        set_env_keys("A=1\nB=2\n", {"A": "fine", "B": bad})
    assert "B" in str(err.value)


# --------------------------------------------------------------- inline comments

# The final review's M5. `.env.template` has fourteen `KEY=value   # comment` lines and
# parse_env kept the comment in the value on the six that carry one — while dotenv, the
# parser the app itself boots through, read them right. Three env parsers with three
# comment rules is the class; agreement with dotenv is how this one leaves it.
TEMPLATE = Path(__file__).resolve().parents[2] / ".env.template"


def _dotenv(text: str) -> dict[str, str]:
    from dotenv import dotenv_values

    return {k: (v if v is not None else "") for k, v in
            dotenv_values(stream=io.StringIO(text)).items()}


def test_the_template_really_does_carry_the_shape_this_is_about():
    """Non-vacuous: if the template ever stops having them, this test says so rather
    than passing on an empty set. Eight, not the original fourteen: week 2 task 3 moved
    the comments of the eight BLANK-valued lines above their keys, because a blank
    value followed by `# comment` is read by python-dotenv as the comment ITSELF (the
    owner id looked configured; JWT_SECRET signed sessions with its own instructions)
    — the shape this guard protects, a real value followed by an inline comment, was
    still there six times, Batch 2 (2026-09-27) added a seventh,
    PALLI_AGENT_HISTORY_STEP, and Batch 4 (2026-09-28) an eighth,
    PALLI_AGENT_HISTORY_MAX_MESSAGES_REMINDER."""
    shaped = [ln for ln in TEMPLATE.read_text(encoding="utf-8").splitlines()
              if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=.*[ \t]#", ln)]
    assert len(shaped) == 8, shaped


def test_parse_env_agrees_with_dotenv_over_the_whole_shipped_template():
    text = TEMPLATE.read_text(encoding="utf-8")
    assert parse_env(text) == _dotenv(text)


@pytest.mark.parametrize("line, expected", [
    ("PRESENT_MOMENT_MIN_GAP_SECONDS=600   # min gap between cycles", "600"),
    ("SESSION_COOKIE_SECURE=false          # true behind HTTPS", "false"),
    ("PALLI_AGENT_HISTORY_MAX_MESSAGES=120  # LLM payload window (messages)", "120"),
    ("LOG_USER_CONTENT_PREVIEW_CHARS=0     # 0 = never log user content previews", "0"),
    ("K=v\t# tab-preceded counts too", "v"),
    ("K=a b # trailing comment", "a b"),
])
def test_an_inline_comment_is_not_part_of_the_value(line, expected):
    assert parse_env(line) == {line.split("=")[0]: expected}


@pytest.mark.parametrize("line, expected", [
    ("K=v#c", "v#c"),                    # no whitespace before it: part of the value
    ("K=  # c", "# c"),                  # the value IS a hash — dotenv keeps it
    ("K=# c", "# c"),
    ('K="v # c"', "v # c"),              # quoted: the hash is data
    ("K='v # c'", "v # c"),
    ('K="v" # c', "v"),                  # quoted value, then a comment
    ("K=", ""),
])
def test_a_hash_that_is_not_a_comment_survives(line, expected):
    """Matched against dotenv case by case, because "strip everything after #" would
    quietly truncate a password, a URL fragment or a colour."""
    assert parse_env(line) == {"K": expected}
    assert _dotenv(line) == {"K": expected}
