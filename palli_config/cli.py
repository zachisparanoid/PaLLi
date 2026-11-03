"""CLI entry point. Default skin is menu; --wizard / --dashboard switch."""
from __future__ import annotations

import argparse
from typing import Sequence


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="palli_configure",
        description="Interactive PaLLi configuration TUI",
    )
    skin_group = parser.add_mutually_exclusive_group()
    skin_group.add_argument(
        "--wizard", action="store_const", const="wizard", dest="skin",
        help="Launch the wizard skin (linear walkthrough)",
    )
    skin_group.add_argument(
        "--dashboard", action="store_const", const="dashboard", dest="skin",
        help="Launch the dashboard skin (single live screen)",
    )
    parser.set_defaults(skin="menu")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    from pathlib import Path
    repo_root = Path(__file__).resolve().parents[1]
    env_local = repo_root / ".env.local"
    palli_yaml = repo_root / "data" / "palli.yaml"
    if args.skin == "menu":
        from palli_config.skins.menu import MenuApp
        MenuApp(env_local=env_local, palli_yaml=palli_yaml).run()
    elif args.skin == "wizard":
        from palli_config.skins.wizard import WizardApp
        WizardApp(env_local=env_local, palli_yaml=palli_yaml).run()
    elif args.skin == "dashboard":
        from palli_config.skins.dashboard import DashboardApp
        DashboardApp(env_local=env_local, palli_yaml=palli_yaml).run()
    else:
        raise ValueError(f"unknown skin: {args.skin!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
