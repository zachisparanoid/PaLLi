"""The door page's Phone row (spec §9). A source pin: the website has no
test runner in this repo; `npm run lint && npm run build` is its check."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "website" / "src"


def test_the_door_page_reads_the_phone_door_and_offers_to_pair_one():
    card = (ROOT / "components" / "door" / "DoorsCard.tsx").read_text(encoding="utf-8")
    pairing = (ROOT / "components" / "door" / "PhonePairing.tsx").read_text(encoding="utf-8")
    setup = (ROOT / "lib" / "setup.ts").read_text(encoding="utf-8")
    assert "mobile: asDoor(doors.mobile)" in setup
    assert '"/api/mobile/pair/start"' in setup and '"/api/mobile/devices"' in setup
    assert 'key: "mobile"' in card and "<PhonePairing" in card
    assert "ENABLE_MOBILE_DOOR=true" in card
    # The backend's QR is shown as an image, never injected as markup.
    assert "data:image/svg+xml" in pairing and "dangerouslySetInnerHTML" not in pairing
