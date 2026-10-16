"""Her Hands (step 33) — the capability sweep.

Spec: docs/superpowers/specs/2026-08-11-her-hands-design.md
Owner decisions verbatim in spec §0: full trust (no ceremonies, no caps),
owner-only (strangers get no levers), workbench stays hand-free.
All Discord objects are fakes — no live client in tests.
"""
from types import SimpleNamespace

import pytest

import core.agent.hands_runtime as hr


def _fake_client():
    """A guild with two voice channels, two text channels, three members."""
    vc_general = SimpleNamespace(name="General", id=101, __class__=type("VC", (), {}))
    vc_den = SimpleNamespace(name="the den", id=102)
    tx_general = SimpleNamespace(name="general", id=201)
    tx_prints = SimpleNamespace(name="print-status", id=202)
    m_owner = SimpleNamespace(name="sam", display_name="Sam", id=1, bot=False)
    m_eddie = SimpleNamespace(name="eddie4821", display_name="Eddie", id=2, bot=False,
                              status="online")
    m_bot = SimpleNamespace(name="palli", display_name="PaLLi", id=3, bot=True)
    guild = SimpleNamespace(
        name="home", voice_channels=[vc_general, vc_den],
        text_channels=[tx_general, tx_prints],
        members=[m_owner, m_eddie, m_bot],
        system_channel=tx_general,
    )
    return SimpleNamespace(guilds=[guild]), guild


@pytest.fixture(autouse=True)
def _clean_holder():
    hr.register_client(None)
    yield
    hr.register_client(None)


def test_holder_roundtrip():
    assert hr.get_client() is None
    client, _ = _fake_client()
    hr.register_client(client)
    assert hr.get_client() is client


class TestResolvers:
    def test_exact_beats_fuzzy(self):
        client, g = _fake_client()
        hr.register_client(client)
        match, cands = hr.resolve_voice_channel("General")
        assert match is g.voice_channels[0]

    def test_case_insensitive_and_contains(self):
        client, g = _fake_client()
        hr.register_client(client)
        match, _ = hr.resolve_voice_channel("den")
        assert match is g.voice_channels[1]

    def test_member_by_display_name_fuzzy(self):
        client, g = _fake_client()
        hr.register_client(client)
        match, _ = hr.resolve_member("Edddy")
        assert match is g.members[1]  # Eddie — difflib closest

    def test_ambiguous_returns_candidates_not_match(self):
        client, g = _fake_client()
        g.text_channels.append(SimpleNamespace(name="general-2", id=203))
        hr.register_client(client)
        match, cands = hr.resolve_text_channel("gener")
        assert match is None
        assert "general" in cands and "general-2" in cands

    def test_unknown_gives_nearest_names(self):
        client, _ = _fake_client()
        hr.register_client(client)
        match, cands = hr.resolve_member("Quentin")
        assert match is None
        assert cands  # nearest names for her judgment, never empty silence

    def test_no_client_is_calm(self):
        match, cands = hr.resolve_member("Eddie")
        assert match is None and cands == []


def test_list_all_members_marks_bots_and_filters():
    client, _ = _fake_client()
    hr.register_client(client)
    everyone = hr.list_all_members()
    assert {m["display_name"] for m in everyone} == {"Sam", "Eddie", "PaLLi"}
    assert [m for m in everyone if m["display_name"] == "PaLLi"][0]["bot"] is True
    hits = hr.list_all_members("edd")
    assert hits and hits[0]["display_name"] == "Eddie"


def test_list_all_members_carries_online_state_when_known():
    # §1.4: presence intent is on, so Member.status is real data; members
    # without one (fakes, partials) render as unknown — never invented.
    client, _ = _fake_client()
    hr.register_client(client)
    by_name = {m["display_name"]: m for m in hr.list_all_members()}
    assert by_name["Eddie"]["status"] == "online"
    assert by_name["Sam"]["status"] == ""


def test_fallback_text_channel_prefers_system():
    client, g = _fake_client()
    assert hr.fallback_text_channel(g) is g.system_channel
    g.system_channel = None
    assert hr.fallback_text_channel(g) is g.text_channels[0]


