"""Friends' web searches, the floor on how many (R14, T7; plan
docs/2026-10-12-one-first-look-plan.md §2b.7a).

The owner, 10-12 21:42: "yeah a few searches per hour per friend is fine";
22:03 (Q5): 5 per friend per rolling hour, counted per platform account
across both apps in one ledger, 60 per room per day, 5 results per search,
the owner's own searches never counted. Over a cap the tool says so plainly,
as a fact for PaLLi to weigh; the message is never dropped.

A resource floor in code, like ``bots/common/guest_gate.py``: never a
messaging rule. Stand-in names only: Robin is the owner, Sam and Alex guests.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from core.agent.tools import ToolContext

SAM = 4242
ALEX = 4343
ROOM = 8800002
OTHER_ROOM = 8800009


class Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def _ctx(who: int = SAM, *, room: int = ROOM, surface: str = "discord_text",
         owner: bool = False) -> ToolContext:
    return ToolContext(turn_owner_id=who, is_owner=owner, surface=surface, channel_id=room)


def _settings(**features):
    return SimpleNamespace(snapshot=lambda: SimpleNamespace(features=SimpleNamespace(**features)))


@pytest.fixture
def budget():
    from core.agent.guest_search_budget import GuestSearchBudget

    clock = Clock()
    b = GuestSearchBudget(clock=clock)
    b.clock = clock
    return b


def _take(budget, ctx=None, *, per_hour=5, per_room_day=60):
    return budget.take_for(ctx or _ctx(), per_hour=per_hour, per_room_day=per_room_day)


# ── the ledger ──────────────────────────────────────────────────────────────

def test_the_fifth_search_is_allowed_and_the_sixth_refused_within_the_hour(budget):
    for _ in range(5):
        assert _take(budget) is None
    refusal = _take(budget)
    assert refusal is not None
    assert refusal.startswith("web search isn't available for this person right now")
    assert "5 searches an hour" in refusal
    assert "the next one frees up in 60 minutes" in refusal


def test_the_window_rolls(budget):
    _take(budget)
    budget.clock.t += 600
    for _ in range(4):
        _take(budget)
    assert _take(budget) is not None
    budget.clock.t += 3000            # 3,600 s after the first: it falls off
    assert "frees up" not in (_take(budget) or "")
    assert _take(budget) is not None  # the four at +600 still stand


def test_the_refusal_says_when_the_next_one_frees_up(budget):
    for _ in range(5):
        _take(budget)
    budget.clock.t += 3600 - 90       # the oldest frees up in 90 s
    assert "the next one frees up in 2 minutes" in _take(budget)
    budget.clock.t += 50              # 40 s left
    assert "the next one frees up in 1 minute." in _take(budget)


def test_two_friends_are_two_budgets(budget):
    for _ in range(5):
        _take(budget, _ctx(SAM))
    assert _take(budget, _ctx(SAM)) is not None
    assert _take(budget, _ctx(ALEX)) is None


def test_one_friend_on_two_apps_is_two_accounts(budget):
    """PaLLi cannot link a Discord account to a Telegram one: two budgets."""
    for _ in range(5):
        _take(budget, _ctx(SAM, surface="discord_text"))
    assert _take(budget, _ctx(SAM, surface="telegram_text")) is None


def test_a_friend_counts_across_rooms_on_one_app(budget):
    for _ in range(5):
        _take(budget, _ctx(SAM, room=ROOM))
    assert _take(budget, _ctx(SAM, room=OTHER_ROOM)) is not None


def test_a_room_takes_60_a_day_and_then_says_so(budget):
    for i in range(60):
        assert _take(budget, _ctx(10_000 + i)) is None
        budget.clock.t += 60
    refusal = _take(budget, _ctx(99_999))
    assert refusal is not None
    assert refusal.startswith("web search isn't available in this room right now")
    assert "60 searches a day" in refusal
    # the first was 3,600 s ago; it frees up a day after it was made
    assert "the next one frees up in 23 hours" in refusal
    assert _take(budget, _ctx(99_999, room=OTHER_ROOM)) is None


def test_a_refused_search_is_not_counted(budget):
    for _ in range(5):
        _take(budget, _ctx(SAM))
    for _ in range(10):
        assert _take(budget, _ctx(SAM)) is not None
    # the room saw five searches, not fifteen
    for i in range(55):
        assert _take(budget, _ctx(20_000 + i)) is None
    assert _take(budget, _ctx(30_000)) is not None


def test_the_owner_never_counts_and_never_reads_the_ledger(budget):
    for _ in range(5):
        _take(budget, _ctx(SAM))
    budget._friends = None            # any read of the ledger would raise
    budget._rooms = None
    for _ in range(20):
        assert _take(budget, _ctx(SAM, owner=True)) is None


def test_cap_zero_refuses_every_search_with_the_plain_result(budget):
    refusal = _take(budget, per_hour=0)
    assert refusal is not None and refusal.startswith(
        "web search isn't available for this person right now")
    assert "frees up" not in refusal
    room_zero = _take(budget, _ctx(ALEX), per_room_day=0)
    assert room_zero is not None and room_zero.startswith(
        "web search isn't available in this room right now")


def test_the_shared_ledger_is_one_instance_for_both_doors():
    from core.agent import guest_search_budget as gsb

    assert gsb.shared_budget() is gsb.shared_budget()


def test_the_limits_are_read_at_call_time_with_the_schema_defaults_when_unbooted():
    import core.settings_boot as settings_boot
    from core.agent import guest_search_budget as gsb
    from core.settings_schema import FeaturesConfig

    assert FeaturesConfig().guest_web_searches_per_hour == 5
    assert FeaturesConfig().guest_web_searches_per_room_per_day == 60
    assert gsb.RESULTS_PER_SEARCH == 5
    with patch.object(settings_boot, "settings_service", SimpleNamespace(snapshot=lambda: None)):
        assert gsb.limits() == (5, 60)
    with patch.object(settings_boot, "settings_service", _settings(
            guest_web_searches_per_hour=2, guest_web_searches_per_room_per_day=7)):
        assert gsb.limits() == (2, 7)
    with patch.object(settings_boot, "settings_service", _settings(
            guest_web_searches_per_hour=3, guest_web_searches_per_room_per_day=9)):
        assert gsb.limits() == (3, 9)


def test_the_settings_refuse_a_negative_cap():
    from pydantic import ValidationError

    from core.settings_schema import FeaturesConfig

    with pytest.raises(ValidationError):
        FeaturesConfig(guest_web_searches_per_hour=-1)
    with pytest.raises(ValidationError):
        FeaturesConfig(guest_web_searches_per_room_per_day=-1)


# ── the tool ───────────────────────────────────────────────────────────────

def _payload(n: int = 8):
    return {"results": [{"title": f"result {i}", "url": f"https://example.org/{i}",
                         "summary": f"summary {i}"} for i in range(n)]}


async def _search(tool, ctx, *, client=None, settings=None):
    import core.settings_boot as settings_boot

    client = client or SimpleNamespace(search=AsyncMock(return_value=_payload()))
    with patch("ai.web_search.web_search_engine", SimpleNamespace(exa_client=client)), \
            patch.object(type(tool), "_api_key_present", staticmethod(lambda: True)), \
            patch.object(settings_boot, "settings_service", settings or _settings(
                guest_web_searches_per_hour=5, guest_web_searches_per_room_per_day=60)):
        return await tool.execute({"query": "who won the match", "num_results": 8}, ctx)


@pytest.fixture
def fresh_shared(monkeypatch):
    from core.agent import guest_search_budget as gsb

    clock = Clock()
    b = gsb.GuestSearchBudget(clock=clock)
    b.clock = clock
    monkeypatch.setattr(gsb, "_shared", b)
    return b


@pytest.mark.asyncio
async def test_the_guest_tool_refuses_the_sixth_with_a_plain_string(fresh_shared):
    from core.agent.builtin_tools.web_search import WebSearchTool
    from core.agent.tool_outcome import ToolFailure
    from core.untrusted import HEADER

    tool = WebSearchTool(for_guest=True)
    client = SimpleNamespace(search=AsyncMock(return_value=_payload()))
    for _ in range(5):
        out = await _search(tool, _ctx(), client=client)
        assert HEADER.split("{")[0] in out
    out = await _search(tool, _ctx(), client=client)
    assert isinstance(out, str) and not isinstance(out, ToolFailure)
    assert out.startswith("web search isn't available for this person right now")
    assert "content from the web" not in out          # its own words, not wrapped
    assert client.search.await_count == 5               # the sixth never reached Exa


@pytest.mark.asyncio
async def test_a_guests_search_returns_five_results_at_most(fresh_shared):
    from core.agent.builtin_tools.web_search import WebSearchTool

    tool = WebSearchTool(for_guest=True)
    client = SimpleNamespace(search=AsyncMock(return_value=_payload(8)))
    out = await _search(tool, _ctx(), client=client)
    assert client.search.call_args.kwargs["num_results"] == 5
    assert "result 4" in out and "result 5" not in out


@pytest.mark.asyncio
async def test_a_failed_exa_call_counts(fresh_shared):
    from core.agent.builtin_tools.web_search import WebSearchTool
    from core.agent.tool_outcome import ToolFailure

    tool = WebSearchTool(for_guest=True)
    client = SimpleNamespace(search=AsyncMock(side_effect=RuntimeError("exa down")))
    for _ in range(5):
        assert isinstance(await _search(tool, _ctx(), client=client), ToolFailure)
    out = await _search(tool, _ctx(), client=client)
    assert isinstance(out, str) and "isn't available for this person" in out
    assert client.search.await_count == 5


@pytest.mark.asyncio
async def test_the_setting_is_read_at_call_time(fresh_shared):
    from core.agent.builtin_tools.web_search import WebSearchTool

    tool = WebSearchTool(for_guest=True)
    one = _settings(guest_web_searches_per_hour=1, guest_web_searches_per_room_per_day=60)
    assert "web results" in await _search(tool, _ctx(), settings=one)
    assert "isn't available" in await _search(tool, _ctx(), settings=one)
    three = _settings(guest_web_searches_per_hour=3, guest_web_searches_per_room_per_day=60)
    assert "web results" in await _search(tool, _ctx(), settings=three)


@pytest.mark.asyncio
async def test_an_empty_query_or_a_missing_key_spends_nothing(fresh_shared):
    from core.agent.builtin_tools.web_search import WebSearchTool

    tool = WebSearchTool(for_guest=True)
    assert "non-empty query" in await tool.execute({"query": " "}, _ctx())
    with patch.object(type(tool), "_api_key_present", staticmethod(lambda: False)):
        assert "not configured" in await tool.execute({"query": "x"}, _ctx())
    assert fresh_shared.take_for(_ctx(), per_hour=1, per_room_day=60) is None


@pytest.mark.asyncio
async def test_the_owners_tool_never_reads_the_ledger(fresh_shared):
    from core.agent.builtin_tools.web_search import WebSearchTool

    fresh_shared._friends = None
    fresh_shared._rooms = None
    tool = WebSearchTool()
    client = SimpleNamespace(search=AsyncMock(return_value=_payload(8)))
    for _ in range(8):
        out = await _search(tool, _ctx(owner=True), client=client)
        assert "web results" in out
    assert client.search.call_args.kwargs["num_results"] == 8   # her own reach is unchanged


# ── both doors, one ledger, and the message is still answered ──────────────

@pytest.fixture
def guest_search_on(monkeypatch):
    import core.agent.builtin_tools as bt
    from core.agent.builtin_tools.web_search import WebSearchTool

    monkeypatch.setattr(bt, "_guest_web_search_enabled", lambda: True)
    monkeypatch.setattr(bt, "_web_search_enabled", lambda: True)
    monkeypatch.setattr(WebSearchTool, "_api_key_present", staticmethod(lambda: True))
    client = SimpleNamespace(search=AsyncMock(return_value=_payload(3)))
    monkeypatch.setattr("ai.web_search.web_search_engine", SimpleNamespace(exa_client=client))
    return client


def _search_use(i: int = 1):
    from core.agent.session import ToolUseBlock

    return ToolUseBlock(id=f"s{i}", name="web_search", input={"query": "who won the match"})


def _tool_results(call):
    from core.agent.session import ToolResultBlock

    return [b for m in call["messages"] for b in m.content if isinstance(b, ToolResultBlock)]


@pytest.mark.asyncio
@pytest.mark.parametrize("door", ["discord", "telegram"])
async def test_over_the_cap_the_friend_is_still_answered(monkeypatch, tmp_path, door,
                                                         guest_search_on, fresh_shared):
    from tests.bots.room_harness import GUEST_IDS, RoomHarness

    room = RoomHarness(monkeypatch, tmp_path, door=door)
    surface = "discord_text" if door == "discord" else "telegram_text"
    sam = _ctx(GUEST_IDS["sam"], room=room.room, surface=surface)
    for _ in range(5):
        assert fresh_shared.take_for(sam, per_hour=5, per_room_day=60) is None
    room.model_calls(_search_use())
    await room.say("sam", "palli, who won the match last night?", first_pass="answer",
                   model_chooses="answer", answer_text="I couldn't check just now.")
    results = _tool_results(room.calls[-1])
    assert results and results[-1].content.startswith(
        "web search isn't available for this person right now")
    assert results[-1].is_error is False
    assert guest_search_on.search.await_count == 0
    assert room.sent == ["I couldn't check just now."]


@pytest.mark.asyncio
async def test_two_doors_spend_from_one_ledger(monkeypatch, tmp_path, guest_search_on,
                                                fresh_shared):
    from tests.bots.room_harness import GUEST_IDS, RoomHarness

    for door in ("discord", "telegram"):
        room = RoomHarness(monkeypatch, tmp_path / door, door=door)
        room.model_calls(_search_use())
        await room.say("sam", "palli, who won the match last night?", first_pass="answer",
                       model_chooses="answer", answer_text="The home side.")
        assert room.sent == ["The home side."]
    sam = GUEST_IDS["sam"]
    assert set(fresh_shared._friends) == {("discord", str(sam)), ("telegram", str(sam))}
    assert guest_search_on.search.await_count == 2


# ── Fable's review (10-13): the ledger says what it counted and refused ─────

def test_the_ledger_says_what_it_counted_and_what_it_refused(budget, caplog):
    """One INFO line per counted search and per refusal — the account and the
    room by id only, as the doors' own logs name a guest — so a week's Exa
    bill for friends, which the spending check never sees, can be read off
    the log."""
    import logging

    caplog.set_level(logging.INFO, logger="core.agent.guest_search_budget")
    _take(budget)
    assert ("guest search: counted for discord:4242 — 1/5 this hour; "
            "1/60 today in discord:8800002") in caplog.text
    for _ in range(4):
        _take(budget)
    _take(budget)
    assert ("guest search: refused for discord:4242 — 5/5 this hour; "
            "the next frees up in 60 minutes") in caplog.text
    assert "Sam" not in caplog.text


def test_the_ledger_says_so_on_the_rooms_day_and_on_a_cap_of_zero(budget, caplog):
    import logging

    caplog.set_level(logging.INFO, logger="core.agent.guest_search_budget")
    _take(budget, per_hour=0)
    assert "guest search: refused for discord:4242 — the cap is 0" in caplog.text
    for i in range(60):
        _take(budget, _ctx(10_000 + i))
    _take(budget, _ctx(99_999))
    assert ("guest search: refused for discord:99999 — the room discord:8800002 is at "
            "60/60 today; the next frees up in 24 hours") in caplog.text
