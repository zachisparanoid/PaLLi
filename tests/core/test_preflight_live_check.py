"""The preflight tests the brain LIVE, and reads the balance (week 1, task 1b).

"The key is present" was never the question a stranger needed answered. A typo,
a revoked key, a provider outage and an empty account all look identical to a
config check, and all of them end the same way: she starts, and every reply
fails. So the preflight sends one tiny PONG and reads what is left on the
account — and the doctor shows both, as warnings, never as blockers.

Nothing here touches the network: `validate_provider` and `fetch_balance` are
stubbed at the module the probes import them from.
"""
from __future__ import annotations

import main as main_module
import pytest

from core import launcher_support
from core.config import config
from core.punch_list import PunchList
from palli_config import providers as providers_module
from palli_config.ctl_prefix import ctl_prefix
from palli_config.providers import BalanceInfo, ValidationResult


@pytest.fixture
def online(monkeypatch):
    """tests/conftest.py keeps the suite offline; these tests opt back in."""
    monkeypatch.delenv("PALLI_PREFLIGHT_OFFLINE", raising=False)


@pytest.fixture
def answering_brain(monkeypatch):
    calls: list[str] = []

    async def _validate(provider_name: str, **_kwargs) -> ValidationResult:
        calls.append(provider_name)
        return ValidationResult(ok=True, latency_ms=42, error=None)

    monkeypatch.setattr(providers_module, "validate_provider", _validate)
    return calls


@pytest.fixture
def no_balance(monkeypatch):
    async def _fetch(provider_name: str, **_kwargs):
        return None

    monkeypatch.setattr(providers_module, "fetch_balance", _fetch)


def _balance(monkeypatch, total: float, *, available: bool = True) -> None:
    async def _fetch(provider_name: str, **_kwargs) -> BalanceInfo:
        return BalanceInfo(
            provider=provider_name,
            currency="USD",
            total_balance=total,
            available=available,
        )

    monkeypatch.setattr(providers_module, "fetch_balance", _fetch)


def _exploding(monkeypatch) -> None:
    async def _never(*_args, **_kwargs):
        raise AssertionError("the network was reached")

    monkeypatch.setattr(providers_module, "validate_provider", _never)
    monkeypatch.setattr(providers_module, "fetch_balance", _never)


# --- the probes -------------------------------------------------------------


def test_offline_skips_the_pong_and_the_balance(monkeypatch):
    monkeypatch.setenv("PALLI_PREFLIGHT_OFFLINE", "1")
    _exploding(monkeypatch)

    assert launcher_support.probe_llm_brain("deepseek")["skipped"] is True
    assert launcher_support.probe_llm_balance("deepseek")["skipped"] is True


def test_a_brain_that_answers_reports_its_latency(online, answering_brain):
    result = launcher_support.probe_llm_brain("deepseek")

    assert result["skipped"] is False
    assert result["ok"] is True
    assert result["latency_ms"] == 42
    assert answering_brain == ["deepseek"]


def test_a_brain_that_does_not_answer_reports_why(online, monkeypatch):
    async def _validate(provider_name: str, **_kwargs) -> ValidationResult:
        return ValidationResult(ok=False, latency_ms=7, error="401 unauthorized")

    monkeypatch.setattr(providers_module, "validate_provider", _validate)

    result = launcher_support.probe_llm_brain("deepseek")

    assert result["ok"] is False
    assert "401 unauthorized" in result["error"]


def test_a_low_balance_is_flagged_against_the_configured_floor(online, monkeypatch):
    monkeypatch.setattr(config, "LLM_LOW_BALANCE_WARN", 2.0, raising=False)
    _balance(monkeypatch, 1.25)

    result = launcher_support.probe_llm_balance("deepseek")

    assert result["known"] is True
    assert result["low"] is True
    assert result["total_balance"] == 1.25
    assert result["currency"] == "USD"


def test_a_healthy_balance_is_not_flagged(online, monkeypatch):
    monkeypatch.setattr(config, "LLM_LOW_BALANCE_WARN", 2.0, raising=False)
    _balance(monkeypatch, 12.34)

    assert launcher_support.probe_llm_balance("deepseek")["low"] is False