def test_flag_in_schema_default_false():
    from core.settings_schema import FeaturesConfig

    assert FeaturesConfig().her_hands is False


def test_flag_in_yaml_shipped_true():
    from pathlib import Path

    text = Path("data/palli.yaml").read_text(encoding="utf-8")
    assert "her_hands: true" in text


def test_registration_respects_flag(monkeypatch):
    import core.agent.builtin_tools as bt

    monkeypatch.setattr(bt, "_her_hands_enabled", lambda: False)
    off_names = bt.build_default_registry().all_names()
    monkeypatch.setattr(bt, "_her_hands_enabled", lambda: True)
    on_names = bt.build_default_registry().all_names()
    assert set(on_names) - set(off_names) == set(
        n for n in on_names if n in (
            "join_voice_channel", "leave_voice", "speak_line",
            "list_members", "send_message", "set_presence",
        )
    )
    # Flag off = byte-identical belt (nothing else changed either).
    assert off_names == [n for n in on_names if n not in (
        "join_voice_channel", "leave_voice", "speak_line",
        "list_members", "send_message", "set_presence",
    )]


def test_workbench_registry_never_has_hands(monkeypatch):
    import core.agent.builtin_tools as bt

    monkeypatch.setattr(bt, "_her_hands_enabled", lambda: True)
    wb = bt.build_default_registry(surface="workbench").all_names()
    for name in ("join_voice_channel", "leave_voice", "speak_line",
                 "list_members", "send_message", "set_presence"):
        assert name not in wb


import asyncio


def _ctx(owner=True, channel_id=201):
    from core.agent.tools import ToolContext
    return ToolContext(turn_owner_id=1, is_owner=owner, surface="discord_text",
                       channel_id=channel_id)


class _FakeVC:
    def __init__(self):
        self.disconnected = False
    async def disconnect(self):
        self.disconnected = True


