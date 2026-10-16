"""`doctor` looks at the model server — the one dependency the shipped config leans on.

The final review's I6. The shipped configuration points every lane at a local Ollama,
and nothing in `doctor` ever asked whether that server answers or whether the two tags
are pulled. A stranger with no Ollama got a green `doctor`, a confident `brain`, a
`main.py` that starts — and a first message that fails. The tool that claims to check
readiness has to check the dependency the install cannot run without.

WARNING severity, never a blocker: a host that runs its models on another machine, or
starts Ollama after PaLLi, is a real install and not a broken one.
"""
from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from core.config import config
from core.launcher_support import check_local_llm_reachable


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def model_server():
    """A stub that answers both doors of a local Ollama, with the tags it is given."""
    tags: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path == "/api/tags":
                body = {"models": [{"name": tag} for tag in tags]}
            elif self.path == "/v1/models":
                body = {"data": [{"id": tag} for tag in tags]}
            else:
                self.send_error(404)   # a port that answers, but not the model API
                return
            raw = json.dumps(body).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_args):  # keep pytest's output clean
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server.tags = tags  # type: ignore[attr-defined]
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def _routing(*providers: str) -> dict:
    return {
        "default_provider": providers[0],
        "task_routing": {f"lane{i}": {"provider": p, "model": "x"}
                         for i, p in enumerate(providers)},
    }


def _point_at(monkeypatch, port: int, *, model: str = "qwen3.5:9b") -> None:
    monkeypatch.setattr(config, "LOCAL_LLM_API_BASE", f"http://127.0.0.1:{port}/v1", raising=False)
    monkeypatch.setattr(config, "OLLAMA_BASE_URL", f"http://127.0.0.1:{port}", raising=False)
    monkeypatch.setattr(config, "LLM_RESPONSE_MODEL", model, raising=False)


def _codes(checks) -> list[str]:
    return [c["code"] for c in checks]


def test_a_cloud_install_is_never_probed(monkeypatch):
    """No local door in the routing, no probe: an OpenAI/Anthropic install must not be
    told about an Ollama it does not want."""
    _point_at(monkeypatch, _free_port())
    assert check_local_llm_reachable(_routing("openai", "anthropic")) == []


def test_a_server_that_answers_with_the_shipped_tag_is_ok(monkeypatch, model_server):
    model_server.tags.extend(["qwen3.5:9b", "gemma3:12b"])
    _point_at(monkeypatch, model_server.server_address[1])
    checks = check_local_llm_reachable(_routing("local_openai", "ollama"))
    assert _codes(checks) == ["local_llm_ready", "local_llm_ready"]
    assert all(c["severity"] == "ok" for c in checks)
    assert all(str(model_server.server_address[1]) in c["message"] for c in checks)


def test_a_server_that_is_not_there_is_a_warning_that_names_the_door(monkeypatch):
    port = _free_port()  # bound by nobody
    _point_at(monkeypatch, port)
    checks = check_local_llm_reachable(_routing("local_openai"))
    assert _codes(checks) == ["local_llm_unreachable"]
    assert checks[0]["severity"] == "warning"
    assert f"127.0.0.1:{port}" in checks[0]["message"]
    assert "LOCAL_LLM_API_BASE" in checks[0]["message"]


def test_both_doors_are_named_when_both_are_down(monkeypatch):
    _point_at(monkeypatch, _free_port())
    checks = check_local_llm_reachable(_routing("local_openai", "ollama"))
    assert _codes(checks) == ["local_llm_unreachable", "local_llm_unreachable"]
    assert {c["details"]["provider"] for c in checks} == {"local_openai", "ollama"}


def test_an_unpulled_response_model_says_how_to_pull_it(monkeypatch, model_server):
    model_server.tags.append("gemma3:12b")
    _point_at(monkeypatch, model_server.server_address[1], model="qwen3.5:9b")
    checks = check_local_llm_reachable(_routing("local_openai"))
    assert "local_llm_model_missing" in _codes(checks)
    missing = next(c for c in checks if c["code"] == "local_llm_model_missing")
    assert missing["severity"] == "warning"
    assert "ollama pull qwen3.5:9b" in missing["message"]


def test_the_model_check_is_silent_when_no_door_answered(monkeypatch):
    """Nothing answered, so nothing is known about what is pulled — one problem, one
    warning, and no advice to pull a model onto a server that is not running."""
    _point_at(monkeypatch, _free_port())
    checks = check_local_llm_reachable(_routing("local_openai"))
    assert "local_llm_model_missing" not in _codes(checks)


def test_a_lane_routed_to_a_local_door_counts_even_when_the_default_is_cloud(
        monkeypatch, model_server):
    """The vision lane alone is enough: her eyes go through the native door while the
    text lanes are on a cloud provider."""
    model_server.tags.append("gemma3:12b")
    _point_at(monkeypatch, model_server.server_address[1], model="gemma3:12b")
    checks = check_local_llm_reachable(_routing("anthropic", "ollama"))
    assert _codes(checks) == ["local_llm_ready"]
    assert checks[0]["details"]["provider"] == "ollama"


def test_the_probe_survives_a_door_that_answers_with_nonsense(monkeypatch, model_server):
    """A reachable server whose body cannot be read is reachable — the check must not
    turn a parse problem into 'your model server is down'."""
    model_server.tags.append("qwen3.5:9b")
    port = model_server.server_address[1]
    monkeypatch.setattr(config, "LOCAL_LLM_API_BASE", f"http://127.0.0.1:{port}/nope",
                        raising=False)
    monkeypatch.setattr(config, "LLM_RESPONSE_MODEL", "qwen3.5:9b", raising=False)
    checks = check_local_llm_reachable(_routing("local_openai"))
    assert _codes(checks) == ["local_llm_unreachable"]
