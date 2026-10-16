"""Reversible masking at the provider seam (Batch 1, 2026-09-25).

Fix round 1 (2026-09-26) added coverage for: HMAC-keyed tokens + collision
handling, the revised data:/base64 exemption (R2), the key-grammar boundary
fix (underscore + JSON-escaped-newline neighbors), the Bearer trailing-dot
exclusion, and the tightened phone separators.
"""
from __future__ import annotations

import hashlib

import pytest

from core.agent import payload_masking as pm

SSN = "123-45-6789"
KEY = "gsk_" + "Ab3" * 12  # 40 chars after the prefix


@pytest.fixture(autouse=True)
def _fresh():
    pm.reset_for_tests()
    yield
    pm.reset_for_tests()


def test_ssn_and_key_are_masked_and_come_back_exactly():
    text = f"his number is {SSN}, and the key is `{KEY}`."
    masked = pm.mask_text(text)
    assert SSN not in masked and KEY not in masked
    assert len(pm.TOKEN_RE.findall(masked)) == 2
    assert masked.endswith("`.")  # the punctuation around the key survives
    assert pm.unmask_text(masked) == text


def test_same_value_gets_one_stable_token():
    a = pm.mask_text(f"{KEY} then {KEY}")
    b = pm.mask_text(f"again {KEY}")
    tokens = set(pm.TOKEN_RE.findall(a)) | set(pm.TOKEN_RE.findall(b))
    assert len(tokens) == 1


def test_masking_is_idempotent_and_tokens_survive_remasking():
    once = pm.mask_text(f"key {KEY}")
    assert pm.mask_text(once) == once


def test_phone_bearer_and_pem_are_masked():
    pem = "-----BEGIN PRIVATE KEY-----\nMIIabc\n-----END PRIVATE KEY-----"
    text = f"call (555) 010-2233 or use Bearer abcdefghijklmnopqrstuvwxyz0123 and {pem}"
    masked = pm.mask_text(text)
    assert "010-2233" not in masked and "abcdefghijklmnop" not in masked and "MIIabc" not in masked
    assert masked.startswith("call [MASKED:")
    assert "Bearer [MASKED:" in masked  # the word Bearer stays; the token goes
    assert pm.unmask_text(masked) == text


def test_ordinary_text_is_untouched():
    for s in (
        "meet at 10:30:45",
        "order 12345-678",
        "version 3.2.1",
        "sk-",
        "the room was quiet",
        "192.168." + "100.123",  # split: the release scanner reads this file too
        "2026-09-26 00:47:12",
        "123456789012345678",  # an 18-digit Discord id
        "task-management-and-follow-up-items",
    ):
        assert pm.mask_text(s) == s


def test_mask_obj_walks_nested_payloads_and_leaves_other_types():
    payload = [{"role": "user", "content": [{"type": "text", "text": f"n {SSN}"}], "n": 3}]
    out = pm.mask_obj(payload)
    assert SSN not in str(out) and out[0]["n"] == 3 and out[0]["role"] == "user"
    assert pm.unmask_obj(out) == payload


def test_unknown_token_is_left_as_is():
    assert pm.unmask_text("[MASKED:deadbeef]") == "[MASKED:deadbeef]"


def test_tolerant_token_spelling_is_put_back():
    masked = pm.mask_text(KEY)
    hexpart = pm.TOKEN_RE.search(masked).group(1)
    assert pm.unmask_text(f"[MASKED: {hexpart}]") == KEY


def test_tolerant_token_spelling_accepts_uppercase_hex_and_trailing_space():
    masked = pm.mask_text(KEY)
    hexpart = pm.TOKEN_RE.search(masked).group(1)
    assert pm.unmask_text(f"[MASKED: {hexpart.upper()} ]") == KEY


