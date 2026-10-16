from __future__ import annotations

from ai.prompt_loader import EXPECTED_PROMPTS, PromptLoader


def test_load_prompt_and_cache(tmp_path):
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    file_path = prompts_dir / "alpha.txt"
    file_path.write_text("First", encoding="utf-8")

    loader = PromptLoader(prompts_dir=str(prompts_dir))
    assert loader.load_prompt("alpha") == "First"

    # Cached value should remain even if file changes.
    file_path.write_text("Second", encoding="utf-8")
    assert loader.load_prompt("alpha") == "First"


def test_reload_prompt_refreshes_cache(tmp_path):
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    file_path = prompts_dir / "beta.txt"
    file_path.write_text("One", encoding="utf-8")

    loader = PromptLoader(prompts_dir=str(prompts_dir))
    assert loader.load_prompt("beta") == "One"

    file_path.write_text("Two", encoding="utf-8")
    assert loader.reload_prompt("beta") == "Two"


def test_load_prompt_handles_missing_and_empty(tmp_path):
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "empty.txt").write_text("", encoding="utf-8")

    loader = PromptLoader(prompts_dir=str(prompts_dir))
    assert loader.load_prompt("does_not_exist") is None
    assert loader.load_prompt("empty") is None


def test_inspect_prompt_inventory_reports_missing_empty_and_loaded(tmp_path):
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "loaded.txt").write_text("ready", encoding="utf-8")
    (prompts_dir / "empty.txt").write_text("", encoding="utf-8")

    loader = PromptLoader(prompts_dir=str(prompts_dir))
    inventory = loader.inspect_prompt_inventory(
        expected_prompts=["loaded", "empty", "missing"],
    )

    assert inventory["status"] == {
        "loaded": True,
        "empty": False,
        "missing": False,
    }
    assert inventory["missing"] == ["missing"]
    assert inventory["empty"] == ["empty"]
    assert inventory["errors"] == {}
    assert inventory["loaded_count"] == 1
    assert inventory["total_count"] == 3


class TestEveryUsedPromptIsWatched:
    """The class behind 08-26, closed for good: EXPECTED_PROMPTS is the
    startup banner's whole idea of what a healthy prompt set looks like,
    and nothing ever forced it to match the code. It drifted for 27 roadmap
    steps — 14 of its 27 names had no caller left, and 31 live prompts
    (every selfhood organ among them) went unwatched, so a deleted
    `stake_appraisal.txt` would have surfaced at 04:00, not at boot.

    This walks every `load_prompt(...)` call in the project (AST, not grep —
    docstrings and regex patterns don't count) and demands the list and the
    code agree in both directions. A new organ that forgets to register its
    prompt fails HERE, with instructions.
    """

    # evals/ (R15, T6): a hand-run measure loads its judge by name too.
    SEARCH_DIRS = ("ai", "core", "api", "bots", "voice", "scripts", "evals")

    DYNAMIC = {  # reached through a constant, so no literal to find — see the source
        "turn_reflect": "core/turn/reflect.py:40 PROMPT_NAME",
        "narrative_self_my_story": "ai/narrative_self.py:117 _DOC_PROMPTS",
        "narrative_self_story_of_us": "ai/narrative_self.py:118 _DOC_PROMPTS",
        "narrative_self_care_spine": "ai/narrative_self.py:119 _DOC_PROMPTS",
        "narrative_self_voice_identity": "ai/narrative_self.py:120 _DOC_PROMPTS",
        "consolidation_day_gist": "ai/consolidation.py:262 flag-picked",
        "consolidation_day_gist_continuity": "ai/consolidation.py:260 flag-picked",
    }

    @classmethod
    def _scan(cls):
        """→ {prompt_name: "file.py:line"} for every prompt the code can ask for."""
        import ast
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        used = dict(cls.DYNAMIC)
        if not (root / "evals").is_dir():
            # The public copy ships without evals/ (private tooling); its judge's
            # prompt still ships, loaded by name from there in the owner's tree.
            used.setdefault("memory_reach_judge", "evals/memory_reach_replay/harness.py (not shipped)")
        for sub in cls.SEARCH_DIRS:
            for path in sorted((root / sub).rglob("*.py")):
                if "__pycache__" in str(path):
                    continue
                tree = ast.parse(path.read_text(encoding="utf-8-sig", errors="replace"))
                rel = str(path.relative_to(root))
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call) or not node.args:
                        continue
                    func = node.func
                    name = (
                        func.attr if isinstance(func, ast.Attribute)
                        else func.id if isinstance(func, ast.Name)
                        else None
                    )
                    if name not in ("load_prompt", "reload_prompt"):
                        continue
                    arg = node.args[0]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value:
                        used.setdefault(arg.value, f"{rel}:{node.lineno}")
        return used

    def test_every_used_prompt_is_watched(self):
        used = self._scan()
        unwatched = {n: s for n, s in used.items() if n not in EXPECTED_PROMPTS}
        assert not unwatched, (
            "prompts the code loads but the startup banner never checks — add them "
            f"to EXPECTED_PROMPTS in ai/prompt_loader.py: {sorted(unwatched)}"
        )

    def test_no_dead_names_in_expected(self):
        used = self._scan()
        dead = sorted(n for n in EXPECTED_PROMPTS if n not in used)
        assert not dead, (
            "EXPECTED_PROMPTS names prompts nothing loads any more; drop them from "
            f"ai/prompt_loader.py: {dead}"
        )

    def test_every_watched_prompt_has_a_file(self):
        from pathlib import Path

        prompts_dir = Path(__file__).resolve().parents[2] / "prompts"
        missing = sorted(n for n in EXPECTED_PROMPTS if not (prompts_dir / f"{n}.txt").is_file())
        assert not missing, (
            "EXPECTED_PROMPTS names prompt files that do not exist — the startup "
            f"banner would log [error] for these on every boot: {missing}"
        )
