"""The `phone` hook in scripts/palli_ctl.py: parsed, dispatched, and the file
still CRLF throughout."""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_module():
    spec = importlib.util.spec_from_file_location("palli_ctl_phone_test_module",
                                                  REPO_ROOT / "scripts" / "palli_ctl.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_phone_parses_its_three_actions():
    parser = _load_module().build_parser()
    assert parser.parse_args(["phone", "pair"]).phone_action == "pair"
    assert parser.parse_args(["phone", "list"]).phone_action == "list"
    revoke = parser.parse_args(["phone", "revoke", "7"])
    assert (revoke.phone_action, revoke.device_id) == ("revoke", 7)


def test_phone_dispatches_to_the_mobile_cli(monkeypatch):
    module = _load_module()
    seen = {}

    def fake(action, *, device_id=None, **kwargs):
        seen.update(action=action, device_id=device_id)
        return 0

    import core.mobile_cli as mobile_cli

    monkeypatch.setattr(mobile_cli, "run_phone_command", fake)
    assert module.main(["phone", "revoke", "3"]) == 0
    assert seen == {"action": "revoke", "device_id": 3}


def test_the_file_is_still_crlf_throughout():
    data = (REPO_ROOT / "scripts" / "palli_ctl.py").read_bytes()
    assert b"\n" not in data.replace(b"\r\n", b"")


# -- channels.mobile.enabled mirrors ENABLE_MOBILE_DOOR (review M5) ----------

def test_the_env_flag_is_mirrored_into_channels_mobile_like_the_others():
    controller = _load_module().LauncherController()
    on = controller._build_structured_settings_updates({"ENABLE_MOBILE_DOOR": True})
    off = controller._build_structured_settings_updates({"ENABLE_MOBILE_DOOR": False})
    assert on["channels.mobile.enabled"] is True
    assert off["channels.mobile.enabled"] is False
    assert "channels.mobile.enabled" not in controller._build_structured_settings_updates({})


def _channels(*, discord=False, telegram=False, mobile=False, slack=False):
    from types import SimpleNamespace

    def entry(on):
        return SimpleNamespace(enabled=on)

    return SimpleNamespace(discord=entry(discord), telegram=entry(telegram),
                           mobile=entry(mobile), slack=entry(slack))


def _configurator(monkeypatch, *, channels, answers):
    """Run `settings configure` against a fake settings service, answering
    the menu with ``answers``; returns what it printed and the changes it
    proposed."""
    from unittest.mock import MagicMock

    import core.settings_boot as settings_boot

    module = _load_module()
    controller = module.LauncherController()
    proposed: list = []
    snapshot = MagicMock()
    snapshot.channels = channels
    service = MagicMock()
    service.snapshot.return_value = snapshot

    def propose(changes, *, reason):
        proposed.append(changes)
        return MagicMock(diff=[])

    service.propose.side_effect = propose
    monkeypatch.setattr(settings_boot, "boot_settings", lambda: None)
    monkeypatch.setattr(settings_boot, "settings_service", service)
    monkeypatch.setattr(controller, "_prompt_yes_no", lambda *a, **k: True)
    replies = iter(answers)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(replies))
    return controller, proposed


def test_the_configure_readout_names_the_phone_door_when_it_is_on(monkeypatch, capsys):
    controller, _ = _configurator(monkeypatch, channels=_channels(discord=True, mobile=True), answers=["0"])
    assert controller._handle_settings_configure() == 0
    assert "Channels:       Discord, Mobile" in capsys.readouterr().out

    controller, _ = _configurator(monkeypatch, channels=_channels(discord=True), answers=["0"])
    assert controller._handle_settings_configure() == 0
    assert "Channels:       Discord\n" in capsys.readouterr().out


def test_a_factory_reset_resets_the_phone_door_too(monkeypatch):
    import pathlib
    from unittest.mock import MagicMock

    import core.settings_schema as settings_schema

    seed = MagicMock()
    seed.channels = _channels(discord=True)

    class Seed:
        @staticmethod
        def model_validate(data):
            return seed

    real_read_text = pathlib.Path.read_text

    def read_text(self, *args, **kwargs):
        if self.parts[-2:] == ("data", "palli.yaml"):
            return "{}"                    # the seed file is never opened
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", read_text)
    monkeypatch.setattr(settings_schema, "PalliSettings", Seed)
    controller, proposed = _configurator(
        monkeypatch, channels=_channels(mobile=True), answers=["6", "0"])
    assert controller._handle_settings_configure() == 0
    (reset,) = proposed
    assert reset["channels.mobile"] == {"enabled": False}
    assert reset["channels.discord"] == {"enabled": True}
