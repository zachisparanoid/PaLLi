"""CLI argument parsing — selects the right skin via flags."""
import pytest

from palli_config.cli import parse_args


def test_default_selects_menu():
    args = parse_args([])
    assert args.skin == "menu"


def test_wizard_flag_selects_wizard():
    args = parse_args(["--wizard"])
    assert args.skin == "wizard"


def test_dashboard_flag_selects_dashboard():
    args = parse_args(["--dashboard"])
    assert args.skin == "dashboard"


def test_wizard_and_dashboard_mutually_exclusive():
    with pytest.raises(SystemExit):
        parse_args(["--wizard", "--dashboard"])
