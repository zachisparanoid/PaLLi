# Cognitive observatory

The cognitive observatory is an optional, private trace collector and a separate local dashboard. It helps inspect what PaLLi's faculties read and write, what reaches a prompt, what decisions are observed, and what happened afterward. It does not assume that an item affected a reply merely because the model saw it.

## Enable collection

Collection is **off by default**. To opt in, add this line to your local `.env.local`:

```dotenv
PALLI_COGNITIVE_TRACE=1
```

Restart PaLLi using your normal control script when convenient. Changes to the environment and instrumentation do not take effect in an already running process. Set the value back to `0` and restart to turn collection off. The dashboard is independent: it can read retained traces even when PaLLi is stopped or tracing is off.

Open the dashboard in a separate terminal:

```bash
venv/bin/python cognitive_dashboard/server.py --port 8765
```

Open a complete URL printed by the command. By default, the dashboard listens on all IPv4 interfaces and prints the discovered LAN and Tailscale addresses, the Tailscale MagicDNS name when available, and a loopback URL. The token is exchanged for a same-site, HTTP-only browser cookie. Requests require the token, a recognized local Host and a loopback, private IPv4 or Tailscale IPv4 source; access from public IPv4 addresses is rejected. It reads the trace store and displays new conversation messages from the application database. Stop a manually started dashboard with Ctrl-C. The **CONNECTED** indicator means the browser can poll the dashboard, not that PaLLi is currently running. Use `--port 0` to choose an available port. Use `--bind 127.0.0.1` for local-only access, or `--bind <local-ip>` to listen on a single discovered interface. If you use an additional local DNS name, pass `--allow-host your-name` (repeatable). Interface and MagicDNS names are discovered at startup, so restart the dashboard if they change.

The dashboard uses HTTP: the token and trace metadata are not encrypted on a normal LAN. Tailscale encrypts traffic within the tailnet. Use a trusted LAN or connect over Tailscale; do not share the token URL with someone who should not see the traces. Firewall rules can still block the dashboard port.

The views provide an overview, chronological timeline with older-page loading, nested execution traces, faculty activity, pseudonymous state threads and comparisons of named runs. An optional label typed in a state thread stays in the current browser tab and is not sent to the server. The evidence guide explains what each type of observation means.

## Privacy and retention

The collector records metadata, not prompt text, conversations, model replies, raw record IDs, API credentials or exception messages. Record IDs and content versions are keyed HMAC fingerprints; timestamps, component names, activity and relationships between records are still sensitive metadata. The key and daily trace files are created owner-only under `data/cognitive_traces/`, which Git ignores. The key lives beside the events: this is pseudonymization, not encryption. Deleting it prevents new events from matching older identifiers.

Defaults are 14 days of event-file retention and 20 MiB per UTC day. Optional `.env.local` settings:

```dotenv
PALLI_COGNITIVE_TRACE_DAYS=14
PALLI_COGNITIVE_TRACE_MAX_MB=20
PALLI_COGNITIVE_TRACE_RUN=default
```

Choose a non-sensitive run label; it appears in trace metadata and the dashboard. Pruning runs when tracing starts and removes only matching old event files, not the key. Once the daily cap is reached, further events are dropped and a warning is logged. Collection and writes are best-effort: missing events may reflect a cap, interruption, unavailable hook, disabled component or a path that is not instrumented. The collector uses synchronous hooks, so it can add latency; measure whole-turn overhead before relying on timing measurements. For test isolation, `PALLI_TEST_DATA_ROOT` redirects the entire trace store into the test data root.

## Following a record

State threads show a stable pseudonymous ID and, where available, a content-version fingerprint. IDs are derived from a store namespace and record ID, so an independently cloned database can reuse the same ID. A version fingerprint covers text rather than every field. Identical moment/daydream text is intentionally grouped. An ID by itself is not a semantic description.

To identify a record from this installation's current state, copy its ID from the dashboard and run:

```bash
venv/bin/python cognitive_dashboard/resolve_state.py --entity 'stake:REPLACE_WITH_ID'
```

Private text is hidden by default. Add `--show-text` only when you want it printed in your terminal. The resolver reads local files or the main SQLite database read-only; it never sends the text to the dashboard or collector. `--data-dir` and `--database` can select paths inside this installation's data root. Records that have been deleted or overwritten cannot necessarily be resolved to an older content version.

