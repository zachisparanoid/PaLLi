"""Every `palli_ctl` command the docs promise is a command the CLI actually has.

Step 28, task 4. README.md and SETUP.md are the first two files a stranger reads,
and the only two that hand them commands to type. A command that has been renamed,
nested under a new subparser, or never existed at all fails *at the reader*, on
their first evening, with an argparse usage dump and no way to tell whether they
mistyped it or the project lied to them. So the docs are pinned to the parser:
every fenced `palli_ctl.py ...` line in either file is lifted out and parsed by
the real `build_parser()`. The docs cannot drift from the CLI without this going
red, and the CLI cannot drop a subcommand the docs still promise.

The pin is `parse_args`, not a name grep: a documented `settings get llm` proves
the nested subparser and its positional too, not merely that `settings` exists.
"""
from __future__ import annotations

import argparse
import importlib.util
import shlex
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DOCS: tuple[Path, ...] = (REPO / "README.md", REPO / "SETUP.md")

# Where a documented line is a shell pipeline (`... doctor --json | jq .`), the
# CLI's own arguments stop at the operator; everything after belongs to the shell.
_SHELL_OPERATORS = frozenset({"|", "||", "&&", ";", "&", ">", ">>", "<", "2>", "2>&1"})


@dataclass(frozen=True)
class Invocation:
    doc: str
    line_no: int
    raw: str
    args: tuple[str, ...] | None  # None = the line could not be read as a command


def extract_invocations(path: Path) -> list[Invocation]:
    """Every `palli_ctl.py` command inside a fenced block of one doc."""
    if not path.is_file():
        return []
    found: list[Invocation] = []
    in_fence = False
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        stripped = line.strip()
        if not in_fence or "palli_ctl.py" not in stripped or stripped.startswith("#"):
            continue
        try:
            tokens = shlex.split(stripped, comments=True)
        except ValueError:
            found.append(Invocation(path.name, line_no, stripped, None))
            continue
        index = next(
            (i for i, token in enumerate(tokens) if token.endswith("palli_ctl.py")), None
        )
        if index is None:
            found.append(Invocation(path.name, line_no, stripped, None))
            continue
        args: list[str] = []
        for token in tokens[index + 1:]:
            if token in _SHELL_OPERATORS:
                break
            args.append(token)
        found.append(Invocation(path.name, line_no, stripped, tuple(args)))
    return found


INVOCATIONS: list[Invocation] = [item for doc in DOCS for item in extract_invocations(doc)]


def _ident(item: Invocation) -> str:
    return f"{item.doc}:{item.line_no}"


@pytest.fixture(scope="module")
def parser() -> argparse.ArgumentParser:
    module_path = REPO / "scripts" / "palli_ctl.py"
    spec = importlib.util.spec_from_file_location("palli_ctl_doc_pin", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_parser()


@pytest.fixture(scope="module")
def subcommands(parser: argparse.ArgumentParser) -> frozenset[str]:
    for action in parser._actions:  # noqa: SLF001 — argparse exposes no public reader
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            return frozenset(action.choices)
    raise AssertionError("scripts/palli_ctl.py build_parser() has no subparsers")


def test_both_release_docs_exist():
    """A missing doc must fail here, not quietly empty the parametrisation below."""
    missing = [doc.name for doc in DOCS if not doc.is_file()]
    assert missing == [], f"the release docs are missing: {missing}"


def test_the_docs_actually_hand_the_reader_commands(subcommands: frozenset[str]):
    """Guards against a vacuous pass: a rewrite that drops every fenced command
    block would leave the parametrised test with nothing to check and go green."""
    named = {item.args[0] for item in INVOCATIONS if item.args}
    assert named, "neither README.md nor SETUP.md documents a single palli_ctl command"
    assert named <= subcommands, f"not commands: {sorted(named - subcommands)}"
    assert len(named) >= 6, (
        "the setup path a stranger follows runs through more of the CLI than this — "
        f"only {sorted(named)} are documented"
    )


@pytest.mark.parametrize("item", INVOCATIONS, ids=_ident)
def test_a_documented_command_is_a_real_command(
    item: Invocation, parser: argparse.ArgumentParser, subcommands: frozenset[str]
):
    assert item.args is not None, f"{_ident(item)} is not a readable command: {item.raw}"
    assert item.args, f"{_ident(item)} runs palli_ctl.py with no subcommand: {item.raw}"
    assert item.args[0] in subcommands, (
        f"{_ident(item)} documents {item.args[0]!r}, which scripts/palli_ctl.py does not "
        f"have. Real subcommands: {sorted(subcommands)}"
    )
    try:
        parser.parse_args(list(item.args))
    except SystemExit as exc:  # argparse exits on an unknown flag or a bad choice
        raise AssertionError(
            f"{_ident(item)} does not parse against the real CLI (exit {exc.code}): {item.raw}"
        ) from exc
