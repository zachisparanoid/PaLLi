"""Phase 1 ship gate.

Prove that the SHIPPED configuration and .env.template agree about the
fresh-install defaults, so a clone that reads one and boots the other
does not start life with two answers. The user's .env.local may override
either at runtime; that is theirs.

The shipped configuration is data/palli.example.yaml (step 28 task 3) —
see the settings_snapshot fixture for why it is no longer data/palli.yaml.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.config_schema import BRAIN_KEY_BY_PROVIDER
from core.settings_schema import PalliSettings
from core.settings_service import SettingsService


REPO_ROOT = Path(__file__).resolve().parents[2]


def _seed_presets() -> dict:
    """The presets as SHIPPED — read from the seed, never from his own file."""
    import yaml as _yaml

    doc = _yaml.safe_load((REPO_ROOT / "data" / "palli.example.yaml").read_text(encoding="utf-8"))
    return (doc or {}).get("presets") or {}


SEED_PRESETS = _seed_presets()


@pytest.fixture(scope="module")
def settings_snapshot() -> PalliSettings:
    """THE SEED, which since step 28 task 3 is data/palli.example.yaml.

    It used to be data/palli.yaml, back when that file WAS the shipped seed. It is not
    any more: it is the owner's live, running configuration, and this module's gate —
    "the fresh-install defaults agree with .env.template" — has nothing to say about it.
    Pointed there, the gate went red the moment the shipped default provider moved to
    Ollama (task 4 fix 1) while his own instance stayed on a paid one, which is not a
    ship problem and never was. core/settings_boot.seed_from_example() copies THIS file
    on a first boot, so this is the file a stranger's .env.template has to agree with."""
    svc = SettingsService(yaml_path=REPO_ROOT / "data" / "palli.example.yaml")
    return svc.load()


def test_seed_structural_sanity(settings_snapshot: PalliSettings):
    """Seed YAML has all the structural slots Phase 2 will migrate."""
    s = settings_snapshot
    assert s.version == 1
    assert len(s.providers) >= 4
    # Routing has all 8 required task slots (schema enforces this, sanity asserting)
    routing = s.llm.routing
    for task in ("response", "decision", "analysis", "memory",
                 "proactive", "reflection", "web_search", "vision"):
        entry = getattr(routing, task)
        assert entry.provider in s.providers
        assert entry.model


def test_seed_matches_env_template_default_provider(settings_snapshot: PalliSettings):
    """The shipped env brain and the shipped yaml routing may differ ONLY while
    there is no brain at all — and then only because nothing runs and the door
    closes the gap the moment one arrives.

    PREMISE REVERSED 2026-09-18, on the owner's call that a stranger must be
    prompted to choose between a local and a cloud brain. The template used to
    name the same local model server the seed routes to, which meant a fresh
    clone booted "ready" and walked straight past the door: no key was missing,
    so setup mode never fired, and anyone without that server got her outage
    line on every message. The template now names a key-needing provider with
    the key blank, so a clone always lands on the door.

    That leaves the two files naming different providers before anyone chooses.
    It is inert: with no brain, every keyless key-needing provider is a
    NullProvider and every lane is gated, so no lane runs at all. And the first
    brain accepted at the door moves the yaml routing to match
    (`api/setup.py::_move_the_lanes`), so they agree from the first moment
    either one is used. The guard that matters now is coverage, pinned by
    test_the_door_moves_every_lane_the_seed_pins below.
    """
    template_path = REPO_ROOT / ".env.template"
    template_text = template_path.read_text(encoding="utf-8")
    for line in template_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("LLM_PROVIDER="):
            template_default = stripped.split("=", 1)[1].strip()
            if template_default == settings_snapshot.llm.default_provider:
                return
            # They differ, so the shipped state MUST be the keyless one that
            # sends a stranger to the door, not a silent disagreement.
            from core.llm_capabilities import PROVIDERS, Auth

            assert PROVIDERS[template_default].auth is Auth.API_KEY, (
                f"the template brain {template_default!r} differs from the seed's "
                f"{settings_snapshot.llm.default_provider!r} but needs no key, so nothing "
                "sends a stranger to the door and the two just disagree"
            )
            key_line = f"{PROVIDERS[template_default].model_env_key.replace('_MODEL', '_API_KEY')}="
            assert any(
                line.strip() == key_line.rstrip("=") + "=" for line in template_text.splitlines()
            ), f"{key_line} must ship blank, or the clone never reaches the door"
            return
    pytest.fail("LLM_PROVIDER not found in .env.template")