class TestVoiceTrio:
    def _tools(self):
        from core.agent.builtin_tools.her_hands import build_her_hands_tools
        by_name = {t.name: t for t in build_her_hands_tools()}
        return by_name

    def test_all_three_registered_owner_only_no_approval(self):
        tools = self._tools()
        for name in ("join_voice_channel", "leave_voice", "speak_line"):
            assert name in tools
            assert tools[name].owner_only is True
            assert tools[name].requires_approval is False  # full trust

    def test_join_resolves_connects_and_listens(self, monkeypatch):
        client, g = _fake_client()
        hr.register_client(client)
        connected = {}

        async def fake_connect(cls=None):
            connected["vc"] = _FakeVC()
            return connected["vc"]
        g.voice_channels[0].connect = fake_connect

        import core.agent.builtin_tools.her_hands as hh
        monkeypatch.setattr(hh, "_voice_features_on", lambda: True)
        monkeypatch.setattr(hh, "_voice_runtime_ready", lambda: (True, []))
        started = {}

        async def fake_start(vc, vch_id, text_id, user_id, **kw):
            started.update(vc=vc, vch=vch_id, text=text_id, user=user_id)
            return True
        monkeypatch.setattr(hh, "_start_listening", fake_start)
        deeds = []
        monkeypatch.setattr(hh, "_deed", lambda kind, d: deeds.append((kind, d)))

        tool = self._tools()["join_voice_channel"]
        out = asyncio.run(
            tool.execute({"channel_name": "general"}, _ctx()))
        assert "General" in out
        assert started["vch"] == 101 and started["text"] == 201 and started["user"] == 1
        assert deeds and deeds[0][0] == "hands"

    def test_join_without_ctx_channel_uses_fallback(self, monkeypatch):
        client, g = _fake_client()
        hr.register_client(client)

        async def fake_connect(cls=None):
            return _FakeVC()
        g.voice_channels[0].connect = fake_connect
        import core.agent.builtin_tools.her_hands as hh
        monkeypatch.setattr(hh, "_voice_features_on", lambda: True)
        monkeypatch.setattr(hh, "_voice_runtime_ready", lambda: (True, []))
        started = {}

        async def fake_start(vc, vch_id, text_id, user_id, **kw):
            started["text"] = text_id
            return True
        monkeypatch.setattr(hh, "_start_listening", fake_start)
        monkeypatch.setattr(hh, "_deed", lambda *a: None)

        tool = self._tools()["join_voice_channel"]
        asyncio.run(
            tool.execute({"channel_name": "general"}, _ctx(channel_id=0)))
        assert started["text"] == 201  # guild system channel

    def test_join_calm_when_features_off(self, monkeypatch):
        import core.agent.builtin_tools.her_hands as hh
        monkeypatch.setattr(hh, "_voice_features_on", lambda: False)
        tool = self._tools()["join_voice_channel"]
        out = asyncio.run(
            tool.execute({"channel_name": "general"}, _ctx()))
        assert "disabled" in out.lower()

    def test_join_ambiguous_hands_her_the_candidates(self, monkeypatch):
        client, g = _fake_client()
        g.voice_channels.append(SimpleNamespace(name="general 2", id=103))
        hr.register_client(client)
        import core.agent.builtin_tools.her_hands as hh
        monkeypatch.setattr(hh, "_voice_features_on", lambda: True)
        monkeypatch.setattr(hh, "_voice_runtime_ready", lambda: (True, []))
        tool = self._tools()["join_voice_channel"]
        out = asyncio.run(
            tool.execute({"channel_name": "gener"}, _ctx()))
        assert "General" in out and "general 2" in out  # her call now

    def test_join_move_stops_old_session_before_disconnect(self, monkeypatch):
        # The move branch must tear down the OLD listening session before
        # disconnecting — a bare disconnect zombies the sinks bound to the
        # old connection (the parked step-33 zombie).
        client, g = _fake_client()
        old_vc = _FakeVC()
        old_vc.channel = SimpleNamespace(name="old-room", id=777)
        g.voice_client = old_vc
        hr.register_client(client)

        async def fake_connect(cls=None):
            return _FakeVC()
        g.voice_channels[0].connect = fake_connect

        import core.agent.builtin_tools.her_hands as hh
        monkeypatch.setattr(hh, "_voice_features_on", lambda: True)
        monkeypatch.setattr(hh, "_voice_runtime_ready", lambda: (True, []))
        order = []

        async def fake_stop(channel_id):
            order.append(("stop", channel_id))
            return True
        monkeypatch.setattr(hh, "_stop_listening", fake_stop)

        async def fake_start(vc, vch_id, text_id, user_id, **kw):
            return True
        monkeypatch.setattr(hh, "_start_listening", fake_start)
        monkeypatch.setattr(hh, "_deed", lambda *a: None)
        orig = old_vc.disconnect

        async def dis():
            order.append(("disconnect", None))
            await orig()
        old_vc.disconnect = dis

        tool = self._tools()["join_voice_channel"]
        out = asyncio.run(
            tool.execute({"channel_name": "general"}, _ctx()))
        assert order[0] == ("stop", 777)
        assert order[1][0] == "disconnect"
        assert "moved" in out

    def test_leave_stops_then_disconnects(self, monkeypatch):
        client, g = _fake_client()
        vc = _FakeVC()
        vc.channel = g.voice_channels[0]
        g.voice_client = vc
        hr.register_client(client)
        import core.agent.builtin_tools.her_hands as hh
        order = []

        async def fake_stop(channel_id):
            order.append("stop")
            return True
        monkeypatch.setattr(hh, "_stop_listening", fake_stop)
        monkeypatch.setattr(hh, "_deed", lambda *a: order.append("deed"))
        orig = vc.disconnect

        async def dis():
            order.append("disconnect")
            await orig()
        vc.disconnect = dis

        tool = self._tools()["leave_voice"]
        out = asyncio.run(
            tool.execute({}, _ctx()))
        assert order[:2] == ["stop", "disconnect"]
        assert "General" in out

    def test_leave_calm_when_not_in_voice(self):
        client, g = _fake_client()
        g.voice_client = None
        hr.register_client(client)
        tool = self._tools()["leave_voice"]
        out = asyncio.run(
            tool.execute({}, _ctx()))
        assert "not in a voice channel" in out.lower()

    def test_speak_line_uses_session_entry(self, monkeypatch):
        client, g = _fake_client()
        vc = _FakeVC()
        vc.channel = g.voice_channels[0]
        g.voice_client = vc
        hr.register_client(client)
        import core.agent.builtin_tools.her_hands as hh
        spoken = {}

        async def fake_say(channel_id, text):
            spoken.update(ch=channel_id, text=text)
            return True
        monkeypatch.setattr(hh, "_say_in_session", fake_say)
        monkeypatch.setattr(hh, "_deed", lambda *a: None)
        tool = self._tools()["speak_line"]
        out = asyncio.run(
            tool.execute({"text": "hello out loud"}, _ctx()))
        assert spoken == {"ch": 101, "text": "hello out loud"}

    def test_speak_calm_without_session(self, monkeypatch):
        # Batch 4, R15 (2026-09-28): with no session the line now goes to him
        # as text on the chat he used last (tests/tools/test_speak_line_fallback.py).
        # With no chat either it is a ToolFailure — still calm, still says join.
        import core.agent.builtin_tools.her_hands as hh
        from core.agent.tool_outcome import ToolFailure

        async def no_door(owner_id):
            return None
        monkeypatch.setattr(hh, "last_used_door", no_door)
        client, g = _fake_client()
        g.voice_client = None
        hr.register_client(client)
        tool = self._tools()["speak_line"]
        out = asyncio.run(
            tool.execute({"text": "hi"}, _ctx()))
        assert isinstance(out, ToolFailure)
        assert "join" in str(out).lower()