def test_for_provider_applies_the_irreversible_layer_first(monkeypatch):
    from core import secrets_redaction as sr
    monkeypatch.setattr(sr, "redact", lambda t: t.replace("hunter2hunter2", "[REDACTED]"))
    out = pm.for_provider({"content": f"pw hunter2hunter2 ssn {SSN}"})
    assert out["content"].startswith("pw [REDACTED] ssn [MASKED:")


def test_looks_secret():
    assert pm.looks_secret(f"note {SSN}") and pm.looks_secret(KEY)
    assert not pm.looks_secret("a plain memory line about the boat")


# --- Fix round 1, item 1: HMAC-keyed tokens + collision handling ----------


def test_token_is_keyed_not_a_plain_hash():
    # Without the process key, the token would be exactly sha256(value)[:8]
    # and a provider (or anyone guessing a candidate value, e.g. one of an
    # SSN's ~10**9 possibilities) could compute and confirm it unassisted.
    plain = hashlib.sha256(KEY.encode("utf-8")).hexdigest()[:8]
    masked = pm.mask_text(KEY)
    token = pm.TOKEN_RE.search(masked).group(1)
    assert token != plain


def test_collision_gets_a_second_stable_token(monkeypatch):
    # Force two distinct values onto the same first digest; the second must
    # be pushed to the counter-suffixed digest instead of overwriting.
    seq = iter(["aaaaaaaa", "aaaaaaaa", "bbbbbbbb"])
    monkeypatch.setattr(pm, "_digest", lambda value, counter=0: next(seq))
    tok1 = pm._token_for("value-one")
    tok2 = pm._token_for("value-two")
    assert tok1 != tok2
    assert pm.unmask_text(tok1) == "value-one"
    assert pm.unmask_text(tok2) == "value-two"


# --- Fix round 1, item 2 (R2 revised): data:/base64 exemption ------------


def test_plain_text_starting_with_data_colon_is_still_masked():
    sse = f'data: {{"ssn": "{SSN}"}}'
    masked = pm.mask_text(sse)
    assert SSN not in masked
    assert pm.unmask_text(masked) == sse

    yaml_like = f"data:\n  key: {KEY}\n"
    masked_yaml = pm.mask_text(yaml_like)
    assert KEY not in masked_yaml
    assert pm.unmask_text(masked_yaml) == yaml_like

    sentence = f"data: my key is {KEY}"
    masked_sentence = pm.mask_text(sentence)
    assert KEY not in masked_sentence
    assert pm.unmask_text(masked_sentence) == sentence


def test_data_url_and_raw_base64_blob_are_left_byte_identical():
    data_url = "data:image/png;base64,AIza" + "B" * 35 + "restofthebase64=="
    assert pm.mask_text(data_url) == data_url

    raw_blob = ("A" * 120) + "+" + "AIza" + "B" * 35 + ("C" * 100)
    assert len(raw_blob) >= 256
    assert pm.mask_text(raw_blob) == raw_blob

    nested = {"content": [{"type": "image", "url": data_url}, {"type": "b64", "data": raw_blob}]}
    out = pm.mask_obj(nested)
    assert out["content"][0]["url"] == data_url
    assert out["content"][1]["data"] == raw_blob


# --- Fix round 1, item 3: key-grammar boundary ----------------------------
# The `sk-` values carry a digit ("A1" * 10): since the final fix wave
# (2026-09-26) an `sk-` tail must, as every real key's does — a digit-free
# run is a prose slug. These tests are about the BOUNDARY, not the tail.


def test_key_after_underscore_is_masked():
    value = "sk-" + "A1" * 10
    text = f"_{value}_"
    masked = pm.mask_text(text)
    assert value not in masked
    assert pm.unmask_text(masked) == text

    gvalue = "gsk_" + "A" * 20
    text2 = f"__{gvalue}__"
    masked2 = pm.mask_text(text2)
    assert gvalue not in masked2
    assert pm.unmask_text(masked2) == text2


