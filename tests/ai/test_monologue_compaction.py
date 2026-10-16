from __future__ import annotations

import asyncio
import json
import shutil
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

import ai.monologue_compaction as monologue_compaction
from ai import inner_monologue_store as store
from ai.inner_monologue import InnerMonologueEngine
from core.settings_service import SettingsService


def _make_entry(day: date, index: int, *, tag: str = "content", candidate: str | None = None) -> store.MonologueEntry:
    ts = datetime(day.year, day.month, day.day, 12, 0, tzinfo=timezone.utc).timestamp() + index
    return store.MonologueEntry(
        timestamp=ts,
        thought=f"thought {day.isoformat()} #{index}",
        situation_snapshot={"source": "test", "day": day.isoformat()},
        tick_metadata={"substantive": index % 3 != 2},
        sentiment={"tag": tag, "confidence": 0.8},
        proactive_candidate=candidate,
    )


def _write_week_entries(
    week_start: date,
    *,
    days: int = 7,
    tags: list[str] | None = None,
    candidates: list[str | None] | None = None,
) -> list[Path]:
    paths: list[Path] = []
    counter = 0
    for offset in range(days):
        day = week_start + timedelta(days=offset)
        tag = tags[counter % len(tags)] if tags else "content"
        candidate = candidates[counter % len(candidates)] if candidates else None
        paths.append(store.write_entry(_make_entry(day, counter, tag=tag, candidate=candidate)))
        counter += 1
    return paths


def _patch_llm(monkeypatch: pytest.MonkeyPatch, responses: list[str] | None = None, calls: list[Any] | None = None) -> None:
    queue = list(responses or ["This week, I kept track of the emotional shape without flattening it."])

    async def fake_chat_completion(messages: list[dict[str, str]], **kwargs: Any) -> str:
        if calls is not None:
            calls.append({"messages": messages, "kwargs": kwargs})
        return queue.pop(0) if queue else "A later digest summary."

    monkeypatch.setattr(monologue_compaction.llm, "chat_completion", fake_chat_completion)


