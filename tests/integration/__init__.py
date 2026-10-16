"""Integration tests for PaLLi behavior.

Unlike unit tests, these run actual LLM calls and exercise the full
turn pipeline (ingest → recall → decide → respond → reflect).

Use a synthetic owner_id (BEHAVIOR_TEST_OWNER_ID) so test conversations
don't pollute the real owner's memory. The harness cleans up its own
rows on teardown.
"""
