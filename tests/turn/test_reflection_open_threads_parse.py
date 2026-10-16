"""_extract_plan parses the new open_threads_to_add/close fields from LLM JSON."""
from core.turn.reflect import _parse_open_threads_to_add, _parse_open_threads_to_close


def test_parse_open_threads_to_add_happy():
    payload = {
        "open_threads_to_add": [
            {
                "topic_summary": "user wants to send a vendor email",
                "why_it_matters": "committed to send this week",
                "kind": "work",
                "urgency": "time-sensitive",
            }
        ]
    }
    out = _parse_open_threads_to_add(payload)
    assert len(out) == 1
    assert out[0]["topic_summary"] == "user wants to send a vendor email"


def test_parse_open_threads_to_add_drops_invalid_kind():
    payload = {
        "open_threads_to_add": [
            {
                "topic_summary": "x",
                "why_it_matters": "y",
                "kind": "not_a_valid_kind",
                "urgency": "z",
            }
        ]
    }
    out = _parse_open_threads_to_add(payload)
    assert out == []  # silently dropped


def test_parse_open_threads_to_add_drops_missing_required():
    payload = {
        "open_threads_to_add": [
            {"topic_summary": "x", "kind": "work"},  # missing why_it_matters, urgency
        ]
    }
    out = _parse_open_threads_to_add(payload)
    assert out == []


def test_parse_open_threads_to_close_happy():
    payload = {
        "open_threads_to_close": [
            {"thread_id": "abc123", "resolution_reason": "user said sent"},
        ]
    }
    out = _parse_open_threads_to_close(payload)
    assert len(out) == 1
    assert out[0]["thread_id"] == "abc123"


def test_parse_open_threads_to_close_drops_missing_thread_id():
    payload = {
        "open_threads_to_close": [
            {"resolution_reason": "x"},  # missing thread_id
        ]
    }
    out = _parse_open_threads_to_close(payload)
    assert out == []


def test_parse_open_threads_to_add_handles_missing_field():
    out = _parse_open_threads_to_add({})
    assert out == []


def test_parse_open_threads_to_close_handles_missing_field():
    out = _parse_open_threads_to_close({})
    assert out == []