class TestPeopleAndReach:
    def _tools(self):
        from core.agent.builtin_tools.her_hands import build_her_hands_tools
        return {t.name: t for t in build_her_hands_tools()}

    def test_list_members_output_and_query(self):
        client, _ = _fake_client()
        hr.register_client(client)
        tool = self._tools()["list_members"]
        out = asyncio.run(
            tool.execute({}, _ctx()))
        assert "Eddie" in out and "Sam" in out and "bot" in out.lower()
        # §1.4 online state: shown when known, silently absent when not.
        assert "— online" in out
        assert "Sam (@sam, id 1)\n" in out + "\n"  # no invented status
        out2 = asyncio.run(
            tool.execute({"query": "edd"}, _ctx()))
        assert "Eddie" in out2 and "Sam" not in out2

    def test_send_to_channel_rides_custody(self, monkeypatch):
        client, _ = _fake_client()
        hr.register_client(client)
        import core.agent.builtin_tools.her_hands as hh
        sent = {}

        async def fake_outbound(**kw):
            sent.update(kw)
            return True
        monkeypatch.setattr(hh, "_record_outbound", fake_outbound)
        tool = self._tools()["send_message"]
        out = asyncio.run(
            tool.execute({"to": "#print-status", "text": "done!"}, _ctx()))
        assert sent["channel_id"] == 202 and sent["origin"] == "her_hands"
        assert "third_party" not in (sent.get("extra") or {})
        assert "print-status" in out

    def test_send_to_person_is_dm_with_third_party_extra(self, monkeypatch):
        client, g = _fake_client()
        dm = SimpleNamespace(id=999)

        async def create_dm():
            return dm
        g.members[1].create_dm = create_dm
        hr.register_client(client)
        import core.agent.builtin_tools.her_hands as hh
        sent = {}

        async def fake_outbound(**kw):
            sent.update(kw)
            return True
        monkeypatch.setattr(hh, "_record_outbound", fake_outbound)
        tool = self._tools()["send_message"]
        out = asyncio.run(
            tool.execute({"to": "Edddy", "text": "print finished"}, _ctx()))
        assert sent["channel_id"] == 999
        assert sent["extra"]["third_party"] is True
        assert sent["extra"]["recipient"] == "Eddie"
        assert "Eddie" in out

    def test_send_by_raw_channel_mention_routes(self, monkeypatch):
        # §1.5: list_members hands her ids; mentions and snowflakes must
        # route without name resolution.
        client, _ = _fake_client()
        hr.register_client(client)
        import core.agent.builtin_tools.her_hands as hh
        sent = {}

        async def fake_outbound(**kw):
            sent.update(kw)
            return True
        monkeypatch.setattr(hh, "_record_outbound", fake_outbound)
        tool = self._tools()["send_message"]
        out = asyncio.run(
            tool.execute({"to": "<#202>", "text": "done!"}, _ctx()))
        assert sent["channel_id"] == 202
        assert "print-status" in out

    def test_send_by_member_mention_is_dm(self, monkeypatch):
        client, g = _fake_client()
        dm = SimpleNamespace(id=888)

        async def create_dm():
            return dm
        g.members[1].create_dm = create_dm
        hr.register_client(client)
        import core.agent.builtin_tools.her_hands as hh
        sent = {}

        async def fake_outbound(**kw):
            sent.update(kw)
            return True
        monkeypatch.setattr(hh, "_record_outbound", fake_outbound)
        tool = self._tools()["send_message"]
        out = asyncio.run(
            tool.execute({"to": "<@2>", "text": "hey"}, _ctx()))
        assert sent["channel_id"] == 888
        assert sent["extra"]["recipient"] == "Eddie"
        assert "Eddie" in out

    def test_send_unknown_id_is_calm(self, monkeypatch):
        client, _ = _fake_client()
        hr.register_client(client)
        import core.agent.builtin_tools.her_hands as hh
        called = []

        async def fake_outbound(**kw):
            called.append(kw)
            return True
        monkeypatch.setattr(hh, "_record_outbound", fake_outbound)
        tool = self._tools()["send_message"]
        out = asyncio.run(
            tool.execute({"to": "<#55555>", "text": "hi"}, _ctx()))
        assert called == []
        assert "55555" in out

    def test_parse_raw_id_shapes(self):
        assert hr.parse_raw_id("<#202>") == 202
        assert hr.parse_raw_id("<@2>") == 2
        assert hr.parse_raw_id("<@!42>") == 42
        assert hr.parse_raw_id("123456789012345678") == 123456789012345678
        # bare short numbers are NAMES, not ids — "2024" the channel wins
        assert hr.parse_raw_id("2024") is None
        assert hr.parse_raw_id("Eddie") is None

    def test_send_to_reply_channel_refused(self, monkeypatch):
        client, _ = _fake_client()
        hr.register_client(client)
        import core.agent.builtin_tools.her_hands as hh
        called = []

        async def fake_outbound(**kw):
            called.append(kw)
            return True
        monkeypatch.setattr(hh, "_record_outbound", fake_outbound)
        tool = self._tools()["send_message"]
        out = asyncio.run(
            tool.execute({"to": "general", "text": "hi"}, _ctx(channel_id=201)))
        assert called == []
        assert "just reply" in out.lower()

    def test_send_ambiguous_gives_candidates(self, monkeypatch):
        client, g = _fake_client()
        g.text_channels.append(SimpleNamespace(name="general-2", id=203))
        hr.register_client(client)
        tool = self._tools()["send_message"]
        out = asyncio.run(
            tool.execute({"to": "gener", "text": "hi"}, _ctx()))
        assert "general" in out and "general-2" in out

    def test_send_failure_surfaces(self, monkeypatch):
        client, _ = _fake_client()
        hr.register_client(client)
        import core.agent.builtin_tools.her_hands as hh

        async def boom(**kw):
            raise RuntimeError("discord said no")
        monkeypatch.setattr(hh, "_record_outbound", boom)
        tool = self._tools()["send_message"]
        with pytest.raises(RuntimeError):
            asyncio.run(
                tool.execute({"to": "#print-status", "text": "x"}, _ctx()))


