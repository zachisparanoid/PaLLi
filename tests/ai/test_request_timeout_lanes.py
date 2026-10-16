"""Patient/snappy per-request timeout split (2026-08-09).

One provider ceiling for everything was the wrong shape: 30s is right for
lanes a human is waiting on (replies, turn reads, steering verdicts) and
wrong for background organs that legitimately think for 30-60s on reasoning
models (night cycle, consolidation, appraisal, daydreams). The live failure
family this closes: ~daily "request timeout after 4 attempts" on background
lanes whose work was salvageable — the provider gave up at 30s four times in
a row on calls that finish at 40-50s.

The split is by log_context prefix. SNAPPY (30s) is the safe default: a new
lane someone forgets to classify stays fast and fails fast; nothing waiting
on a human can silently inherit a 60s stall. PATIENT (60s) is an explicit
allowlist of fire-and-forget organ lanes.
"""

from ai.llm_openai_compatible import OpenAICompatibleProvider


def timeout_for(context):
    return OpenAICompatibleProvider.request_timeout_for_context(context)


PATIENT = float(OpenAICompatibleProvider.PATIENT_REQUEST_TIMEOUT_SECONDS)
SNAPPY = float(OpenAICompatibleProvider.REQUEST_TIMEOUT_SECONDS)
DEEP = float(OpenAICompatibleProvider.DEEP_REQUEST_TIMEOUT_SECONDS)


def test_patient_is_longer_than_snappy():
    assert PATIENT > SNAPPY


def test_deep_is_longer_than_patient():
    """2026-08-22 04:02: the stake review's first attempt spent its whole
    4,000-token budget thinking (33.6 s ≈ 120 tok/s), the safety net bumped
    the budget to 9,600 — and a 9,600-token think needs ~80 s, so the 60 s
    patient ceiling killed it three times in a row. For the 04:00 family —
    nobody waits; a reasoning model's burn is the variable — time must grow
    with the budget: 240 s ≈ 16k+ tokens even on a slow night."""
    assert DEEP > PATIENT
    assert DEEP >= 240


class TestDeepLanes:
    """The 04:00 family — fire-and-forget, heavy prompts, a reasoning model
    that sometimes thinks 16k tokens: 240 s per attempt (2026-08-22)."""

    def test_night_cycle(self):
        assert timeout_for("night_cycle_daily_meaning") == DEEP

    def test_consolidation_lanes(self):
        assert timeout_for("consolidation_day_gist") == DEEP
        assert timeout_for("consolidation_fact_gist") == DEEP
        assert timeout_for("consolidation_reconcile") == DEEP

    def test_monologue_digests(self):
        assert timeout_for("monologue_weekly_digest_2026-W32") == DEEP
        assert timeout_for("monologue_monthly_digest_2026-08") == DEEP

    def test_narrative_self(self):
        assert timeout_for("narrative_self_author") == DEEP
        assert timeout_for("narrative_self_gate") == DEEP

    def test_passions(self):
        assert timeout_for("passions_author") == DEEP

    def test_stake_review_and_owner_picture(self):
        assert timeout_for("stake_review") == DEEP
        assert timeout_for("owner_picture") == DEEP


class TestPatientLanes:
    """The daytime background lanes — nobody waits, but they tick every few
    minutes and must not pile up: 60s."""

    def test_inner_monologue(self):
        assert timeout_for("inner_monologue_tick") == PATIENT

    def test_present_moment_cycle(self):
        assert timeout_for("present_moment_cycle") == PATIENT

    def test_stake_appraisal(self):
        assert timeout_for("stake_appraisal") == PATIENT

    def test_daydream(self):
        assert timeout_for("daydream_tick") == PATIENT

    def test_curiosity_research(self):
        assert timeout_for("curiosity_research") == PATIENT

    def test_arbiter_freshness(self):
        assert timeout_for("arbiter_freshness") == PATIENT


class TestSnappyLanes:
    """Anything a human is (or may be) waiting on stays at 30s."""

    def test_reply_generation(self):
        assert timeout_for("response_generation_12345") == SNAPPY
        assert timeout_for("operator_response_generation_12345") == SNAPPY

    def test_turn_read_and_cadence(self):
        assert timeout_for("turn_read") == SNAPPY
        assert timeout_for("cadence_check") == SNAPPY

    def test_steering_lanes(self):
        assert timeout_for("steering_judgment") == SNAPPY
        assert timeout_for("steering_supersede") == SNAPPY

    def test_the_first_look(self):
        """R14 (2026-10-12): the look at a message someone is waiting on."""
        assert timeout_for("first_look") == SNAPPY

    def test_the_second_look(self):
        """R15 (2026-10-13): the second look's widening and sifting run inside
        a reply the owner is waiting on, under a 10 s wall."""
        assert timeout_for("memory_reach_widen") == SNAPPY
        assert timeout_for("memory_reach_keep") == SNAPPY


