"""The names a message calls, and who's who in a room (the room-reading fix;
the owner's design and words, 2026-10-12).

10-11: a friend's typed name ("hey <friend>, you around?") — someone in the room — and she
answered, because only @mentions and replies told her who a message was for.
Stand-in names only: Robin is the owner; Sam, Alex and Jo are in the room; Jordan is
nobody here.
"""
from __future__ import annotations

import json
from types import SimpleNamespace as NS

import pytest

from core import room_people as rp

KEY = "discord:8800002"
HERE = ["Alex", "Sam", "Jo"]
MINE = ["palli", "polly"]


@pytest.fixture(autouse=True)
def _store(tmp_path, monkeypatch):
    monkeypatch.setenv("PALLI_ROOM_PEOPLE_PATH", str(tmp_path / "room_people.json"))


def _line(text, *, here=HERE, mine=MINE, voice="me"):
    return rp.render(rp.names_in(text, mine=mine, here=here), voice=voice)


# ── the line ─────────────────────────────────────────────────────────────────

def test_a_friends_typed_name_names_someone_in_the_room():
    assert _line("hey Alex, you around?") == (
        'Names in this message: "Alex" (someone in this room, not me)')


def test_the_approved_words_her_reply_call_sees():
    line = _line("palli, did Alex tell Jordan? hey Jordan, you there?")
    assert line == ('Names in this message: "palli" (me) · "Alex" (someone in this room, '
                    'not me) · "Jordan" (someone else; not anyone I know here)')


def test_the_quick_reads_see_it_in_the_third_person():
    line = _line("palli, did Alex tell Jordan? hey Jordan, you there?", voice="her")
    assert line == ('Names in this message: "palli" (her) · "Alex" (someone in this room, '
                    'not her) · "Jordan" (someone else; not anyone she knows here)')


def test_no_name_says_none():
    assert _line("anyone know a good pizza place?") == "Names in this message: none"


@pytest.mark.parametrize("text", [
    "hey Alex, you around?", "hey alex, you around?", "you around, Alex?",
    "Alex, you around?", "Alex said the slides are done", "did @Alex see it",
])
def test_a_name_for_someone_here_is_found_however_it_is_called(text):
    assert '(someone in this room, not me)' in _line(text)


def test_a_known_name_mid_sentence_in_lower_case_is_left_to_the_model():
    # "will", "may", "sky" are words too: lower case away from a call is not a name.
    assert _line("I'll tell alex later") == "Names in this message: none"


def test_her_own_names_count_anywhere_in_either_case():
    assert _line("ok POLLY what now") == 'Names in this message: "POLLY" (me)'
    assert _line("is palli around") == 'Names in this message: "palli" (me)'


def test_her_handle_is_not_her_name_in_a_word():
    assert _line("ping @palli_test_bot") == "Names in this message: none"


@pytest.mark.parametrize("text", [
    "Hey guys!", "Thanks Everyone!", "hey you around?", "Yesterday, you said it",
    "Is it ready, Sir?", "yo wtf!", "hey there", "Honestly, I don't know",
])
def test_words_that_name_nobody_are_not_names(text):
    assert _line(text) == "Names in this message: none"


@pytest.mark.parametrize("text", ["hey Jordan, you there?", "thanks Jordan", "you around, Jordan?"])
def test_an_unknown_name_is_reported_only_where_a_message_calls_it(text):
    assert _line(text) == 'Names in this message: "Jordan" (someone else; not anyone I know here)'


def test_an_unknown_capitalized_word_elsewhere_is_not_a_name():
    assert _line("Jordan said hi") == "Names in this message: none"


def test_a_guest_tag_and_links_are_not_read_as_names():
    assert _line("[Sam]: look at https://Alex.example.com") == "Names in this message: none"


def test_each_name_once_in_order():
    assert _line("Sam? Sam! hey Alex") == (
        'Names in this message: "Sam" (someone in this room, not me) · '
        '"Alex" (someone in this room, not me)')


def test_a_longer_name_wins_over_a_part_of_it():
    assert _line("hey Alex Smith", here=["Alex", "Alex Smith"]) == (
        'Names in this message: "Alex Smith" (someone in this room, not me)')


def test_one_of_her_names_someone_here_also_goes_by():
    assert _line("hey Polly", here=["Polly"]) == (
        'Names in this message: "Polly" (me, or someone in this room by the same name)')


def test_names_are_quoted_safely():
    line = rp.render([('Lo"gan', "here")], voice="me")
    assert line == "Names in this message: \"Lo'gan\" (someone in this room, not me)"


# ── the owner's corrections ─────────────────────────────────────────────────

def test_a_nickname_the_owner_taught_is_hers_in_that_room_only():
    rp.teach(KEY, "Pol", means_me=True)
    assert rp.names_line("hey Pol, you there?", key=KEY) == 'Names in this message: "Pol" (me)'
    assert rp.names_line("hey Pol, you there?", key="discord:1") == (
        'Names in this message: "Pol" (someone else; not anyone I know here)')