def test_a_provider_with_no_balance_api_is_not_a_finding(online, no_balance):
    result = launcher_support.probe_llm_balance("codex")

    assert result["supported"] is False
    assert result["known"] is False
    assert result["low"] is False


def test_an_unavailable_account_is_not_called_low(online, monkeypatch):
    """An account can hold money and still be shut off. Folding that into `low`
    sent the reader to the wrong fix — top up an account that is already funded."""
    monkeypatch.setattr(config, "LLM_LOW_BALANCE_WARN", 2.0, raising=False)
    _balance(monkeypatch, 50.0, available=False)

    result = launcher_support.probe_llm_balance("deepseek")

    assert result["known"] is True
    assert result["low"] is False
    assert result["available"] is False


def test_a_probe_that_could_not_run_is_not_an_unanswered_brain(online, monkeypatch):
    """Ours, not theirs."""
    def _broken(*_args, **_kwargs):
        raise RuntimeError("the probe itself fell over")

    monkeypatch.setattr(providers_module, "validate_provider", _broken)

    result = launcher_support.probe_llm_brain("deepseek")

    assert result["skipped"] is False
    assert result["ran"] is False
    assert result["ok"] is False


# --- the preflight surface --------------------------------------------------


def test_a_silent_brain_becomes_a_hard_punch_item(online, monkeypatch, capsys):
    async def _validate(provider_name: str, **_kwargs) -> ValidationResult:
        return ValidationResult(ok=False, latency_ms=0, error="connection refused")

    monkeypatch.setattr(providers_module, "validate_provider", _validate)
    monkeypatch.setattr(config, "LLM_PROVIDER", "deepseek", raising=False)
    config.punch_list = PunchList()

    assert main_module.live_brain_and_balance() is False

    rendered = config.punch_list.render()
    assert "LLM_PROVIDER" in rendered
    assert "the brain (deepseek) did not answer: connection refused" in rendered
    # Premise update (final review): the punch text asks ctl_prefix() what the
    # reader can paste, so the assertion asks the same question rather than
    # pinning the repo form. tests/palli_config/test_brain_render.py does this.
    assert f"{ctl_prefix()} brain" in rendered


def test_an_answering_brain_prints_its_latency(online, answering_brain, no_balance,
                                               monkeypatch, capsys):
    monkeypatch.setattr(config, "LLM_PROVIDER", "deepseek", raising=False)
    config.punch_list = PunchList()

    assert main_module.live_brain_and_balance() is True

    assert "Brain: deepseek answered in 42 ms" in capsys.readouterr().out


def test_a_low_balance_is_printed_as_a_warning(online, answering_brain, monkeypatch,
                                               capsys):
    monkeypatch.setattr(config, "LLM_PROVIDER", "deepseek", raising=False)
    monkeypatch.setattr(config, "LLM_LOW_BALANCE_WARN", 2.0, raising=False)
    _balance(monkeypatch, 0.42)
    config.punch_list = PunchList()

    assert main_module.live_brain_and_balance() is True

    out = capsys.readouterr().out
    assert "Balance: 0.42 USD (deepseek)" in out
    assert "Balance is low" in out
    assert "top up before she runs dry" in out


def test_a_healthy_balance_is_printed_without_the_warning(online, answering_brain,
                                                          monkeypatch, capsys):
    monkeypatch.setattr(config, "LLM_PROVIDER", "deepseek", raising=False)
    monkeypatch.setattr(config, "LLM_LOW_BALANCE_WARN", 2.0, raising=False)
    _balance(monkeypatch, 12.34)
    config.punch_list = PunchList()

    assert main_module.live_brain_and_balance() is True

    out = capsys.readouterr().out
    assert "Balance: 12.34 USD (deepseek)" in out
    assert "Balance is low" not in out


def test_a_probe_that_could_not_run_warns_and_lets_the_boot_stand(
    online, no_balance, monkeypatch, capsys
):
    """A broken check of ours must never become a punch item telling a stranger
    to go and look at a key that is perfectly fine."""
    def _broken(*_args, **_kwargs):
        raise RuntimeError("the probe itself fell over")

    monkeypatch.setattr(providers_module, "validate_provider", _broken)
    monkeypatch.setattr(config, "LLM_PROVIDER", "deepseek", raising=False)
    config.punch_list = PunchList()

    assert main_module.live_brain_and_balance() is True

    assert "could not be checked" in capsys.readouterr().out
    assert config.punch_list.render() == ""