class TestSafeDefault:
    """Unknown or malformed contexts must default SNAPPY — fail fast, never
    silently inherit the patient ceiling."""

    def test_unknown_lane(self):
        assert timeout_for("some_future_lane_nobody_classified") == SNAPPY

    def test_empty_and_none(self):
        assert timeout_for("") == SNAPPY
        assert timeout_for(None) == SNAPPY


class TestTheLanesTheAllowlistForgot:
    """2026-08-21 04:02: her nightly stake review failed with four timeouts
    ~31 s apart — the SNAPPY rhythm, not the patient one — because
    `stake_review` (born 08-18) and `owner_picture` (born 08-19) postdate
    the 08-09 allowlist and never joined it. The month's logs named the rest
    of the class: her post-turn reflect lane (memory notes, corrections,
    quest/fear filings) never named its lane at all, ran as `general` at the
    human ceiling, and was the #1 timing-out lane since the split — 144
    attempt timeouts, 25 exchanges whose reflection was lost without a
    word (08-09→08-21). All three are fire-and-forget organs: nobody is
    waiting on them, so the patient ceiling is the right one."""

    def test_stake_review_and_owner_picture_are_night_lanes(self):
        assert timeout_for("stake_review") == DEEP
        assert timeout_for("owner_picture") == DEEP

    def test_reflect_turn(self):
        assert timeout_for("reflect_turn") == PATIENT


def test_patient_ladder_ceiling_is_the_whole_retry_ladder():
    """An organ's outer envelope must hold the provider's ENTIRE patient
    ladder — (MAX_RETRIES+1) attempts at PATIENT plus the worst-case backoff
    between them — or the envelope kills the last attempt the provider was
    about to win. The 240 s the review and the picture shipped with (08-19)
    could not: 4×60 + (2+3+5) = 250. The number is the provider's to
    publish; the organs' envelope tests lean on it."""
    P = OpenAICompatibleProvider
    expected = (P.MAX_RETRIES + 1) * PATIENT + sum(
        min(P.BASE_DELAY * (2 ** a) + 1.0, P.MAX_DELAY) for a in range(P.MAX_RETRIES)
    )
    assert P.patient_ladder_ceiling_seconds() == expected == 250.0


def test_deep_ladder_ceiling_is_the_whole_deep_retry_ladder():
    """The 04:00 organs' outer envelopes lean on this: 4×240 + (2+3+5) = 970."""
    P = OpenAICompatibleProvider
    expected = (P.MAX_RETRIES + 1) * DEEP + sum(
        min(P.BASE_DELAY * (2 ** a) + 1.0, P.MAX_DELAY) for a in range(P.MAX_RETRIES)
    )
    assert P.deep_ladder_ceiling_seconds() == expected == 970.0


