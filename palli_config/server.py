"""Server launch configuration. Does not start services or contact the network."""
from __future__ import annotations

import secrets
import ipaddress
import json
import shutil
import socket
import subprocess
from urllib.parse import urlsplit


def dashboard_addresses() -> list[str]:
    """Discover local interface addresses without contacting an outside service."""
    candidates: list[str] = []
    ip = shutil.which("ip")
    if ip:
        try:
            result = subprocess.run([ip, "-j", "-4", "address", "show", "up"],
                                    capture_output=True, text=True, timeout=2, check=False)
            for interface in json.loads(result.stdout or "[]"):
                candidates.extend(info.get("local", "") for info in interface.get("addr_info", []))
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    if not candidates:
        try:
            candidates.extend(info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
        except OSError:
            pass
    addresses = []
    for candidate in candidates:
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if address.version == 4 and not (address.is_loopback or address.is_unspecified or address.is_multicast):
            if str(address) not in addresses:
                addresses.append(str(address))
    return addresses


def server_env_updates(
    url: str, *, bind: str, port: int | None, current: dict[str, str], reset_key: bool = False,
) -> dict[str, str]:
    if any(character.isspace() for character in url):
        raise ValueError("The dashboard URL cannot contain spaces or line breaks.")
    try:
        parsed = urlsplit(url.strip())
        public_port = parsed.port
    except ValueError:
        raise ValueError("Use a complete dashboard URL, such as http://192.168.1.50:3000.") from None
    if (
        parsed.scheme not in {"http", "https"} or not parsed.hostname
        or parsed.username is not None or parsed.password is not None
        or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
        or parsed.hostname in {"0.0.0.0", "::"}
    ):
        raise ValueError("Use an http:// or https:// dashboard URL without a path, credentials or query.")
    listen_port = port if port is not None else (public_port or 3000)
    if not 1 <= listen_port <= 65535 or not bind.strip() or any(c.isspace() for c in bind):
        raise ValueError("Give a bind address and a port between 1 and 65535.")
    key = str(current.get("DASHBOARD_ACCESS_TOKEN", "")).strip()
    signing_key = str(current.get("JWT_SECRET", "")).strip()
    if reset_key or len(key) < 32:
        key = secrets.token_urlsafe(32)
        # Rotating the access key also revokes previously issued browser sessions.
        signing_key = secrets.token_urlsafe(48)
    elif len(signing_key) < 32:
        signing_key = secrets.token_urlsafe(48)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    names = [n.strip() for n in current.get("BACKEND_ALLOWED_HOSTS", "").split(",") if n.strip()]
    if parsed.hostname not in names:
        names.append(parsed.hostname)
    backend_port = int(current.get("BACKEND_PORT") or 8000)
    return {
        "PALLI_DEPLOYMENT_MODE": "local",
        "FRONTEND_URL": origin,
        "FRONTEND_HOST": bind.strip(),
        "FRONTEND_PORT": str(listen_port),
        "BACKEND_HOST": "127.0.0.1",
        "BACKEND_URL": f"http://127.0.0.1:{backend_port}",
        "BACKEND_ALLOWED_HOSTS": ",".join(names),
        "DASHBOARD_ACCESS_TOKEN": key,
        "JWT_SECRET": signing_key,
        "LOCAL_DASHBOARD_NO_AUTH": "false",
        "CSRF_PROTECT_LOCAL": "true",
        "SESSION_COOKIE_SECURE": "true" if parsed.scheme == "https" else "false",
        "ALLOW_INSECURE_CORS": "false",
        "FRONTEND_ORIGINS": origin,
        "FRONTEND_ORIGIN_REGEX": "",
        # This machine hosts PaLLi; it is not the owner's active desktop.
        "LOCAL_MACHINE_PRESENCE_ENABLED": "false",
        "DESKTOP_ACTIVITY_ENABLED": "false",
        "OPERATOR_DESKTOP_ENABLED": "false",
        "OPERATOR_DESKTOP_ALLOW_INPUT_CONTROL": "false",
        "OPERATOR_DESKTOP_VISION_ENABLED": "false",
        "OPERATOR_OCR_ENABLED": "false",
        "OPERATOR_BROWSER_HEADLESS": "true",
        "ENABLE_ALWAYS_LISTENING": "false",
    }