class TestPresenceAndSelfKnowledge:
    def test_set_presence_maps_args(self, monkeypatch):
        import discord
        client, _ = _fake_client()
        calls = {}

        async def change_presence(**kw):
            calls.update(kw)
        client.change_presence = change_presence
        hr.register_client(client)
        import core.agent.builtin_tools.her_hands as hh
        deeds = []
        monkeypatch.setattr(hh, "_deed", lambda kind, d: deeds.append((kind, d)))
        from core.agent.builtin_tools.her_hands import build_her_hands_tools
        tool = {t.name: t for t in build_her_hands_tools()}["set_presence"]
        out = asyncio.run(
            tool.execute({"status": "idle", "activity": "in voice with him"}, _ctx()))
        assert "idle" in out
        assert calls["status"] is discord.Status.idle
        assert isinstance(calls["activity"], discord.CustomActivity)
        assert calls["activity"].name == "in voice with him"
        assert deeds and deeds[0][0] == "hands"

    def test_set_presence_clears_activity(self, monkeypatch):
        client, _ = _fake_client()
        calls = {}

        async def change_presence(**kw):
            calls.update(kw)
        client.change_presence = change_presence
        hr.register_client(client)
        import core.agent.builtin_tools.her_hands as hh
        monkeypatch.setattr(hh, "_deed", lambda *a: None)
        from core.agent.builtin_tools.her_hands import build_her_hands_tools
        tool = {t.name: t for t in build_her_hands_tools()}["set_presence"]
        asyncio.run(
            tool.execute({"status": "online", "activity": ""}, _ctx()))
        assert calls["activity"] is None  # empty activity clears

    def test_set_presence_no_client_calm(self):
        from core.agent.builtin_tools.her_hands import build_her_hands_tools
        tool = {t.name: t for t in build_her_hands_tools()}["set_presence"]
        out = asyncio.run(
            tool.execute({"status": "idle"}, _ctx()))
        assert out == "My Discord body isn't connected right now."

    def test_set_presence_invalid_status_calm(self):
        client, _ = _fake_client()
        hr.register_client(client)
        from core.agent.builtin_tools.her_hands import build_her_hands_tools
        tool = {t.name: t for t in build_her_hands_tools()}["set_presence"]
        out = asyncio.run(
            tool.execute({"status": "sleepy"}, _ctx()))
        assert "online" in out and "idle" in out  # lists the valid ones

    def test_six_tools_total(self):
        from core.agent.builtin_tools.her_hands import build_her_hands_tools
        assert [t.name for t in build_her_hands_tools()] == [
            "join_voice_channel", "leave_voice", "speak_line",
            "list_members", "send_message", "set_presence",
        ]

    def test_whoami_names_her_hands_and_heartbeat(self):
        from core.agent.builtin_tools.whoami import WhoamiTool
        # The paragraph gates on owner + hands actually registered (the
        # parked is_owner condition, fixed 2026-08-14) — so this fixture
        # now carries a hands tool, as her live owner session does.
        tool = WhoamiTool(loop_description="ReAct",
                          available_tool_names=["send_message"])
        out = asyncio.run(
            tool.execute({}, _ctx()))
        low = out.lower()
        assert "hands" in low
        assert "heartbeat" in low
        assert "workbench" in low  # the standing wall, stated