class TestEveryLaneIsClassified:
    """The class behind 08-21, closed for good: the allowlist is opt-in, so
    every organ born after it silently runs SNAPPY, and a lane that never
    names itself hides as `general`. This walks every provider call in ai/
    and core/ (AST, not grep — docstrings don't count) and demands:
      1. every call names its lane (`log_context=`), and
      2. every named lane is DEEP or PATIENT by prefix, or listed below as
         deliberately human-facing with the reason.
    A new organ that forgets either fails HERE, with instructions, instead
    of at 04:00 three nights later."""

    HUMAN_FACING = {  # someone is waiting on these — SNAPPY on purpose
        "agent_loop_main": "the ReAct loop — he is waiting on the reply",
        "agent_cadence_shape": "the cadence of the reply he is waiting on",
        "ambient_arrival_generation_": "the greeting when he arrives — moment-bound",
        "local_shell_intent_classifier": "in-reply classifier",
        "steering_judgment": "barge-in verdict mid-reply",
        "steering_supersede": "barge-in verdict mid-reply",
        "turn_read": "in-reply read of his message",
        "first_look": "the first look at a message someone is waiting on (R14)",
        "memory_reach_widen": "the second look inside a reply the owner is waiting on",
        "memory_reach_keep": "the second look's sifting, same reply",
        "reply_govern": "the register fix's compression call — he is waiting on the reply",
    }

    @staticmethod
    def _scan():
        """→ (unnamed_calls, lanes) over ai/ and core/.

        A call names its lane with `log_context=<literal|f-string|name>`
        or forwards `**kwargs`. Literal/f-string prefixes become lanes;
        variables can't be judged statically (their literals are caught at
        the site that defines them — `_make_llm_call(context="…")`)."""
        import ast
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        unnamed, lanes = [], {}
        for sub in ("ai", "core"):
            for path in sorted((root / sub).rglob("*.py")):
                tree = ast.parse(path.read_text(encoding="utf-8-sig", errors="replace"))
                rel = str(path.relative_to(root))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Dict):  # {"log_context": "…"} fed to chat_completion(**kwargs)
                        for k, v in zip(node.keys, node.values):
                            if (isinstance(k, ast.Constant) and k.value == "log_context"
                                    and isinstance(v, ast.Constant) and isinstance(v.value, str) and v.value):
                                lanes.setdefault(v.value, f"{rel}:{node.lineno}")
                        continue
                    if not isinstance(node, ast.Call):
                        continue
                    fn = node.func
                    name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                    is_provider_call = name in ("chat_completion", "chat_completion_stream")
                    is_lane_factory = name.endswith("llm_call") or "llm" in name.lower()
                    if not (is_provider_call or is_lane_factory):
                        continue
                    kws = {k.arg: k.value for k in node.keywords}
                    key = "log_context" if is_provider_call else "context"
                    val = kws.get(key)
                    if val is None and is_provider_call and len(node.args) >= 5:
                        val = node.args[4]  # (messages, model, temperature, max_tokens, log_context, …)
                    if is_provider_call and val is None and None not in kws:
                        unnamed.append(f"{rel}:{node.lineno}")
                        continue
                    def _lits(v):  # literal / f-string prefix / both arms of a conditional
                        if isinstance(v, ast.Constant) and isinstance(v.value, str):
                            return [v.value]
                        if isinstance(v, ast.JoinedStr) and v.values and isinstance(v.values[0], ast.Constant):
                            return [str(v.values[0].value)]
                        if isinstance(v, ast.IfExp):
                            return _lits(v.body) + _lits(v.orelse)
                        return []
                    for lit in _lits(val):
                        if lit:
                            lanes.setdefault(lit, f"{rel}:{node.lineno}")
        return unnamed, lanes

    def test_every_provider_call_names_its_lane(self):
        unnamed, _ = self._scan()
        assert not unnamed, (
            "these provider calls pass no log_context and run as `general` — "
            "name the lane (it is how the timeout split, the logs and the "
            "activity monitor see it): " + ", ".join(unnamed)
        )

    def test_every_named_lane_is_classified(self):
        _, lanes = self._scan()
        assert len(lanes) >= 20, f"the scan went blind — only {sorted(lanes)}"
        unclassified = sorted(
            f"{lane!r} ({where})" for lane, where in lanes.items()
            if timeout_for(lane) not in (PATIENT, DEEP)
            and not any(lane.startswith(h) for h in self.HUMAN_FACING)
        )
        assert not unclassified, (
            "unclassified LLM lanes — each must be either a fire-and-forget "
            "organ (add its prefix to OpenAICompatibleProvider.DEEP_LANE_PREFIXES for the "
            "04:00 family or PATIENT_LANE_PREFIXES for the daytime organs) "
            "or something a human waits on (add it to HUMAN_FACING here with "
            "the reason): " + ", ".join(unclassified)
        )

    # Measures run by hand from evals/, which the scan above never walks (it
    # reads ai/ and core/): no conversation waits on them. Listed so the lane
    # is still classified on purpose; each must still be named where it says.
    HAND_RUN = {
        "memory_reach_judge": (
            "the memory-that-reaches replay's judge (R15), run by hand: a 300-token "
            "verdict the one running it waits on at the terminal, so the default "
            "30 s stands",
            "evals/memory_reach_replay/harness.py",
        ),
    }

    def test_the_hand_run_measures_are_still_true(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        for lane, (_reason, where) in self.HAND_RUN.items():
            if not (root / where).is_file():   # the public copy ships without evals/
                continue
            source = (root / where).read_text(encoding="utf-8")
            assert f'JUDGE_LANE = "{lane}"' in source, f"{lane!r} is no longer named in {where}"
            assert "log_context=JUDGE_LANE" in source, f"{where} no longer calls on {lane!r}"
            assert timeout_for(lane) == SNAPPY, f"{lane!r} moved tier — update its reason here"

    def test_the_human_facing_list_is_still_true(self):
        """Every entry here must still exist in the code (a retired lane
        should leave this list, or it rots into folklore) and must still be
        SNAPPY (someone promoting it to PATIENT must move it)."""
        _, lanes = self._scan()
        for prefix in self.HUMAN_FACING:
            assert any(l.startswith(prefix) for l in lanes), f"{prefix!r} no longer exists in ai/ or core/"
            assert timeout_for(prefix) == SNAPPY, f"{prefix!r} is PATIENT now — move it out of HUMAN_FACING"
