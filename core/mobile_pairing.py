"""Pair codes, device tokens and the pairing link for the mobile door
(spec §3, 2026-10-13).

A code is what the owner reads off the door page or the terminal: eight
Crockford base32 characters, made with ``secrets``, good once for ten
minutes. A token is what the phone keeps: ``pmd_`` and 32 random bytes. Both
are stored only as SHA-256 hashes; the randomness makes a slow hash pointless.
"""
from __future__ import annotations

import hashlib
import io
import secrets
from urllib.parse import quote

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_LENGTH = 8
CODE_TTL_SECONDS = 600
TOKEN_PREFIX = "pmd_"
API_VERSION = 1

# Crockford's own reading of the letters people mistype for digits.
_LOOKALIKES = str.maketrans({"O": "0", "I": "1", "L": "1"})


def new_pair_code() -> str:
    return "".join(secrets.choice(CROCKFORD) for _ in range(CODE_LENGTH))


def normalize_pair_code(raw: str) -> str:
    """What a person typed, as the code it was: case, spaces and the dash
    dropped, and O/I/L read as 0/1."""
    cleaned = "".join(ch for ch in str(raw or "").upper() if ch.isalnum())
    return cleaned.translate(_LOOKALIKES)


def display_code(code: str) -> str:
    return f"{code[:4]}-{code[4:]}"


def new_device_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def pair_link(base_url: str, code: str) -> str:
    """``palli://pair?u=<address>&c=<code>`` — what the QR holds and what the
    app parses (the app plan's PairingLinks)."""
    base = str(base_url or "").strip().rstrip("/")
    return f"palli://pair?u={quote(base, safe='')}&c={display_code(code)}"


def qr_svg(link: str) -> str:
    """The QR as an SVG document, for the door page (shown as an <img>)."""
    import qrcode
    import qrcode.image.svg

    image = qrcode.make(link, image_factory=qrcode.image.svg.SvgPathImage, border=2)
    return image.to_string(encoding="unicode")


def qr_text(link: str) -> str:
    """The QR as block characters, for `palli_ctl phone pair`."""
    import qrcode

    qr = qrcode.QRCode(border=1)
    qr.add_data(link)
    qr.make(fit=True)
    out = io.StringIO()
    qr.print_ascii(out=out, invert=True)
    return out.getvalue()