class TestDuplexPresence:
    """Step 27 §5: expect_minutes feeds the time sense; a debounce keeps the
    signage from flickering; both ONLY when duplex is on."""

    def _client_recording(self):
        client, guild = _fake_client()
        calls: list[dict] = []

        async def change_presence(**kw):
            calls.append(kw)
        client.change_presence = change_presence
        return client, calls

    def _ctx(self):
        from core.agent.tools import ToolContext
        return ToolContext(turn_owner_id=1, is_owner=True, surface="discord_text", channel_id=555)

    def test_expect_minutes_sets_expectation_and_notes_activity_when_on(self, monkeypatch):
        import core.agent.builtin_tools.her_hands as hh
        import core.agent.steering as st
        from core.agent import duplex as dx
        monkeypatch.setattr(dx, "duplex_enabled", lambda: True)
        hh._last_presence_change = 0.0
        st.clear_run_state("discord:555")
        client, calls = self._client_recording()
        hr.register_client(client)
        out = asyncio.run(hh.SetPresenceTool().execute(
            {"activity": "heads down on the adapter", "expect_minutes": 12}, self._ctx()))
        assert out.startswith("Presence set")
        assert st.get_expectation("discord:555") == 12.0
        assert st.pop_presence_activity("discord:555") == "heads down on the adapter"
        assert calls and calls[0]["activity"] is not None

    def test_second_change_within_debounce_is_declined_when_on(self, monkeypatch):
        import core.agent.builtin_tools.her_hands as hh
        from core.agent import duplex as dx
        monkeypatch.setattr(dx, "duplex_enabled", lambda: True)
        hh._last_presence_change = 0.0
        client, calls = self._client_recording()
        hr.register_client(client)
        asyncio.run(hh.SetPresenceTool().execute({"activity": "one"}, self._ctx()))
        out = asyncio.run(hh.SetPresenceTool().execute({"activity": "two"}, self._ctx()))
        assert "a moment ago" in out
        assert len(calls) == 1

    def test_no_debounce_and_no_notes_when_off(self):
        import core.agent.builtin_tools.her_hands as hh
        import core.agent.steering as st
        hh._last_presence_change = 0.0
        st.clear_run_state("discord:555")
        client, calls = self._client_recording()
        hr.register_client(client)
        asyncio.run(hh.SetPresenceTool().execute({"activity": "one", "expect_minutes": 5}, self._ctx()))
        asyncio.run(hh.SetPresenceTool().execute({"activity": "two"}, self._ctx()))
        assert len(calls) == 2                                   # today's behavior
        assert st.get_expectation("discord:555") is None
        assert st.pop_presence_activity("discord:555") is None