def test_a_person_the_owner_taught_is_someone_in_the_room():
    rp.teach(KEY, "Alex", means_me=False)
    assert rp.names_line("alex, you around?", key=KEY) == (
        'Names in this message: "alex" (someone in this room, not me)')


def test_teaching_moves_a_name_between_the_lists_and_forget_drops_it():
    rp.teach(KEY, "Pol", means_me=True)
    rp.teach(KEY, "pol", means_me=False)
    assert rp.taught(KEY) == {"for_me": [], "people": ["pol"]}
    assert rp.forget(KEY, "POL") is True
    assert rp.taught(KEY) == {"for_me": [], "people": []}
    assert rp.forget(KEY, "Pol") is False


def test_her_own_names_cannot_be_someone_else():
    with pytest.raises(ValueError):
        rp.teach(KEY, "Polly", means_me=False)
    with pytest.raises(ValueError):
        rp.teach(KEY, "  ?? ", means_me=True)


def test_an_unreadable_file_is_no_corrections_and_never_overwritten(tmp_path):
    path = tmp_path / "room_people.json"
    path.write_text("{not json", encoding="utf-8")
    assert rp.taught(KEY) == {"for_me": [], "people": []}
    with pytest.raises(rp.RoomPeopleStoreError):
        rp.teach(KEY, "Pol", means_me=True)
    assert path.read_text(encoding="utf-8") == "{not json"


def test_the_file_is_kept_by_room(tmp_path):
    rp.teach(KEY, "Pol", means_me=True)
    data = json.loads((tmp_path / "room_people.json").read_text(encoding="utf-8"))
    assert data == {KEY: {"for_me": ["Pol"], "people": []}}


# ── who is in the room ──────────────────────────────────────────────────────

def test_people_seen_talking_come_without_the_owners_mark():
    from core.agent.session import AgentMessage, TextBlock

    rows = [AgentMessage(role="user", content=[TextBlock(text="hi")],
                         speaker={"kind": "owner", "platform": "discord", "name": "Robin"}),
            AgentMessage(role="user", content=[TextBlock(text="[Sam]: yo")],
                         speaker={"kind": "guest", "platform": "discord", "name": "Sam"})]
    assert rp.seen_talking(rows) == ["Robin", "Sam"]


def test_people_seen_talking_are_labelled_with_the_whole_room_in_view(monkeypatch):
    """Only the last SEEN_ROWS rows are labelled, but with the rows before in
    view (``label_names(last=)``, Fable's final review, 7): a row without a
    record, after the room's records began, reads as "someone", never as the
    owner, however far back the first record was. (Through this list the two
    read alike today — the owner's mark is taken off and an empty name left
    out — so the test watches the labels it is given.)"""
    from core.agent import speakers
    from core.agent.session import AgentMessage, TextBlock

    seen = []
    real = speakers.label_names
    monkeypatch.setattr(speakers, "label_names",
                        lambda rows, **kw: seen.append(real(rows, **kw)) or seen[-1])
    sam = {"kind": "guest", "platform": "discord", "name": "Sam"}
    rows = ([AgentMessage(role="user", content=[TextBlock(text="[Sam]: first")], speaker=sam)]
            + [AgentMessage(role="user", content=[TextBlock(text=f"unrecorded {i}")])
               for i in range(rp.SEEN_ROWS)])
    assert rp.seen_talking(rows) == []
    assert seen and len(seen[-1]) == rp.SEEN_ROWS and set(seen[-1]) == {None}


def test_discord_members_who_can_see_the_channel_count_and_bots_do_not(monkeypatch):
    from core.agent import hands_runtime as hr

    members = [{"display_name": "Alex", "name": "alex_22", "bot": False},
               {"display_name": "PaLLi", "name": "palli_bot", "bot": True}]
    monkeypatch.setattr(hr, "members_here", lambda query="", *, channel_id=None: (members, True))
    assert rp.channel_members(KEY) == ["Alex"]                 # display names only
    assert rp.channel_members(KEY, handles=True) == ["Alex", "alex_22"]
    assert rp.channel_members("telegram:-100") == []
    rp.reset_for_tests()
    monkeypatch.setattr(hr, "members_here", lambda query="", *, channel_id=None: (members, False))
    assert rp.channel_members(KEY) == []          # a server-wide list isn't "this room"


def test_the_member_list_is_kept_for_a_minute(monkeypatch):
    from core.agent import hands_runtime as hr

    calls = []

    def members_here(query="", *, channel_id=None):
        calls.append(channel_id)
        return [{"display_name": "Alex", "name": "a", "bot": False}], True

    monkeypatch.setattr(hr, "members_here", members_here)
    rp.channel_members(KEY)
    rp.channel_members(KEY)
    assert len(calls) == 1


def test_a_line_that_cannot_be_built_is_empty(monkeypatch):
    monkeypatch.setattr(rp, "people_here", lambda *a, **k: 1 / 0)
    assert rp.names_line("hey Alex", key=KEY) == ""


# ── Fable's review (2026-10-12) ─────────────────────────────────────────────