## Reading the evidence

- **Executed** means a hook ran; it is not proof that an internally best-effort operation succeeded.
- **Retrieved** means a component read material; it may never be selected or rendered.
- **In prompt** means the instrumented renderer included a band or identified tag. It does not prove that the model used it.
- **Matched text · correlation** means a retrieved inner-memory hit exactly matched a bullet in the reply prompt. Another source might contain identical text.
- **Model decision** and **model output** report a selection/impulse or a generated reply. They are not recipient-delivery confirmations.
- **Persisted** means an instrumented storage operation reported success. **Delivery requested** means the outbound rail requested release; it does not mean a person saw the message.
- **Deterministic comparison** re-evaluates one local rule: the selected inner-memory hits with the present-moment recall lean removed, holding the candidate set and other scores fixed. It does not measure a change in the model's final wording.
- **Analyst-coded outcome** is a manual classification, not an automatic or independent judgment. `cognitive_dashboard/annotate.py --help` explains its bounded labels.

Traces link an initiating turn to nested execution and detached tasks when context is available. They also link selected stakes, wishes, fears, thoughts and moments across observed reads and writes. Some background paths, exact source versions, semantic feedback, and recipient delivery are not instrumented. Do not treat a blank cell, a matched text, or a changed event count as a causal conclusion. For model-mediated influence, use controlled, independently initialized comparisons and inspect actual responses.

The older-page viewer uses timestamp boundaries; simultaneous events with an identical boundary timestamp can be omitted. The dashboard opens recent events first and loads older pages on request. Existing logs from before collection was enabled cannot be reconstructed as detailed traces.

### Living map and new conversations

The **Living map** shows observed faculties and recorded parent-child execution links. Live pulses show newly collected observations one at a time in timestamp order; they indicate execution nesting, not a verified transfer of information or causation. Events are polled in batches, and animation gaps are bounded and adjusted for readability and to limit lag, so they are not an exact clock. When more than 120 animations are waiting, the oldest are skipped and the count is displayed; the underlying events remain in trace history. Select a node or line for the recorded evidence, then open its trace or follow a state ID. Replay a retained trace with play, pause, step, or the scrubber; replay follows timestamp order with adjusted gaps. The graph is limited to loaded trace events (the latest 350 in live view); missing instrumentation, unloaded history, or missing parents can leave paths absent. The display polls about every five seconds rather than streaming instantaneously.

The conversation panel reads the latest 30 conversation rows created **after the dashboard starts**, directly from the configured local SQLite database in read-only mode. It does not change the metadata-only trace collector, alter conversation history, or establish a message-to-trace match. Rows appear after PaLLi stores them; unpersisted or failed messages may not appear. If the database is unavailable at startup, the starting point is set when the feed first connects. Restarting the dashboard resets the starting point.

**Dashboard access now includes private conversation text.** Anyone with the dashboard access token on a permitted network can view it. Direct LAN HTTP is unencrypted; use a trusted network or Tailnet, and do not share the token. The dashboard does not write conversation text into trace files.


The **web search** faculty is visible in the live map even before it runs. When tracing is enabled, the conversation search tool and the shared Exa client are instrumented, including background curiosity calls. A client call records its start, completion, and the number of results returned; an unavailable result is separate from a confirmed empty list. A tool invocation alone does not prove an external search ran, and results returned do not prove PaLLi used them. Queries, result text, URLs, and error text are not included in the cognitive trace. Other search implementations are not covered by this hook. Restart PaLLi after installing or changing instrumentation.


### Launcher-managed dashboard

`venv/bin/python scripts/palli_ctl.py start` also starts the cognitive dashboard on port 8765. The launcher prints private tokenized URLs, and `venv/bin/python scripts/palli_ctl.py logs cognitive` shows them again. The token-bearing `logs/launcher/cognitive.log` is recreated with owner-only permissions for each managed start; access links change when the dashboard restarts. `palli_ctl.py status` reports the dashboard and `palli_ctl.py stop` stops it along with the launcher-managed services. A manually started or other service already using port 8765 is left alone, and dashboard startup failure does not stop PaLLi. To collect new traces, `PALLI_COGNITIVE_TRACE=1` must still be enabled in the environment before PaLLi starts. The dashboard continues to listen on LAN and Tailnet; its access link grants access to new conversation text.