class TestSendMessageOnTheSecondDoor:
    """Her hand learns a second door (telegram door, 2026-09-14, task 2).

    `send_message` resolved every destination through the Discord client's
    caches, so from a Telegram chat it could only ever say "nothing and
    nobody I can see matches that". A `telegram:<chat_id>` destination is
    an ADDRESS, not a name to resolve — no lookup, straight to custody
    under the Telegram platform, so the line lands in the transcript she
    will actually read next.
    """

    def _tool(self):
        import core.agent.builtin_tools.her_hands as hh
        return hh.SendMessageTool()

    def test_a_telegram_destination_goes_out_the_telegram_door(self, monkeypatch):
        import core.agent.builtin_tools.her_hands as hh
        sent = {}

        async def fake_outbound(**kw):
            sent.update(kw)
            return True

        monkeypatch.setattr(hh, "_record_outbound", fake_outbound)
        out = asyncio.run(self._tool().execute(
            {"to": "telegram:424242", "text": "the part came in"}, _ctx()))

        assert sent["platform"] == "telegram"
        assert sent["channel_id"] == 424242
        assert sent["surface_id"] == "424242"
        assert sent["origin"] == "her_hands"
        assert "telegram chat 424242" in out

    def test_the_short_form_and_a_negative_group_id_both_route(self, monkeypatch):
        import core.agent.builtin_tools.her_hands as hh
        sent = {}

        async def fake_outbound(**kw):
            sent.update(kw)
            return True

        monkeypatch.setattr(hh, "_record_outbound", fake_outbound)
        asyncio.run(self._tool().execute(
            {"to": "tg:-1001234567890", "text": "in the shop group"}, _ctx()))

        assert sent["platform"] == "telegram"
        assert sent["channel_id"] == -1001234567890

    def test_this_very_chat_is_still_just_a_reply(self, monkeypatch):
        """The one line that keeps her from talking to herself, on the
        second door too."""
        import core.agent.builtin_tools.her_hands as hh
        from core.agent.tools import ToolContext

        called = []

        async def fake_outbound(**kw):
            called.append(kw)
            return True

        monkeypatch.setattr(hh, "_record_outbound", fake_outbound)
        ctx = ToolContext(turn_owner_id=1, is_owner=True,
                          surface="telegram_text", channel_id=424242)
        out = asyncio.run(self._tool().execute(
            {"to": "telegram:424242", "text": "hello"}, ctx))

        assert "just reply" in out
        assert called == []

    def test_the_tool_description_names_the_telegram_form(self):
        assert "telegram:" in self._tool().description
