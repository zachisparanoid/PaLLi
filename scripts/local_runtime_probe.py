from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any, List

import httpx


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send local probe messages to the live PaLLi backend.",
    )
    parser.add_argument(
        "messages",
        nargs="*",
        help="Message content to send. Repeat to run a burst.",
    )
    parser.add_argument(
        "--messages-file",
        type=Path,
        help="Optional text file with one probe message per line.",
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="Base URL for the running PaLLi backend.",
    )
    parser.add_argument(
        "--surface-id",
        default="probe:local-runtime",
        help="Synthetic local surface id. Reuse the same id to exercise queue/coalescing behavior.",
    )
    parser.add_argument(
        "--lane-id",
        default="",
        help="Optional synthetic lane id to isolate runtime/session behavior within a shared surface.",
    )
    parser.add_argument(
        "--lane-kind",
        default="",
        help="Optional lane kind passed through to the runtime.",
    )
    parser.add_argument(
        "--lane-label",
        default="",
        help="Optional lane label passed through to the runtime.",
    )
    parser.add_argument(
        "--no-dm",
        action="store_true",
        help="Mark the probe as non-DM instead of DM.",
    )
    parser.add_argument(
        "--interval-ms",
        type=float,
        default=0.0,
        help="Delay between launches, in milliseconds.",
    )
    parser.add_argument(
        "--parallel",
        action="store_true",
        help="Launch all probes concurrently, offset by --interval-ms.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=45.0,
        help="Per-request timeout in seconds.",
    )
    parser.add_argument(
        "--show-events",
        action="store_true",
        help="Include captured agent stream events in the output.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit raw JSON results instead of a human summary.",
    )
    return parser.parse_args()


def _load_messages(args: argparse.Namespace) -> List[str]:
    messages = [str(item).strip() for item in list(args.messages or []) if str(item).strip()]
    if args.messages_file is not None:
        for line in args.messages_file.read_text(encoding="utf-8").splitlines():
            text = str(line).strip()
            if text:
                messages.append(text)

    if not messages and not sys.stdin.isatty():
        for line in sys.stdin.read().splitlines():
            text = str(line).strip()
            if text:
                messages.append(text)

    if not messages:
        raise SystemExit("Provide at least one message or use --messages-file.")
    return messages


async def _send_probe(
    client: httpx.AsyncClient,
    *,
    index: int,
    message: str,
    surface_id: str,
    lane_id: str,
    lane_kind: str,
    lane_label: str,
    is_dm: bool,
    delay_seconds: float,
    timeout_seconds: float,
    capture_agent_stream: bool,
) -> dict[str, Any]:
    await asyncio.sleep(delay_seconds)
    started_at = time.perf_counter()
    try:
        response = await client.post(
            "/api/web/runtime/local/probe/message",
            json={
                "content": message,
                "surface_id": surface_id,
                "lane_id": lane_id,
                "lane_kind": lane_kind,
                "lane_label": lane_label,
                "is_dm": is_dm,
                "capture_agent_stream": capture_agent_stream,
                "timeout_seconds": timeout_seconds,
            },
        )
        elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)
        try:
            body = response.json()
        except Exception:
            body = {"status": "error", "raw_body": response.text}
        return {
            "index": index,
            "message": message,
            "status_code": response.status_code,
            "elapsed_ms": elapsed_ms,
            "body": body,
        }
    except Exception as exc:
        elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)
        return {
            "index": index,
            "message": message,
            "status_code": None,
            "elapsed_ms": elapsed_ms,
            "body": {"status": "error", "detail": repr(exc)},
        }


def _print_human(results: List[dict[str, Any]], *, show_events: bool) -> None:
    for result in results:
        body = dict(result.get("body") or {})
        delivered = bool(body.get("response_delivered"))
        responses = list(body.get("responses") or [])
        print(
            f"[{result['index']}] status={result['status_code']} elapsed_ms={result['elapsed_ms']} "
            f"delivered={delivered} responses={len(responses)}"
        )
        print(f"message: {result['message']}")
        if responses:
            for item in responses:
                kind = str(item.get("kind") or "unknown")
                content = item.get("content")
                print(f"{kind}: {json.dumps(content, ensure_ascii=False)}")
        detail = body.get("detail")
        if detail:
            print(f"detail: {detail}")
        if show_events and body.get("agent_events"):
            print(f"agent_events: {json.dumps(body.get('agent_events'), ensure_ascii=False)}")
        print("")


async def _main_async(args: argparse.Namespace) -> int:
    messages = _load_messages(args)
    base_url = str(args.base_url or "http://127.0.0.1:8000").rstrip("/")
    interval_seconds = max(float(args.interval_ms or 0.0), 0.0) / 1000.0
    timeout_seconds = max(float(args.timeout or 0.0), 1.0)

    async with httpx.AsyncClient(
        base_url=base_url,
        timeout=timeout_seconds + 5.0,
    ) as client:
        if args.parallel:
            tasks = [
                asyncio.create_task(
                    _send_probe(
                        client,
                        index=index,
                        message=message,
                        surface_id=str(args.surface_id or "probe:local-runtime"),
                        lane_id=str(args.lane_id or ""),
                        lane_kind=str(args.lane_kind or ""),
                        lane_label=str(args.lane_label or ""),
                        is_dm=not bool(args.no_dm),
                        delay_seconds=index * interval_seconds,
                        timeout_seconds=timeout_seconds,
                        capture_agent_stream=bool(args.show_events),
                    )
                )
                for index, message in enumerate(messages)
            ]
            results = await asyncio.gather(*tasks)
        else:
            results = []
            for index, message in enumerate(messages):
                results.append(
                    await _send_probe(
                        client,
                        index=index,
                        message=message,
                        surface_id=str(args.surface_id or "probe:local-runtime"),
                        lane_id=str(args.lane_id or ""),
                        lane_kind=str(args.lane_kind or ""),
                        lane_label=str(args.lane_label or ""),
                        is_dm=not bool(args.no_dm),
                        delay_seconds=interval_seconds if index > 0 else 0.0,
                        timeout_seconds=timeout_seconds,
                        capture_agent_stream=bool(args.show_events),
                    )
                )

    results = sorted(results, key=lambda item: int(item["index"]))
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        _print_human(results, show_events=bool(args.show_events))

    return 0 if all(result.get("status_code") and int(result["status_code"]) < 400 for result in results) else 1


def main() -> int:
    args = _parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
