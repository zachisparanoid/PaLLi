"""`palli_ctl brain` (spec §3.2, §3.6, §7 — the owner's acceptance walk, automated).
Everything is injected: tmp copies of both files, a fake prober, a fake git
status, scripted input. Never the owner's files, never a socket."""
from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest
import yaml

from core.settings_service import SettingsService
from palli_config.brain_render import INTERNAL_WORDS
from palli_config.ctl_prefix import ctl_prefix
from palli_config.probes import ProbeResult

REPO_ROOT = Path(__file__).resolve().parents[2]
HOME_ENV = "LLM_PROVIDER=deepseek\r\nLLM_RESPONSE_MODEL=deepseek-v4-flash\r\nDEEPSEEK_MODEL=deepseek-v4-flash\r\nANTHROPIC_API_KEY=sk-ant-test\r\n"


def _load_module():
    module_path = REPO_ROOT / "scripts" / "palli_ctl.py"
    spec = importlib.util.spec_from_file_location("palli_ctl_brain_test_module", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def home(tmp_path):
    palli_yaml = tmp_path / "palli.yaml"
    # The SHIPPED config, not his. data/palli.yaml exists on his machine and in a
    # clone only because tests/conftest.py seeds it, which makes this fixture depend
    # on the order the suite happens to run in: the assembled public tree went 31
    # errors here on 2026-09-18 for exactly that reason, while the same file passed
    # alone. The example ships, so read the example.
    _source = REPO_ROOT / "data" / "palli.yaml"
    if not _source.is_file():
        _source = REPO_ROOT / "data" / "palli.example.yaml"
    shutil.copyfile(_source, palli_yaml)
    svc = SettingsService(yaml_path=palli_yaml)
    settings = svc.load()
    proposal = svc.propose(settings.presets["deepseek-primary"].overrides, reason="test")
    if proposal.change_id:
        svc.apply(proposal.change_id)
    env_local = tmp_path / ".env.local"
    env_local.write_bytes(HOME_ENV.encode("utf-8"))
    return {"env_local": env_local, "yaml": palli_yaml, "rollback": tmp_path / "brain-rollback.json"}


def _ok_prober(providers, env, models):
    return {p: ProbeResult(p, "logged in (test)", True) for p in providers}


def _run(module, home, argv, *, inputs=(), git_status="", prober=_ok_prober):
    args = module.build_parser().parse_args(["brain", *argv])
    answers = iter(inputs)
    lines: list[str] = []
    code = module.run_brain_command(
        args, env_local=home["env_local"], palli_yaml=home["yaml"], env_base=None,
        input_fn=lambda prompt="": next(answers, ""), prober=prober,
        git_status_fn=lambda: git_status, out=lines.append,
    )
    return code, "\n".join(lines)


def _routing(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))["llm"]["routing"]


def _no_jargon(text):
    for line in text.splitlines():
        if "palli_ctl " in line:
            continue
        for word in INTERNAL_WORDS:
            assert word not in line, line


def test_parser_knows_every_flag():
    module = _load_module()
    args = module.build_parser().parse_args(
        ["brain", "claude-oauth", "--except", "response", "--except", "vision", "--lane", "vision", "--to", "claude/haiku",
         "--dry-run", "--verbose", "--force", "--yes", "--pick", "--show", "--undo"]
    )
    assert args.command == "brain" and args.preset == "claude-oauth"
    assert args.except_lanes == ["response", "vision"] and args.lane == "vision" and args.to == "claude/haiku"
    assert args.dry_run and args.verbose and args.force and args.yes and args.pick and args.show and args.undo
    assert module.build_parser().parse_args(["brain"]).preset is None


def test_walk_step_1_show_is_the_default_and_free_of_jargon(home):
    module = _load_module()
    code, text = _run(module, home, [])
    assert code == 0 and "Who is thinking for PaLLi right now" in text and "she can use her hands" in text
    _no_jargon(text)
    code, loud = _run(module, home, ["--show", "--verbose"])
    assert code == 0 and "Under the hood" in loud and "openai_compat" in loud


