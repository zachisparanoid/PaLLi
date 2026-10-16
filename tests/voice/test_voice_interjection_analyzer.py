from __future__ import annotations

import pytest

from voice.voice_interjection_analyzer import (
    VoiceInterjectionAnalyzer,
    apply_interaction_strategy_to_interjection,
)


@pytest.mark.asyncio
async def test_check_direct_address_detects_polly_with_punctuation():
    analyzer = VoiceInterjectionAnalyzer()

    detected = await analyzer.check_direct_address("Yo. Polly, what's up?")

    assert detected is True


@pytest.mark.asyncio
async def test_check_direct_address_ignores_non_addressed_holly_reference():
    analyzer = VoiceInterjectionAnalyzer()

    detected = await analyzer.check_direct_address("I talked to Holly yesterday about dinner.")

    assert detected is False


@pytest.mark.asyncio
async def test_check_direct_address_detects_you_still_there_phrase():
    analyzer = VoiceInterjectionAnalyzer()

    detected = await analyzer.check_direct_address("You still there?")

    assert detected is True


@pytest.mark.asyncio
async def test_check_direct_address_detects_respond_to_this_message_phrase():
    analyzer = VoiceInterjectionAnalyzer()

    detected = await analyzer.check_direct_address("Respond to this message.")

    assert detected is True


def test_interjection_strategy_blocks_stay_quiet_decision() -> None:
    decision = apply_interaction_strategy_to_interjection(
        {"should_interject": True, "confidence": 0.91, "reasoning": "useful"},
        {"action": "stay_quiet", "reason": "ambient observe"},
    )

    assert decision["should_interject"] is False
    assert decision["interaction_strategy"]["action"] == "stay_quiet"
    assert "Strategy blocked interjection" in decision["reasoning"]


def test_interjection_strategy_allows_soft_probe_decision() -> None:
    decision = apply_interaction_strategy_to_interjection(
        {"should_interject": True, "confidence": 0.91, "reasoning": "useful"},
        {"action": "soft_probe", "reason": "natural opening"},
    )

    assert decision["should_interject"] is True
    assert decision["interaction_strategy"]["action"] == "soft_probe"
