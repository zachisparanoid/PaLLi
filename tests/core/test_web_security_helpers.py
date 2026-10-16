from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response

from core import web_security


def _request_with_tokens(*, cookie_token: str | None, header_token: str | None):
    cookies = {}
    headers = {}
    if cookie_token is not None:
        cookies[web_security.config.CSRF_COOKIE_NAME] = cookie_token
    if header_token is not None:
        headers["x-csrf-token"] = header_token
    return SimpleNamespace(cookies=cookies, headers=headers)


def test_new_csrf_token_is_non_empty_and_random():
    first = web_security.new_csrf_token()
    second = web_security.new_csrf_token()
    assert isinstance(first, str) and len(first) > 20
    assert first != second


def test_create_and_decode_session_jwt(monkeypatch):
    monkeypatch.setattr(web_security.config, "JWT_SECRET", "test-secret")
    monkeypatch.setattr(web_security.config, "SESSION_COOKIE_MAX_AGE_SECONDS", 120)

    token = web_security.create_session_jwt(user_id=123, email="u@example.com")
    payload = web_security.decode_session_jwt(token)

    assert payload["sub"] == "123"
    assert payload["email"] == "u@example.com"
    assert payload["typ"] == "palli_session"
    assert payload["exp"] > payload["iat"]


def test_get_user_id_from_request(monkeypatch):
    monkeypatch.setattr(web_security.config, "JWT_SECRET", "test-secret")
    token = web_security.create_session_jwt(user_id=55, email="u@example.com")

    req = SimpleNamespace(cookies={web_security.config.SESSION_COOKIE_NAME: token}, headers={})
    assert web_security.get_user_id_from_request(req) == 55

    req_bad = SimpleNamespace(cookies={web_security.config.SESSION_COOKIE_NAME: "bad-token"}, headers={})
    assert web_security.get_user_id_from_request(req_bad) is None

    req_missing = SimpleNamespace(cookies={}, headers={})
    assert web_security.get_user_id_from_request(req_missing) is None


def test_require_csrf_accepts_matching_token():
    req = _request_with_tokens(cookie_token="abc", header_token="abc")
    web_security.require_csrf(req)


def test_require_csrf_rejects_missing_or_mismatch():
    original = web_security.local_auth_bypass_enabled
    web_security.local_auth_bypass_enabled = lambda: False
    try:
        with pytest.raises(HTTPException) as missing:
            web_security.require_csrf(_request_with_tokens(cookie_token=None, header_token="x"))
        assert missing.value.status_code == 403

        with pytest.raises(HTTPException) as mismatch:
            web_security.require_csrf(_request_with_tokens(cookie_token="a", header_token="b"))
        assert mismatch.value.status_code == 403
    finally:
        web_security.local_auth_bypass_enabled = original


def test_set_and_clear_auth_cookies(monkeypatch):
    monkeypatch.setattr(web_security.config, "SESSION_COOKIE_NAME", "session_cookie")
    monkeypatch.setattr(web_security.config, "CSRF_COOKIE_NAME", "csrf_cookie")
    monkeypatch.setattr(web_security.config, "SESSION_COOKIE_MAX_AGE_SECONDS", 60)
    monkeypatch.setattr(web_security.config, "SESSION_COOKIE_SECURE", False)

    response = Response()
    web_security.set_auth_cookies(response, "jwt-token", "csrf-token")
    cookie_headers = [v.decode("utf-8") for k, v in response.raw_headers if k.lower() == b"set-cookie"]
    assert any("session_cookie=jwt-token" in h for h in cookie_headers)
    assert any("csrf_cookie=csrf-token" in h for h in cookie_headers)

    web_security.clear_auth_cookies(response)
    cookie_headers = [v.decode("utf-8") for k, v in response.raw_headers if k.lower() == b"set-cookie"]
    assert any("session_cookie=" in h and "Max-Age=0" in h for h in cookie_headers)
    assert any("csrf_cookie=" in h and "Max-Age=0" in h for h in cookie_headers)