def test_walk_step_2_pick_quits_cleanly_and_rejects_nonsense(home):
    module = _load_module()
    code, text = _run(module, home, ["--pick"], inputs=["q"])
    assert code == 0 and "Pick a setup:" in text and "Nothing changed." in text
    code, text = _run(module, home, ["--pick"], inputs=["99"])
    assert code == 1 and "Not one of the choices" in text
    assert home["env_local"].read_bytes() == HOME_ENV.encode("utf-8")


def test_walk_step_3_claude_oauth_is_refused_in_prose_with_the_next_move(home):
    module = _load_module()
    before = (home["env_local"].read_bytes(), home["yaml"].read_bytes())
    code, text = _run(module, home, ["claude-oauth"])
    assert code == 1
    assert "Not doing that." in text and "HER VOICE" in text and "never be able to act" in text
    assert "Nothing was changed." in text
    assert f"{ctl_prefix()} brain claude-oauth --except response" in text
    assert (home["env_local"].read_bytes(), home["yaml"].read_bytes()) == before
    _no_jargon(text)


def test_walk_step_4_dry_run_shows_everything_and_writes_nothing(home):
    module = _load_module()
    before = (home["env_local"].read_bytes(), home["yaml"].read_bytes())
    code, text = _run(module, home, ["claude-oauth", "--except", "response", "--dry-run"])
    assert code == 0
    assert "What would change" in text and "Not changing: her voice" in text
    assert "Every one of her ten lanes is accounted for above." in text
    assert "Seven background lanes stop costing money per token" in text
    assert "Changing 1 file: data/palli.yaml" in text
    assert "Dry run — nothing was written." in text
    assert (home["env_local"].read_bytes(), home["yaml"].read_bytes()) == before
    assert not home["rollback"].exists()


def test_walk_step_5_and_6_apply_then_undo(home):
    module = _load_module()
    original = (home["env_local"].read_bytes(), _routing(home["yaml"]))
    code, text = _run(module, home, ["claude-oauth", "--except", "response"], inputs=["y"])
    assert code == 0 and "Apply? [y/N]" not in text  # the prompt goes through input_fn, not out
    assert "Applied." in text and "takes effect when PaLLi next restarts" in text and f"{ctl_prefix()} brain --undo" in text
    routing = _routing(home["yaml"])
    assert routing["decision"] == {"provider": "claude", "model": "sonnet"}
    assert routing["response"] == {"provider": "deepseek", "model": "deepseek-v4-flash"}
    assert home["env_local"].read_bytes() == HOME_ENV.encode("utf-8")   # Ruling 11: her voice did not move, so .env.local was not touched
    assert home["rollback"].exists()

    code, text = _run(module, home, ["--undo"])
    assert code == 0 and "Put back" in text
    assert (home["env_local"].read_bytes(), _routing(home["yaml"])) == original
    code, text = _run(module, home, ["--undo"])
    assert code == 1 and "nothing to undo" in text


def test_a_declined_prompt_changes_nothing(home):
    module = _load_module()
    before = home["yaml"].read_bytes()
    code, text = _run(module, home, ["openai-primary"], inputs=["n"])
    assert code == 0 and "Nothing changed." in text and home["yaml"].read_bytes() == before


def test_walk_step_8_anthropic_primary_is_refused_and_points_at_the_kickoff(home):
    module = _load_module()
    code, text = _run(module, home, ["anthropic-primary"])
    assert code == 1 and "docs/superpowers/plans/2026-09-02-anthropic-brain-kickoff.md" in text and "Nothing was changed." in text


def test_walk_step_9_local_only_now_accounts_for_every_lane(home):
    module = _load_module()
    code, text = _run(module, home, ["local-only", "--except", "response", "--dry-run"])
    assert code == 0 and "Every one of her ten lanes is accounted for above." in text
    assert "start running free on your desktop" in text


def test_a_single_lane_pin_moves_one_row(home):
    module = _load_module()
    code, text = _run(module, home, ["--lane", "vision", "--to", "openai/gpt-4o", "--yes"])
    assert code == 0 and _routing(home["yaml"])["vision"] == {"provider": "openai", "model": "gpt-4o"}
    code, text = _run(module, home, ["--lane", "vision"])
    assert code == 1 and "--to <provider>/<model>" in text


