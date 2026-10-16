from __future__ import annotations

from typing import Any

from core.agent.tool_outcome import ToolFailure
from core.agent.tools import ToolContext
from core.untrusted import wrap_web_text


class WebSearchTool:
    """Live web search via the Exa client (2026-07-01 rewire).

    ai/web_search.py existed since the April era but was never registered
    with the ReAct registry, so the live agent could not touch the web.
    This tool calls the Exa client directly and returns compact raw-ish
    results — the agent loop is the summarizer. (The legacy
    ``perform_search`` pipeline pins a summarizer model that may not be
    funded; skipping it keeps search provider-agnostic and one LLM call
    cheaper.)
    """

    name = "web_search"
    description = (
        "Search the live web for current information. Returns titles, URLs, "
        "publish dates, and content summaries for the top results. Use for "
        "anything that needs up-to-date or external facts."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to search for."},
            "num_results": {
                "type": "integer",
                "description": "How many results to return (1-8).",
                "default": 5,
            },
        },
        "required": ["query"],
    }
    requires_approval = False
    owner_only = False
    for_guest = False

    def __init__(self, *, for_guest: bool = False) -> None:
        # Friends get web search (R14, T7; the owner, 10-12 21:20 and 22:03,
        # Q5): the guest registry's instance spends from the per-friend and
        # per-room floor (core/agent/guest_search_budget.py) and returns 5
        # results at most. The owner's instance never reads that ledger and
        # keeps its reach (1-8), byte for byte.
        if for_guest:
            from core.agent import guest_search_budget as _gsb

            self.for_guest = True
            props = dict(self.input_schema["properties"])
            props["num_results"] = {
                "type": "integer",
                "description": f"How many results to return (1-{_gsb.RESULTS_PER_SEARCH}).",
                "default": _gsb.RESULTS_PER_SEARCH,
            }
            self.input_schema = {**self.input_schema, "properties": props}

    @staticmethod
    def _api_key_present() -> bool:
        try:
            from core.config import config
            return bool(str(getattr(config, "EXA_API_KEY", "") or "").strip())
        except Exception:
            return False

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str | ToolFailure:
        query = str(input.get("query") or "").strip()
        if not query:
            return "web_search needs a non-empty query."
        if not self._api_key_present():
            return (
                "web search is not configured on this machine "
                "(EXA_API_KEY is missing) — answer from what you know and "
                "say you couldn't check the live web."
            )
        try:
            num_results = max(1, min(8, int(input.get("num_results", 5))))
        except (TypeError, ValueError):
            num_results = 5
        if self.for_guest and not bool(getattr(ctx, "is_owner", False)):
            # The floor (plan §2b.7a): counted here, as the search is sent to
            # Exa; over a cap the plain result goes back instead, unwrapped
            # (its own words, like "not configured" above), and the run goes
            # on to a reply or a quiet choice.
            from core.agent import guest_search_budget as _gsb

            refusal = _gsb.take_for(ctx)
            if refusal is not None:
                return refusal
            num_results = min(num_results, _gsb.RESULTS_PER_SEARCH)

        import ai.web_search as _ws
        try:
            payload = await _ws.web_search_engine.exa_client.search(
                query=query,
                num_results=num_results,
                summary=True,
            )
        except Exception as exc:
            # The search call raised and this tool caught it (week 2, task 1):
            # she got no results, so the result says so to the dispatcher too.
            return ToolFailure(f"web search failed: {exc}")

        results = (payload or {}).get("results") or []
        if not results:
            return f"no web results for {query!r}."

        lines: list[str] = [f"web results for {query!r}:"]
        for i, r in enumerate(results[:num_results], 1):
            title = str(r.get("title") or "(untitled)")
            url = str(r.get("url") or "")
            published = str(r.get("publishedDate") or "")
            body = str(r.get("summary") or r.get("text") or "")
            body = " ".join(body.split())[:800]
            lines.append(f"[{i}] {title}")
            if url:
                lines.append(f"    url: {url}")
            if published:
                lines.append(f"    published: {published}")
            if body:
                lines.append(f"    {body}")
        # Batch 8, task 9 (ruling T9-5): the results are the sites' words, so
        # they come back marked as content from the web, not instructions.
        # Only here — the failure and "no results" lines above are this
        # tool's own words and stay as they were.
        return wrap_web_text(f"web search for {query!r}", "\n".join(lines))