COMMON = ["Will", "Max", "Chat", "Okay", "Grace"]


@pytest.mark.parametrize("text", [
    "Will you be around later palli?", "will you help palli", "Max said hi palli",
    "Okay, so palli, here's the thing", "palli, chat with me", "say it with grace, palli",
])
def test_a_member_named_like_a_word_is_not_read_into_a_message_for_her(text):
    found = rp.names_in(text, mine=MINE, here=COMMON)
    assert [w for w, _ in found] == [w for w in ("palli",) if w in text.lower()]


@pytest.mark.parametrize("text, name", [
    ("hey Will, you around?", "Will"), ("you around, Will?", "Will"), ("hey Max", "Max"),
    ("@Will did you see it", "Will"),
])
def test_a_member_named_like_a_word_still_counts_where_a_message_calls_them(text, name):
    assert rp.names_in(text, mine=MINE, here=COMMON) == [(name, "here")]


def test_after_a_greeting_a_word_name_counts_only_when_the_call_ends_there():
    assert rp.names_in("hey will you help me", mine=MINE, here=COMMON) == []


def test_her_own_account_names_are_hers(monkeypatch):
    from core.agent import hands_runtime as hr

    me = NS(display_name="Pally", nick="Pally")
    channel = NS(guild=NS(me=me))
    client = NS(user=NS(name="palli_bot", display_name="PaLLiBot", global_name=None),
                get_channel=lambda cid: channel)
    monkeypatch.setattr(hr, "get_client", lambda: client)
    monkeypatch.setattr(hr, "members_here", lambda query="", *, channel_id=None: ([], False))
    assert rp.names_line("hey PaLLiBot, you around?", key=KEY) == (
        'Names in this message: "PaLLiBot" (me)')
    assert rp.names_line("Pally, you there?", key=KEY) == 'Names in this message: "Pally" (me)'


def test_her_telegram_bot_names_are_hers(monkeypatch):
    from core.agent import hands_runtime as hr

    monkeypatch.setattr(hr, "get_telegram_bot", lambda: NS(username="palli_test_bot",
                                                           first_name="Pally"))
    assert rp.names_line("@palli_test_bot hi", key="telegram:-100") == (
        'Names in this message: "palli_test_bot" (me)')


def test_a_word_holding_her_name_is_never_someone_else():
    assert _line("hey PaLLiBot, you around?") == "Names in this message: none"
    assert _line("hey PaLLi_bot") == "Names in this message: none"


def test_a_bot_not_yet_started_does_not_break_her_names(monkeypatch):
    from core.agent import hands_runtime as hr

    class NotStarted:
        @property
        def username(self):
            raise RuntimeError("not initialized")

    monkeypatch.setattr(hr, "get_telegram_bot", lambda: NotStarted())
    assert rp.her_names("telegram:-100") == ["palli", "polly"]


def test_a_name_on_a_later_line_of_a_burst_is_found():
    assert _line("[Sam]: yo\n[Sam]: hey Alex, you around?") == (
        'Names in this message: "Alex" (someone in this room, not me)')


def test_someone_here_who_goes_by_one_of_her_names_reads_both(monkeypatch):
    from core.agent import hands_runtime as hr

    monkeypatch.setattr(hr, "members_here", lambda query="", *, channel_id=None: (
        [{"display_name": "Polly", "name": "polly_h", "bot": False}], True))
    assert rp.names_line("hey Polly", key=KEY) == (
        'Names in this message: "Polly" (me, or someone in this room by the same name)')


def test_names_in_other_scripts_and_a_trailing_emoji():
    assert _line("hey Иван, ты тут?") == (
        'Names in this message: "Иван" (someone else; not anyone I know here)')
    assert _line("you around, Jordan? \U0001F600") == (
        'Names in this message: "Jordan" (someone else; not anyone I know here)')


def test_a_greeting_on_a_line_of_its_own_calls_nobody_on_the_next():
    assert _line("hey\nJordan said he'd come, can you add it") == "Names in this message: none"
    assert _line("hey\nAlex said he'd come") == (
        'Names in this message: "Alex" (someone in this room, not me)')   # still a name here


def test_a_display_name_with_an_emoji_is_the_name(monkeypatch):
    from core.agent import hands_runtime as hr

    monkeypatch.setattr(hr, "members_here", lambda query="", *, channel_id=None: (
        [{"display_name": "Kit \U0001F319", "name": "kit_x", "bot": False}], True))
    assert rp.channel_members(KEY) == ["Kit"]
    assert rp.names_line("hey Kit, you around?", key=KEY) == (
        'Names in this message: "Kit" (someone in this room, not me)')


def test_a_server_sized_member_list_costs_little():
    import time

    here = [f"Member{i}" for i in range(3000)] + ["Alex"]
    start = time.perf_counter()
    found = rp.names_in("hey Alex, you around? palli too", mine=MINE, here=here)
    assert time.perf_counter() - start < 0.25
    assert found == [("Alex", "here"), ("palli", "me")]