def test_seed_channels_match_env_template(settings_snapshot: PalliSettings):
    """Seed's channel enablement matches .env.template's ENABLE_*_BOT keys.

    Wave 0 dropped ENABLE_TELEGRAM_BOT and ENABLE_SLACK_BOT from the
    template along with the slack/telegram channel adapters. Discord
    remains the only first-class channel.
    """
    template_path = REPO_ROOT / ".env.template"
    template_text = template_path.read_text(encoding="utf-8")
    mapping = {
        "ENABLE_DISCORD_BOT": settings_snapshot.channels.discord.enabled,
    }
    for key, seed_value in mapping.items():
        found = False
        for line in template_text.splitlines():
            stripped = line.strip()
            if stripped.startswith(f"{key}="):
                template_value = stripped.split("=", 1)[1].strip().lower() == "true"
                assert seed_value == template_value, f"{key}: seed={seed_value} template={template_value}"
                found = True
                break
        assert found, f"{key} not found in .env.template"


def _template_value(key: str) -> str:
    text = (REPO_ROOT / ".env.template").read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{key}="):
            return stripped.split("=", 1)[1].split("#")[0].strip()
    pytest.fail(f"{key} not found in .env.template")


def test_the_template_ships_voice_off(settings_snapshot: PalliSettings):
    """The second leg of the keyless claim, guarded directly (review I3).

    The reviewer's proposed mechanism was the fresh-clone validation check — flip this to
    true and a clone dies on "DEEPGRAM_API_KEY is required when STT_PROVIDER=deepgram".
    That route is GONE as of fix round 2: the shipped preset moved the voice providers to
    faster_whisper + pyttsx3, which need no key, so voice-on now validates fine and no
    validation test can kill the mutant. It is still off on purpose, and for reasons
    validation cannot see: the runtime wants ffmpeg on PATH, faster-whisper pulls a model
    down on first use, and Discord voice needs Connect/Speak on the bot invite. A stranger
    opts into all three; they are not a default. So the line is pinned here, by itself.
    """
    assert _template_value("ENABLE_VOICE_FEATURES") == "false"
    # and the providers it would turn on are the keyless pair, not the hosted ones
    assert settings_snapshot.voice.stt_provider.value == "faster_whisper"
    assert settings_snapshot.voice.tts_provider.value == "pyttsx3"


def test_the_template_and_the_seed_name_the_same_local_model(settings_snapshot: PalliSettings):
    """Her voice reads LLM_PROVIDER + LLM_RESPONSE_MODEL from env, every other lane reads
    the yaml. Ship them disagreeing and nine lanes go one way while she goes another —
    which is exactly the split `palli_ctl brain` exists to keep closed."""
    # PREMISE NARROWED 2026-09-18 (owner's call: a stranger picks at the door).
    # The template's brain is now the keyless cloud one that sends them there,
    # so it no longer names the seed's local server — see
    # test_seed_matches_env_template_default_provider for why that is safe.
    # What still must not drift is the LOCAL option's own defaults: the model in
    # the template is what the door offers for a local server, and it has to be
    # the one the `local-only` preset and the seed's routing actually name, or
    # the door hands them a model their server has never heard of.
    local_only = (SEED_PRESETS.get("local-only") or {}).get("overrides") or {}
    local_response = local_only.get("llm.routing.response") or {}
    assert _template_value("LOCAL_LLM_MODEL") == local_response.get("model")
    assert _template_value("LOCAL_LLM_MODEL") == settings_snapshot.llm.routing.response.model
    assert _template_value("OLLAMA_MODEL") == settings_snapshot.llm.routing.vision.model