def _digest_payload(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8").splitlines()[0])


def _manifest_payload(path: Path) -> dict[str, Any]:
    return json.loads(path.with_suffix(".manifest.json").read_text(encoding="utf-8"))


def _weekly_path(week_start: date) -> Path:
    return store.MONOLOGUE_DIR / "digests" / "weekly" / f"{monologue_compaction._week_key(week_start)}.jsonl"


def _monthly_path(month_start: date) -> Path:
    return store.MONOLOGUE_DIR / "digests" / "monthly" / f"{month_start:%Y-%m}.jsonl"


def _write_weekly_digest(week_start: date, summary: str) -> Path:
    entry = monologue_compaction.MonologueDigestEntry(
        digest_id=f"weekly_{monologue_compaction._week_key(week_start)}",
        tier="weekly",
        period_start=week_start.isoformat(),
        period_end=(week_start + timedelta(days=6)).isoformat(),
        created_at=time.time(),
        summary_text=summary,
        source_entry_count=7,
        substantive_entry_count=5,
        dominant_sentiment_tags=["content"],
        surfaced_candidates=["candidate from weekly"],
        summary_model="ollama::qwen3:8b",
        metadata={"sentiment_counts": {"content": 7}},
    )
    path = _weekly_path(week_start)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def isolated_monologue_dir(monkeypatch: pytest.MonkeyPatch):
    directory = Path("data") / f"phase7b_test_{uuid.uuid4().hex}"
    directory.mkdir(parents=True)
    monkeypatch.setattr(store, "MONOLOGUE_DIR", directory)
    try:
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture
def compaction_config(monkeypatch: pytest.MonkeyPatch):
    def apply(**overrides: Any) -> tuple[Any, Any]:
        monologue = SimpleNamespace(
            enabled=True,
            digest_delay_days=0,
            weekly_summary_max_words=250,
            monthly_summary_max_words=300,
            summary_model="",
        )
        for key, value in overrides.items():
            setattr(monologue, key, value)
        parent = SimpleNamespace(summary_model="qwen3:8b", monologue=monologue)
        monkeypatch.setattr(monologue_compaction, "_config", lambda: (parent, monologue))
        return parent, monologue

    return apply


@pytest.mark.asyncio
async def test_weekly_digest_not_past_delay_returns_none(isolated_monologue_dir: Path, compaction_config, monkeypatch):
    compaction_config(digest_delay_days=9999)
    week_start = date(2026, 1, 5)
    _write_week_entries(week_start)
    monkeypatch.setattr(monologue_compaction.llm, "chat_completion", pytest.fail)

    result = await monologue_compaction.maybe_build_weekly_digest(week_start=week_start)

    assert result is None
    assert not _weekly_path(week_start).exists()


@pytest.mark.asyncio
async def test_weekly_digest_builds_expected_file_manifest_and_required_fields(
    isolated_monologue_dir: Path,
    compaction_config,
    monkeypatch,
):
    compaction_config()
    week_start = date(2026, 1, 5)
    paths = _write_week_entries(
        week_start,
        tags=["stressed", "content", "playful"],
        candidates=["follow up about sleep", None, None],
    )
    before = {path: path.read_bytes() for path in paths}
    _patch_llm(monkeypatch, ["I noticed a real weekly arc and kept the useful emotional beats intact."])

    result = await monologue_compaction.maybe_build_weekly_digest(week_start=week_start)

    path = _weekly_path(week_start)
    assert result is not None
    assert path.exists()
    payload = _digest_payload(path)
    required = {
        "digest_id",
        "tier",
        "period_start",
        "period_end",
        "created_at",
        "summary_text",
        "source_entry_count",
        "substantive_entry_count",
        "dominant_sentiment_tags",
        "surfaced_candidates",
        "summary_model",
        "metadata",
    }
    assert required <= set(payload)
    assert payload["tier"] == "weekly"
    assert payload["source_entry_count"] == 7
    assert "follow up about sleep" in payload["surfaced_candidates"]
    manifest = _manifest_payload(path)
    assert len(manifest["source_files"]) == 7
    assert [item["line_count"] for item in manifest["source_files"]] == [1] * 7
    assert {path: path.read_bytes() for path in paths} == before


@pytest.mark.asyncio
async def test_weekly_digest_is_idempotent(isolated_monologue_dir: Path, compaction_config, monkeypatch):
    compaction_config()
    week_start = date(2026, 1, 5)
    _write_week_entries(week_start)
    calls: list[Any] = []
    _patch_llm(monkeypatch, calls=calls)

    first = await monologue_compaction.maybe_build_weekly_digest(week_start=week_start)
    second = await monologue_compaction.maybe_build_weekly_digest(week_start=week_start)

    assert first is not None
    assert second is None
    assert len(calls) == 1
    assert len(list((_weekly_path(week_start).parent).glob("*.jsonl"))) == 1


@pytest.mark.asyncio
async def test_weekly_digest_force_overwrites_existing_digest(isolated_monologue_dir: Path, compaction_config, monkeypatch):
    compaction_config()
    week_start = date(2026, 1, 5)
    _write_week_entries(week_start)
    _patch_llm(monkeypatch, ["first summary", "second summary"])

    await monologue_compaction.maybe_build_weekly_digest(week_start=week_start)
    result = await monologue_compaction.maybe_build_weekly_digest(week_start=week_start, force=True)

    assert result is not None
    assert _digest_payload(_weekly_path(week_start))["summary_text"] == "second summary"


@pytest.mark.asyncio
async def test_monthly_digest_reads_four_weekly_digests_not_raw_daily_files(
    isolated_monologue_dir: Path,
    compaction_config,
    monkeypatch,
):
    compaction_config()
    month_start = date(2026, 1, 1)
    for idx, week in enumerate(monologue_compaction._weekly_starts_for_month(month_start), start=1):
        _write_weekly_digest(week, f"weekly summary {idx}")
    calls: list[Any] = []
    _patch_llm(monkeypatch, ["monthly summary"], calls=calls)

    result = await monologue_compaction.maybe_build_monthly_digest(month_start=month_start, force=True)

    assert result is not None
    prompt = calls[0]["messages"][0]["content"]
    assert "weekly summary 1" in prompt
    assert "weekly summary 4" in prompt
    assert "thought 2026" not in prompt


@pytest.mark.asyncio
async def test_monthly_manifest_references_four_weekly_digest_sources(
    isolated_monologue_dir: Path,
    compaction_config,
    monkeypatch,
):
    compaction_config()
    month_start = date(2026, 1, 1)
    for week in monologue_compaction._weekly_starts_for_month(month_start):
        _write_weekly_digest(week, f"summary for {week.isoformat()}")
    _patch_llm(monkeypatch, ["monthly summary"])

    await monologue_compaction.maybe_build_monthly_digest(month_start=month_start, force=True)

    manifest = _manifest_payload(_monthly_path(month_start))
    assert len(manifest["source_files"]) == 4
    assert all("/digests/weekly/" in item["path"] for item in manifest["source_files"])


@pytest.mark.asyncio
async def test_scheduled_compaction_builds_windows_once_per_day(isolated_monologue_dir: Path, compaction_config, monkeypatch):
    compaction_config()
    _write_week_entries(date(2026, 1, 5))
    _write_week_entries(date(2026, 1, 12))
    _patch_llm(monkeypatch, ["week one", "week two"])

    first = await monologue_compaction.run_scheduled_compaction(now=time.time())
    second = await monologue_compaction.run_scheduled_compaction(now=time.time())

    assert len(first["weekly_built"]) == 2
    assert first["monthly_built"] == []
    assert second["weekly_built"] == []
    assert second["skipped"] == ["already_ran_today"]


@pytest.mark.asyncio
async def test_llm_failure_leaves_no_digest_manifest_or_raw_mutation(isolated_monologue_dir: Path, compaction_config, monkeypatch):
    compaction_config()
    week_start = date(2026, 1, 5)
    paths = _write_week_entries(week_start)
    before = {path: path.read_bytes() for path in paths}

    async def failing_chat_completion(*_args: Any, **_kwargs: Any) -> str:
        raise TimeoutError("model unavailable")

    monkeypatch.setattr(monologue_compaction.llm, "chat_completion", failing_chat_completion)

    result = await monologue_compaction.maybe_build_weekly_digest(week_start=week_start)

    digest_path = _weekly_path(week_start)
    assert result is None
    assert not digest_path.exists()
    assert not digest_path.with_suffix(".manifest.json").exists()
    assert {path: path.read_bytes() for path in paths} == before


def test_raw_recent_readers_ignore_digest_artifacts(isolated_monologue_dir: Path):
    entry = store.MonologueEntry(
        timestamp=time.time(),
        thought="recent raw thought",
        situation_snapshot={},
        tick_metadata={"substantive": True},
        sentiment={"tag": "content", "confidence": 0.9},
        proactive_candidate="ask about the repo",
    )
    store.write_entry(entry)
    before_context = [item.thought for item in store.read_recent_for_context(max_entries=5, max_age_hours=24)]
    before_candidates = [item.proactive_candidate for item in store.read_recent_with_candidates(max_age_hours=24)]
    _write_weekly_digest(date(2026, 1, 5), "digest should not be returned by raw readers")

    assert [item.thought for item in store.read_recent_for_context(max_entries=5, max_age_hours=24)] == before_context
    assert [item.proactive_candidate for item in store.read_recent_with_candidates(max_age_hours=24)] == before_candidates


def test_read_recent_digest_context_returns_empty_then_newest_first(isolated_monologue_dir: Path):
    assert store.read_recent_digest_context(max_age_days=90) == []
    older = _write_weekly_digest(date(2026, 1, 5), "older digest")
    newer = _write_weekly_digest(date(2026, 1, 12), "newer digest")

    digests = store.read_recent_digest_context(max_age_days=90)

    assert [item.summary_text for item in digests] == [
        _digest_payload(newer)["summary_text"],
        _digest_payload(older)["summary_text"],
    ]


@pytest.mark.asyncio
async def test_settings_hot_reload_disabled_scheduler(monkeypatch, isolated_monologue_dir: Path):
    raw = yaml.safe_load(Path("data/palli.yaml").read_text(encoding="utf-8"))
    raw["compaction"]["monologue"]["enabled"] = False
    yaml_path = isolated_monologue_dir / "palli.yaml"
    yaml_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    service = SettingsService(yaml_path=yaml_path)
    service.load()

    import core.settings_boot as settings_boot

    monkeypatch.setattr(settings_boot, "settings_service", service)
    result = await monologue_compaction.run_scheduled_compaction(now=time.time())

    assert result["skipped"] == ["disabled"]


@pytest.mark.asyncio
async def test_tick_scheduler_queues_compaction_without_blocking(monkeypatch):
    import core.settings_boot as settings_boot

    settings = SimpleNamespace(
        snapshot=lambda: SimpleNamespace(
            compaction=SimpleNamespace(monologue=SimpleNamespace(enabled=True))
        )
    )
    monkeypatch.setattr(settings_boot, "settings_service", settings)
    started = asyncio.Event()
    finished = asyncio.Event()

    async def slow_scheduled_compaction(**_kwargs: Any) -> dict[str, Any]:
        started.set()
        await asyncio.sleep(0.05)
        finished.set()
        return {"weekly_built": [], "monthly_built": [], "skipped": []}

    monkeypatch.setattr(monologue_compaction, "run_scheduled_compaction", slow_scheduled_compaction)
    engine = InnerMonologueEngine()

    begin = time.perf_counter()
    engine._queue_monologue_compaction_if_due()
    elapsed = time.perf_counter() - begin

    assert elapsed < 0.03
    await asyncio.wait_for(started.wait(), timeout=1.0)
    await asyncio.wait_for(finished.wait(), timeout=1.0)


def test_backfill_script_builds_historical_digests(isolated_monologue_dir: Path, compaction_config, monkeypatch, capsys):
    compaction_config()
    week_start = date(2026, 1, 5)
    _write_week_entries(week_start)
    _patch_llm(monkeypatch, ["historical digest"])

    from scripts import build_monologue_digests

    exit_code = build_monologue_digests.main(["--since", week_start.isoformat(), "--force"])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["weekly_built"] == [f"weekly_{monologue_compaction._week_key(week_start)}"]
    assert _weekly_path(week_start).exists()


@pytest.mark.asyncio
async def test_dominant_sentiment_tags_aggregate_synthetic_week(isolated_monologue_dir: Path, compaction_config, monkeypatch):
    compaction_config()
    week_start = date(2026, 1, 5)
    tags = ["stressed", "stressed", "stressed", "stressed", "content", "content", "playful"]
    _write_week_entries(week_start, tags=tags)
    _patch_llm(monkeypatch, ["sentiment digest"])

    result = await monologue_compaction.maybe_build_weekly_digest(week_start=week_start, force=True)

    assert result is not None
    assert result.dominant_sentiment_tags == ["stressed", "content", "playful"]
    assert result.metadata["sentiment_counts"] == {"stressed": 4, "content": 2, "playful": 1}
