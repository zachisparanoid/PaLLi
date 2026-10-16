"""Her sense of the last mile (DEFECT_RECORD §15; moved here for the
telegram door, 2026-09-14, task 2).

§15 was the bounced package: a 15.4 MB file hit Discord's cap five times,
the file taped to it died with it, and no failure ever reached HER — she
went on believing it had landed. The fix was not a retry. It was making a
failed delivery something she can feel, so the next thing she says is true.

Every door has a last mile and every last mile fails, so this lives in
bots/common. Structure only, never content; fire-and-forget; never raises
into a send path.
"""
from __future__ import annotations


def delivery_notice(kind: str, **fields) -> None:
    try:
        from ai.present_moment import notice
        notice(kind, **fields)
    except Exception:  # noqa: BLE001
        pass
