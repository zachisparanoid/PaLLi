from __future__ import annotations

import threading

import ai.relationship_momentum as rm


def test_update_from_turn_persists_state(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(rm, "_STATE_DIR", tmp_path)

    state = rm.update_from_turn(1, emotional_context="casual_greeting")

    assert state.turn_count == 1
    assert rm.load_state(1).turn_count == 1


def test_concurrent_updates_do_not_lose_turns(tmp_path, monkeypatch) -> None:
    """Regression: unlocked load-modify-write let concurrent turns for the
    same owner interleave and silently drop relationship updates."""
    monkeypatch.setattr(rm, "_STATE_DIR", tmp_path)
    owner_id = 42
    n_threads = 8
    turns_each = 25
    barrier = threading.Barrier(n_threads)

    def worker() -> None:
        barrier.wait()
        for _ in range(turns_each):
            rm.update_from_turn(owner_id, emotional_context="neutral")

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    final = rm.load_state(owner_id)
    assert final.turn_count == n_threads * turns_each


def test_save_leaves_no_temp_files_behind(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(rm, "_STATE_DIR", tmp_path)

    rm.save_state(7, rm.RelationshipState(turn_count=3))

    assert rm.load_state(7).turn_count == 3
    assert [p.name for p in tmp_path.iterdir()] == ["relationship_7.json"]