def test_the_template_names_no_keyed_provider_for_the_per_reply_lanes():
    """Two env keys bypass the yaml routing and fire on EVERY reply: LLM_PROVIDER (her
    voice) and CADENCE_MODEL (the register/length pre-pass). The template shipped
    `CADENCE_MODEL=anthropic::claude-haiku-4-5-...`, so a keyless install sent one call per
    message to a provider whose key nobody had set — config validation never sees it,
    because the cadence model is not a validated field. Empty is the right answer: the
    judge then follows her voice, which is local."""
    from core.llm_capabilities import PROVIDERS, Auth

    for key in ("CADENCE_MODEL", "PALLI_CADENCE_MODEL"):
        assert _template_value(key) == "", (
            f"{key} names a model in .env.template; on a keyless install that is one "
            "failed call per reply"
        )
    # PREMISE REVERSED 2026-09-18. This used to demand the shipped brain need no
    # key, because a keyed provider on a keyless install meant one failed call per
    # reply. That harm is now impossible by construction: a key-needing provider
    # with no key IS setup mode, where every such provider is a NullProvider and
    # every lane is gated, so she makes no calls at all until a key arrives. The
    # rule that replaces it: the shipped brain may need a key, but then that key
    # must ship BLANK — a shipped key-needing brain with a value would be someone
    # else's credential in the template.
    provider = _template_value("LLM_PROVIDER")
    if PROVIDERS[provider].auth is not Auth.NONE:
        assert PROVIDERS[provider].auth is Auth.API_KEY, (
            f"the shipped LLM_PROVIDER={provider!r} needs {PROVIDERS[provider].auth.value}, "
            "which no stranger can satisfy from the door"
        )
        key_name = BRAIN_KEY_BY_PROVIDER[provider]
        assert _template_value(key_name) == "", (
            f"the shipped brain needs {key_name} and the template does not ship it blank"
        )


def test_the_door_moves_every_lane_the_seed_pins(settings_snapshot: PalliSettings):
    """The guard that replaces "the two files must name the same provider".

    The shipped env brain and the shipped yaml routing are allowed to differ
    while no brain exists, because the first brain accepted at the door moves
    the routing to match. That is only true if the door covers EVERY lane the
    seed pins. A lane the seed names and the door forgets would keep pointing at
    a model server the person never installed, for good — the exact split the
    old premise guarded, arriving by a different road.

    The one lane allowed to stay behind is a lane that needs to read images,
    and only when the chosen brain has no eyes: moving it would trade a lane
    that might work for one that certainly cannot.
    """
    from api.setup import _lane_overrides
    from core.llm_capabilities import LANES

    seed_lanes = {
        lane for lane in settings_snapshot.llm.routing.model_fields_set
    } or set(LANES)

    # a brain that can see takes everything with it
    seeing = _lane_overrides("openai", "gpt-4o")
    for lane in seed_lanes:
        assert f"llm.routing.{lane}" in seeing, (
            f"the seed pins {lane!r} but the door leaves it behind"
        )
    assert seeing["llm.default_provider"] == "openai"

    # a blind brain leaves exactly the vision lanes, and nothing else
    blind = _lane_overrides("deepseek", "deepseek-chat")
    left_behind = {
        lane for lane in seed_lanes if f"llm.routing.{lane}" not in blind
    }
    assert left_behind == {lane for lane in seed_lanes if LANES[lane].needs_vision}, (
        f"a blind brain left {left_behind!r} behind; only image lanes may stay"
    )
