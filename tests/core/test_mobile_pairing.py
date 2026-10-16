"""The mobile door's pairing primitives (spec §3)."""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from core import mobile_pairing as mp


def test_a_new_code_is_eight_crockford_characters():
    for _ in range(200):
        code = mp.new_pair_code()
        assert len(code) == 8
        assert set(code) <= set(mp.CROCKFORD)


def test_a_typed_code_normalises_dashes_case_and_lookalikes():
    assert mp.normalize_pair_code("k7qm-2xpa") == "K7QM2XPA"
    assert mp.normalize_pair_code(" K7QM 2XPA ") == "K7QM2XPA"
    assert mp.normalize_pair_code("O1IL-0000") == "01110000"


def test_the_display_form_has_one_dash():
    assert mp.display_code("K7QM2XPA") == "K7QM-2XPA"


def test_a_device_token_has_the_prefix_and_enough_randomness():
    token = mp.new_device_token()
    assert token.startswith("pmd_")
    assert len(token) >= 4 + 43
    assert mp.new_device_token() != token


def test_hashing_is_sha256_hex():
    assert mp.sha256_hex("abc") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")


def test_the_link_carries_the_address_and_the_code():
    link = mp.pair_link("http://palli-box.example.ts.net:8000/", "K7QM2XPA")
    parsed = urlparse(link)
    assert (parsed.scheme, parsed.netloc) == ("palli", "pair")
    query = parse_qs(parsed.query)
    assert query["u"] == ["http://palli-box.example.ts.net:8000"]
    assert query["c"] == ["K7QM-2XPA"]


def test_the_qr_comes_as_svg_and_as_terminal_text():
    link = mp.pair_link("http://100.64.0.7:8000", "K7QM2XPA")
    svg = mp.qr_svg(link)
    assert svg.lstrip().startswith("<") and "<svg" in svg and "</svg>" in svg
    text = mp.qr_text(link)
    assert len(text.splitlines()) > 10
