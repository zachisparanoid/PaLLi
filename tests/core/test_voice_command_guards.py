"""The 2026-08-11 01:53 incident: /leave_voice from a DM crashed on
interaction.guild = None (AttributeError swallowed by discord.py, command
silently failed for him). Every voice command now answers a friendly
ephemeral line outside servers. Fakes only — no gateway.

Two deviations from the task brief's literal test code, both taken under
its own sanctioned latitude ("if register_voice_commands resists the fake
tree... choose whichever keeps the production diff smallest; document the
choice"):

1. ``asyncio.run(...)`` instead of
   ``asyncio.get_event_loop().run_until_complete(...)`` — this repo's
   conftest poisons the default event loop (plan-sanctioned adaptation,
   noted in the task cover note).

2. voice_mode/voice_presence/voice_focus each take a second REQUIRED
   parameter (mode/profile/focus, a discord.app_commands.Choice) beyond
   ``interaction``. The fake tree below captures the raw decorated
   function (register_voice_commands' fake tree.command stores it
   unwrapped, with no discord.py Command object and thus no ``.callback``
   indirection) — so calling it with only ``interaction`` raises a
   TypeError for missing arguments before the guard (or anything else in
   the function body) ever runs. A placeholder second positional argument
   below keeps all six commands callable the same way; the guard returns
   before any of these three functions reads the value, so the placeholder
   is never touched. This keeps the PRODUCTION diff to a pure in-place
   guard insertion at the top of all six command bodies — no
   module-level ``_cmd_<name>`` extraction needed anywhere, which is
   smaller than the brief's documented refactor fallback.
"""
import asyncio
from types import SimpleNamespace

from discord import app_commands

# Batch 8, task 2b (ruling T2b-7): every voice command asks who is calling
# FIRST. The DM guard below is about the owner's own commands, so the caller
# is stubbed to the owner (mechanical; the premise is unchanged). The
# non-owner path has its own test at the bottom.
OWNER_DISCORD_ID = 5550001


def _caller_is(monkeypatch, *, owner: bool):
    from bots.common.commands import Caller
    import bots.discord.voice_commands as vcmd

    async def resolve_caller(*, platform, platform_user_id):
        return Caller(is_owner=owner, user_id=1 if owner else int(platform_user_id),
                      platform_user_id=int(platform_user_id), platform=platform)

    monkeypatch.setattr(vcmd, "resolve_caller", resolve_caller)


def _tree():
    from bots.discord.voice_commands import register_voice_commands
    tree = app_commands.CommandTree.__new__(app_commands.CommandTree)
    commands = {}

    def command(*, name, description=""):
        def deco(fn):
            commands[name] = fn
            return fn
        return deco
    tree.command = command
    register_voice_commands(tree)
    return commands


def _dm_interaction(*, user_id=1, defers=None):
    sends = []

    async def send(msg=None, **kw):
        sends.append((msg, kw))

    async def defer(**kw):
        if defers is not None:
            defers.append(kw)
    return SimpleNamespace(
        guild=None,
        channel=SimpleNamespace(id=1),
        user=SimpleNamespace(id=user_id, name="sam"),
        response=SimpleNamespace(defer=defer),
        followup=SimpleNamespace(send=send),
    ), sends


# Placeholder positional args for the three commands whose signature takes
# more than `interaction`. Never read: the guard returns before any of
# these three functions touches mode/profile/focus. See deviation 2 above.
_PLACEHOLDER_EXTRA_ARGS = {
    "voice_mode": (SimpleNamespace(value="direct_address"),),
    "voice_presence": (SimpleNamespace(value="balanced"),),
    "voice_focus": (SimpleNamespace(value="responsive"),),
}


def test_every_command_survives_dm(monkeypatch):
    _caller_is(monkeypatch, owner=True)
    commands = _tree()
    assert len(commands) == 6
    for name, fn in commands.items():
        interaction, sends = _dm_interaction()
        # app_commands wraps callbacks; call the underlying function
        cb = getattr(fn, "callback", fn)
        extra = _PLACEHOLDER_EXTRA_ARGS.get(name, ())
        asyncio.run(cb(interaction, *extra))
        assert sends, f"{name} said nothing from a DM"
        msg = str(sends[0][0]).lower()
        assert "server" in msg, f"{name} lacks the friendly line: {msg!r}"


def test_a_non_owner_gets_one_private_line_from_every_voice_command(monkeypatch):
    """Batch 8, task 2b (B8-2, ruling T2b-7): the voice slash commands are
    the owner's. Anyone else is told so privately (an ephemeral defer and
    the owner-only line) before anything else runs — in a server or a DM."""
    from bots.common.commands import owner_only_line

    _caller_is(monkeypatch, owner=False)
    commands = _tree()
    for name, fn in commands.items():
        for guild in (None, SimpleNamespace(id=1, voice_client=None)):
            defers: list = []
            interaction, sends = _dm_interaction(user_id=4242, defers=defers)
            interaction.guild = guild
            cb = getattr(fn, "callback", fn)
            extra = _PLACEHOLDER_EXTRA_ARGS.get(name, ())
            asyncio.run(cb(interaction, *extra))
            assert defers == [{"ephemeral": True}], name
            assert sends == [(owner_only_line("Voice commands"), {"ephemeral": True})], name


def test_the_owners_defer_is_unchanged(monkeypatch):
    _caller_is(monkeypatch, owner=True)
    commands = _tree()
    for name, fn in commands.items():
        defers: list = []
        interaction, sends = _dm_interaction(user_id=OWNER_DISCORD_ID, defers=defers)
        cb = getattr(fn, "callback", fn)
        asyncio.run(cb(interaction, *_PLACEHOLDER_EXTRA_ARGS.get(name, ())))
        assert defers == [{}], name                # today's public defer, no kwargs