def test_an_unavailable_account_gets_its_own_line(online, answering_brain,
                                                  monkeypatch, capsys):
    monkeypatch.setattr(config, "LLM_PROVIDER", "deepseek", raising=False)
    monkeypatch.setattr(config, "LLM_LOW_BALANCE_WARN", 2.0, raising=False)
    _balance(monkeypatch, 50.0, available=False)
    config.punch_list = PunchList()

    assert main_module.live_brain_and_balance() is True

    out = capsys.readouterr().out
    assert "Balance: 50.00 USD (deepseek)" in out
    assert "marked unavailable" in out
    assert "Balance is low" not in out


def test_a_balance_that_cannot_be_read_says_so(online, answering_brain, no_balance,
                                               monkeypatch, capsys):
    """Silence is how it ran to zero twice without a word — but only for a
    provider that HAS a balance API. The others stay quiet, correctly."""
    monkeypatch.setattr(config, "LLM_PROVIDER", "deepseek", raising=False)
    config.punch_list = PunchList()

    assert main_module.live_brain_and_balance() is True

    assert "Balance: could not be read (deepseek)" in capsys.readouterr().out


def test_a_provider_with_no_balance_api_says_nothing(online, answering_brain,
                                                     no_balance, monkeypatch, capsys):
    """Non-vacuous partner: the line above is about a FAILED read, not about
    every provider that has nothing to report."""
    monkeypatch.setattr(config, "LLM_PROVIDER", "codex", raising=False)
    config.punch_list = PunchList()

    assert main_module.live_brain_and_balance() is True

    assert "Balance" not in capsys.readouterr().out


# --- the doctor surface -----------------------------------------------------


def test_the_doctor_shows_the_brain_and_the_balance(online, answering_brain,
                                                    monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "deepseek", raising=False)
    monkeypatch.setattr(config, "LLM_LOW_BALANCE_WARN", 2.0, raising=False)
    _balance(monkeypatch, 0.42)

    checks = launcher_support.check_llm_brain_and_balance()
    by_code = {str(item["code"]): item for item in checks}

    assert "llm_brain_answers" in by_code
    assert "llm_balance" in by_code
    assert "llm_balance_low" in by_code
    # Funded but shut off is a different finding, and only that one fires here.
    assert "llm_balance_unavailable" not in by_code
    # Never a blocker: a host with no network, or a provider having a bad hour,
    # is a real install and not a broken one.
    assert {item["severity"] for item in checks} <= {"ok", "warning"}


def test_the_doctor_report_carries_them_when_asked(online, answering_brain,
                                                   no_balance, monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "deepseek", raising=False)

    report = launcher_support.build_doctor_report(probe_llm=True)

    codes = {str(item.get("code")) for item in report["checks"]}
    assert "llm_brain_answers" in codes


def test_the_doctor_separates_an_unavailable_account_from_a_low_one(
    online, answering_brain, monkeypatch
):
    monkeypatch.setattr(config, "LLM_PROVIDER", "deepseek", raising=False)
    monkeypatch.setattr(config, "LLM_LOW_BALANCE_WARN", 2.0, raising=False)
    _balance(monkeypatch, 50.0, available=False)

    checks = launcher_support.check_llm_brain_and_balance()
    by_code = {str(item["code"]): item for item in checks}

    assert "llm_balance_unavailable" in by_code
    assert "llm_balance_low" not in by_code
    assert {item["severity"] for item in checks} <= {"ok", "warning"}


def test_a_health_poll_never_pongs_the_brain(monkeypatch):
    """build_doctor_report() is the body of GET /api/health/detail. A live PONG on
    every poll would spend money and add latency, so the probes are opt-in."""
    monkeypatch.delenv("PALLI_PREFLIGHT_OFFLINE", raising=False)
    _exploding(monkeypatch)

    report = launcher_support.build_doctor_report()

    codes = {str(item.get("code")) for item in report["checks"]}
    assert "llm_brain_answers" not in codes
    assert "llm_balance" not in codes
