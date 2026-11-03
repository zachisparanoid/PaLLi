"""What every door she has needs, written once.

A "door" is a surface a person types at her through: Discord, Telegram, the
dashboard. The four pieces that live here were written inside bots/discord
because Discord was the only one — but none of them is about Discord. The
steering fork is about what happens to a message that arrives while she is
already working. The burst coalescer is about someone typing three thoughts
in a row. The guest gate is about how much a stranger may spend of his
account. The voice-note transcriber is about audio.

Re-implemented per door, each would drift within a week — and the guest
budget re-implemented would be TWO budgets, which is not a budget at all,
because the money is one account.

The old bots/discord paths still exist and still work: each is a shim that
makes the old name BE the moved module (``sys.modules[__name__] = _m``), so
a monkeypatch through either path is seen through both.
"""