def test_a_pin_to_an_unlisted_model_is_refused_before_any_write(home):
    module = _load_module()
    before = home["yaml"].read_bytes()
    code, text = _run(module, home, ["--lane", "vision", "--to", "openai/gpt-99", "--yes"])
    assert code == 1 and "Invalid change" in text and home["yaml"].read_bytes() == before
    assert "is not a model OpenAI offers" in text and "gpt-4o" in text
    assert "PalliSettings" not in text and "type=" not in text and "pydantic" not in text


def test_force_walks_past_a_refusal_and_says_so(home):
    module = _load_module()
    code, text = _run(module, home, ["claude-oauth", "--force", "--dry-run"])
    assert code == 0 and "overridden with --force" in text and "she would lose her hands" in text


def test_staged_env_local_refuses_the_write(home):
    module = _load_module()
    before = home["yaml"].read_bytes()
    code, text = _run(module, home, ["openai-primary", "--yes"], git_status="A  .env.local\n")
    assert code == 1 and "staged" in text and home["yaml"].read_bytes() == before


def test_already_there_is_a_no_op(home):
    module = _load_module()
    code, text = _run(module, home, ["deepseek-primary", "--yes"])
    assert code == 0 and "No change — she already runs that." in text


def test_show_never_prints_a_key_value(home):
    module = _load_module()
    _, text = _run(module, home, ["--show", "--verbose"])
    assert "sk-ant-test" not in text


def test_an_unknown_preset_lists_the_valid_names(home):
    module = _load_module()
    code, text = _run(module, home, ["gemini-primary"])
    assert code == 1 and "Invalid change:" in text and "not a preset" in text and "deepseek-primary" in text


def test_show_flag_is_read_only_even_with_a_preset(home):
    module = _load_module()
    before = (home["env_local"].read_bytes(), home["yaml"].read_bytes())
    code, text = _run(module, home, ["openai-primary", "--show"])
    assert code == 0 and "Who is thinking for PaLLi right now" in text and "What would change" not in text
    assert (home["env_local"].read_bytes(), home["yaml"].read_bytes()) == before


def test_undo_refuses_when_env_local_is_staged(home):
    module = _load_module()
    code, _ = _run(module, home, ["claude-oauth", "--except", "response", "--yes"])
    assert code == 0 and home["rollback"].exists()
    code, text = _run(module, home, ["--undo"], git_status="A  .env.local\n")
    assert code == 1 and "staged" in text and home["rollback"].exists()


def test_end_of_input_at_the_prompt_changes_nothing(home):
    module = _load_module()
    before = home["yaml"].read_bytes()
    args = module.build_parser().parse_args(["brain", "openai-primary"])
    lines: list[str] = []

    def eof(prompt=""):
        raise EOFError

    code = module.run_brain_command(
        args, env_local=home["env_local"], palli_yaml=home["yaml"], env_base=None,
        input_fn=eof, prober=_ok_prober, git_status_fn=lambda: "", out=lines.append,
    )
    assert code == 0 and "Nothing changed." in "\n".join(lines) and home["yaml"].read_bytes() == before


def test_a_refusal_never_probes_a_provider(home):
    """Final review F1 (spec §3.2 step 2): BLOCK aborts before a byte moves — and before a probe fires
    with the owner's key. anthropic-primary used to POST to api.anthropic.com just to say no."""
    module = _load_module()

    def raising(providers, env, models):
        raise AssertionError(f"probed {list(providers)} during a refusal")

    code, text = _run(module, home, ["anthropic-primary"], prober=raising)
    assert code == 1 and "Not doing that." in text and "docs/superpowers/plans/2026-09-02-anthropic-brain-kickoff.md" in text
    code, text = _run(module, home, ["claude-oauth"], prober=raising)
    assert code == 1 and "Not doing that." in text


def test_undo_dry_run_writes_nothing(home):
    module = _load_module()
    code, _ = _run(module, home, ["claude-oauth", "--except", "response", "--yes"])
    assert code == 0 and home["rollback"].exists()
    before = (home["env_local"].read_bytes(), home["yaml"].read_bytes(), home["rollback"].read_bytes())
    code, text = _run(module, home, ["--undo", "--dry-run"])
    assert code == 0 and "Dry run" in text and "would put back data/palli.yaml" in text
    assert (home["env_local"].read_bytes(), home["yaml"].read_bytes(), home["rollback"].read_bytes()) == before