def test_key_after_json_escaped_newline_is_masked():
    value = "sk-" + "A1" * 10
    text = "line1\\n" + value  # literal backslash + n, not an actual newline
    masked = pm.mask_text(text)
    assert value not in masked
    assert pm.unmask_text(masked) == text


KEY_PREFIX_VALUES = [
    "sk-" + "A1" * 10,
    "sk-ant-" + "A1" * 10,
    "AKIA" + "A" * 16,
    "ghp_" + "A" * 36,
    "github_pat_" + "A" * 22,
    "xo" + "xb-" + "A" * 12,  # split: the release scanner reads this file too
    "AIza" + "A" * 35,
    "gsk_" + "A" * 20,
    "pmd_" + "A1" * 21,  # the mobile door's device token (2026-10-13)
]


@pytest.mark.parametrize("value", KEY_PREFIX_VALUES)
def test_each_key_prefix_masks_and_round_trips(value):
    text = f"key: {value} end"
    masked = pm.mask_text(text)
    assert value not in masked
    assert pm.unmask_text(masked) == text


def test_key_in_parens_with_trailing_period_round_trips():
    value = "sk-" + "A1" * 10
    text = f"(the key {value})."
    masked = pm.mask_text(text)
    assert value not in masked
    assert pm.unmask_text(masked) == text


# --- Ride-along fixes ------------------------------------------------------


def test_bearer_trailing_period_is_excluded_from_token():
    value = "abcdefghijklmnopqrstuvwxyz0123"
    a = pm.mask_text(f"use Bearer {value}")
    b = pm.mask_text(f"use Bearer {value}.")
    tok_a = pm.TOKEN_RE.search(a).group(1)
    tok_b = pm.TOKEN_RE.search(b).group(1)
    assert tok_a == tok_b
    assert b.endswith(".")
    assert pm.unmask_text(b) == f"use Bearer {value}."


def test_bearer_internal_dots_stay_inside_the_token():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"  # gitleaks:allow
    masked = pm.mask_text(f"Bearer {jwt}")
    assert jwt not in masked
    assert pm.unmask_text(masked) == f"Bearer {jwt}"


def test_phone_tab_separated_numbers_are_untouched():
    text = "100\t200\t3000"  # a numeric table row, not a phone number
    assert pm.mask_text(text) == text


def test_fullwidth_digits_in_an_ssn_shape_are_not_masked():
    """Fix round 1 (2026-09-26): the ssn pattern uses `[0-9]`, not `\\d` — `\\d`
    is Unicode-aware and also matches fullwidth digits (U+FF10-FF19), so the
    old grammar would have masked a string that is not an SSN at all. Exact
    grammars only (owner rule)."""
    fullwidth_ssn = "１２３-４５-６７８９"  # fullwidth "123-45-6789"
    text = f"his number is {fullwidth_ssn}."
    masked = pm.mask_text(text)
    assert masked == text
    assert pm.TOKEN_RE.findall(masked) == []


# --- Final fix wave (2026-09-26), item 2 ------------------------------------


def test_an_sk_prose_slug_without_a_digit_is_not_a_key():
    """A real `sk-` key always carries digits; a prose slug never has to.
    `sk-tips-and-tricks-for-the-weekend` is 20+ slug characters and used to
    be masked (and would have been dropped from memory by the belt)."""
    slug = "sk-" + "tips-and-tricks-for-the-weekend"
    for text in (slug, f"see {slug} for more", f"({slug})"):
        assert pm.mask_text(text) == text
        assert not pm.looks_secret(text)
        assert not pm.looks_like_credential(text)


def test_a_key_prefix_right_after_a_hyphen_is_not_a_key():
    """`my-sk-…` is the tail of a hyphenated word, not a key starting — even
    when the slug happens to carry a digit."""
    slug = "my-sk-" + "tips-and-tricks-for-2026-weekend"
    assert pm.mask_text(slug) == slug
    assert not pm.looks_like_credential(slug)
    # …while a digit-bearing key after a space, `_` or an escaped newline
    # still masks (the other boundary cases above keep covering those).
    key = "sk-" + "Ab3" * 8
    assert key not in pm.mask_text(f"key {key}")
    assert key not in pm.mask_text("x\\n" + key)


def test_fullwidth_digits_in_a_phone_shape_are_not_masked():
    """`[0-9]`, not `\\d` — the phone grammar is exact like the SSN one."""
    fullwidth = "５５５-０１０-２２３３"  # fullwidth "555-010-2233"
    assert pm.mask_text(f"call {fullwidth}") == f"call {fullwidth}"
    assert not pm.looks_secret(fullwidth)


def test_looks_like_credential_covers_every_grammar_but_the_phone():
    pem = "-----BEGIN PRIVATE KEY-----\nMIIabc\n-----END PRIVATE KEY-----"
    assert pm.looks_like_credential(f"note {SSN}")
    assert pm.looks_like_credential(KEY)
    assert pm.looks_like_credential("use Bearer abcdefghijklmnopqrstuvwxyz0123")
    assert pm.looks_like_credential(pem)
    # A phone number is secret-SHAPED (masked on the wire) but not a
    # credential: the memory belt keeps it.
    assert pm.looks_secret("call (555) 010-2233")
    assert not pm.looks_like_credential("call (555) 010-2233")
    assert not pm.looks_like_credential("a plain memory line about the boat")
    assert not pm.looks_like_credential("")


def test_the_collision_walk_is_bounded(monkeypatch):
    """Every digest colliding forever must end in a RuntimeError, never an
    unbounded loop holding the lock."""
    calls = {"n": 0}

    def _always_the_same(value, counter=0):
        calls["n"] += 1
        if calls["n"] > 500:
            raise AssertionError("the collision walk is unbounded")
        return "aaaaaaaa"

    monkeypatch.setattr(pm, "_digest", _always_the_same)
    pm._token_for("value-one")
    with pytest.raises(RuntimeError) as info:
        pm._token_for("value-two")
    assert "value-two" not in str(info.value)
    assert calls["n"] <= 1 + 1 + 64
    # The lock was released: the table still answers.
    assert pm.unmask_text("[MASKED:aaaaaaaa]") == "value-one"


# --- Surgical pass (2026-09-26): linear sk- lookahead, key after \uXXXX ----


def test_a_long_digit_free_sk_run_masks_in_linear_time():
    """The unbounded digit lookahead rescanned the whole run at every `sk-`
    candidate: `"_sk-" * 40000` (160 KB) took ~64 s, and for_provider runs
    on her event loop. Bounded to 255 characters it is O(1) per candidate.
    ~0.26 s on this VM when idle; the quadratic grammar took ~64 s. The best
    of up to three runs is timed, so a load spike during a full gate cannot
    flake it, and a run over 5 s stops early (the regression is 100x+)."""
    import time

    text = "_sk-" * 40000
    timings = []
    for _ in range(3):
        started = time.perf_counter()
        out = pm.mask_text(text)
        timings.append(time.perf_counter() - started)
        assert out == text  # digit-free: not a key
        if timings[-1] < 0.5 or timings[-1] > 5:
            break
    assert min(timings) < 0.5, [f"{t:.2f}s" for t in timings]


@pytest.mark.parametrize("value", [KEY, "sk-" + "A1" * 10])
def test_a_key_right_after_a_json_unicode_escape_is_masked(value):
    """json.dumps (ensure_ascii) writes a curly quote as the six characters
    `\\u201c`; its last one is a hex digit, so the key right after it was
    read as the middle of a word and left unmasked."""
    import json

    original = {"c": "“" + value}
    dumped = json.dumps(original)
    assert "\\u201c" + value in dumped  # the shape under test
    masked = pm.mask_text(dumped)
    assert value not in masked
    assert json.loads(pm.unmask_text(masked)) == original
