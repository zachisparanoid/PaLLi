/**
 * The door's whole contract with the backend, in one file (first door, task 3,
 * 2026-09-17).
 *
 * Three endpoints and nothing else:
 *
 *   GET  /api/setup/status   — what the door shows: is there a brain, which
 *                              brains can be picked, what the two chat doors
 *                              are doing. Reads only, so it needs no CSRF.
 *   POST /api/setup/brain    — the pasted credential, tested live, then saved.
 *   POST /api/chat/message   — hello.
 *
 * Why the two POSTs do not go through `apiPost`: `lib/api.ts` aborts every
 * request at 30 seconds. Her replies routinely take 20, a turn that uses a tool
 * runs for minutes, and a brain test against a local server can sit waiting
 * while the model loads. A 30-second abort would report "timed out" for a turn
 * that was going fine. So the two calls that wait on HER keep their own
 * ceilings, and everything else about them — the backend URL rule, the
 * double-submit CSRF cookie, `credentials: "include"` — is the same dance the
 * account page and the dashboard already do.
 *
 * Every body that comes back is normalised before it leaves this file. A door
 * whose backend answered with an HTML error page must say "the backend is not
 * answering", not throw on `status.doors.discord`.
 */
import { ApiError, apiDelete, apiGet, apiPost, getBackendHttpUrl } from "@/lib/api";
import { getCsrfToken } from "@/lib/csrf";

// The real commands, as SETUP.md and README.md spell them. Never a subcommand
// that does not exist: a stranger reading this page is reading it because
// something is not running.
export const START_COMMAND = "venv/bin/python scripts/palli_ctl.py start";
export const RESTART_COMMAND = "venv/bin/python scripts/palli_ctl.py restart";

export const BACKEND_SILENT_MESSAGE = "Her backend is not answering.";

/** She is slow often enough that the page says so rather than giving up. */
export const SLOW_REPLY_AFTER_MS = 30_000;
/** The only thing that ends a turn from this side: five minutes. */
const CHAT_CEILING_MS = 300_000;
/** One PONG. Generous, because a local server may be loading the model. */
const BRAIN_TEST_CEILING_MS = 120_000;

export type DoorState = "open" | "closed" | "unset";

export type ProviderOption = {
  name: string;
  label: string;
  available: boolean;
  reason: string | null;
  /** ["api_key"], or ["base_url", "model"] for a local server. */
  needs: string[];
};

export type SetupStatus = {
  brain_ready: boolean;
  provider: string;
  /** Meaningless while `brain_ready` is false — a shipped default, not a choice. */
  model: string;
  local_mode: boolean;
  doors: { discord: DoorState; telegram: DoorState; mobile: DoorState };
  doors_need_restart: boolean;
  address: string;
  providers: ProviderOption[];
};

export type BrainRequest = {
  provider: string;
  api_key?: string;
  base_url?: string;
  model?: string;
};

export type BrainSaved = {
  ok: true;
  provider: string;
  model: string;
  /** null where the provider reports none — the page says so, never guesses. */
  balance: string | null;
  doors_need_restart: boolean;
  /** true = it is on disk, but the live swap failed: restart to use it. */
  restart_required: boolean;
};

export type BrainRefused = { ok: false; error: string };

export type BrainResult = BrainSaved | BrainRefused;

export type ChatAttachment = {
  filename: string;
  path: string;
  url: string;
  content_type: string;
};

export type ChatBeat = { text: string; delay_after_previous_seconds: number };

export type ChatReply = {
  response: string;
  delegation_text: string;
  attachments: ChatAttachment[];
  beats: ChatBeat[];
  /** She chose to stay quiet (Batch 8): nothing of hers to draw, and nothing went wrong. */
  quiet: boolean;
};

// ---------------------------------------------------------------------------
// Reading a body that may not be the body we asked for
// ---------------------------------------------------------------------------

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null ? (value as Record<string, unknown>) : {};
}

function asText(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asList(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function asDoor(value: unknown): DoorState {
  return value === "open" || value === "closed" ? value : "unset";
}

function normaliseProvider(raw: unknown): ProviderOption {
  const row = asRecord(raw);
  const needs = asList(row.needs).map(asText).filter(Boolean);
  return {
    name: asText(row.name),
    label: asText(row.label) || asText(row.name),
    available: row.available === true,
    reason: typeof row.reason === "string" && row.reason.trim() ? row.reason : null,
    needs: needs.length ? needs : ["api_key"],
  };
}

function normaliseStatus(raw: unknown): SetupStatus {
  const body = asRecord(raw);
  if (typeof body.brain_ready !== "boolean") {
    // A 200 that is not the setup status: the dev server serving its own HTML
    // because nothing is proxying /api, or a half-started backend. Same
    // treatment as a refused connection — the page says so and offers a retry.
    throw new ApiError(BACKEND_SILENT_MESSAGE, 502);
  }
  const doors = asRecord(body.doors);
  return {
    brain_ready: body.brain_ready,
    provider: asText(body.provider),
    model: asText(body.model),
    local_mode: body.local_mode === true,
    doors: { discord: asDoor(doors.discord), telegram: asDoor(doors.telegram), mobile: asDoor(doors.mobile) },
    doors_need_restart: body.doors_need_restart === true,
    address: asText(body.address),
    providers: asList(body.providers).map(normaliseProvider).filter((p) => Boolean(p.name)),
  };
}

function normaliseBrainResult(raw: unknown): BrainResult {
  const body = asRecord(raw);
  if (body.ok !== true) {
    return { ok: false, error: asText(body.error).trim() || "The brain test did not say why it failed." };
  }
  return {
    ok: true,
    provider: asText(body.provider),
    model: asText(body.model),
    balance: typeof body.balance === "string" && body.balance.trim() ? body.balance : null,
    doors_need_restart: body.doors_need_restart === true,
    restart_required: body.restart_required === true,
  };
}

function normaliseChatReply(raw: unknown): ChatReply {
  const body = asRecord(raw);
  return {
    response: asText(body.response),
    delegation_text: asText(body.delegation_text),
    attachments: asList(body.attachments).map((item) => {
      const row = asRecord(item);
      return {
        filename: asText(row.filename),
        path: asText(row.path),
        url: asText(row.url),
        content_type: asText(row.content_type),
      };
    }),
    beats: asList(body.beats).map((item) => {
      const row = asRecord(item);
      const delay = Number(row.delay_after_previous_seconds);
      return {
        text: asText(row.text),
        delay_after_previous_seconds: Number.isFinite(delay) ? delay : 0,
      };
    }),
    quiet: body.quiet === true,
  };
}

// ---------------------------------------------------------------------------
// The two POSTs
// ---------------------------------------------------------------------------

function isAbort(error: unknown): boolean {
  return asRecord(error).name === "AbortError";
}

async function ensureCsrfCookie(): Promise<void> {
  if (getCsrfToken()) return;
  try {
    // The same bootstrap the dashboard and the account page do on mount: in
    // local mode there is no login, so GET /auth/me is where the double-submit
    // cookie is minted. Without it every POST here would 403.
    await apiGet<unknown>("/api/web/auth/me");
  } catch {
    // Swallowed on purpose: whatever is wrong will be reported by the POST
    // itself, in its own words, rather than as a failure to fetch a cookie.
  }
}

/**
 * A refusal, read twice: the sentence to show, and the body it came in.
 *
 * `body` is what decides, later, whether anything actually ANSWERED: a JSON
 * body means the backend was there and had something to say, an unparseable
 * one means the sentence was invented here from the status line alone. That
 * difference is the whole of `readFailure`'s 5xx rule, so it is carried rather
 * than re-derived (fix round 1, important 1).
 */
type Refusal = { sentence: string; body: unknown };

async function readRefusal(response: Response): Promise<Refusal> {
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    // Not JSON. The status line below is all there is to say.
  }
  const spoken = asRecord(body);
  const detail = spoken.detail;
  if (typeof detail === "string" && detail.trim()) return { sentence: detail.trim(), body };
  if (Array.isArray(detail)) {
    // FastAPI's validation shape, with `input` already dropped by the setup
    // router so a malformed body can never quote the pasted key back.
    const sentences = detail
      .map((item) => asText(asRecord(item).msg))
      .filter(Boolean);
    if (sentences.length) return { sentence: sentences.join("; "), body };
  }
  if (typeof spoken.error === "string" && spoken.error.trim()) {
    return { sentence: spoken.error.trim(), body };
  }
  if (response.status === 403) {
    return { sentence: "That request was refused (CSRF). Reload the page and try again.", body };
  }
  return { sentence: `The backend refused that request (${response.status}).`, body };
}

async function postJson<T>(
  endpoint: string,
  body: unknown,
  options: { ceilingMs: number; ceilingMessage: string; onSlow?: () => void; slowAfterMs?: number },
): Promise<T> {
  await ensureCsrfCookie();

  const controller = new AbortController();
  const ceiling = setTimeout(() => controller.abort(), options.ceilingMs);
  const slow =
    options.onSlow && options.slowAfterMs
      ? setTimeout(options.onSlow, options.slowAfterMs)
      : null;

  const headers = new Headers({ "Content-Type": "application/json" });
  const token = getCsrfToken();
  if (token) headers.set("X-CSRF-Token", token);

  let response: Response;
  try {
    response = await fetch(getBackendHttpUrl(endpoint), {
      method: "POST",
      headers,
      credentials: "include",
      body: JSON.stringify(body),
      signal: controller.signal,
    });
  } catch (error) {
    if (isAbort(error)) throw new ApiError(options.ceilingMessage, 408);
    throw new ApiError(BACKEND_SILENT_MESSAGE, 503);
  } finally {
    clearTimeout(ceiling);
    if (slow !== null) clearTimeout(slow);
  }

  if (!response.ok) {
    const refusal = await readRefusal(response);
    // The body travels on the error: `readFailure` needs to know whether
    // anything answered, and a status code alone cannot say.
    throw new ApiError(refusal.sentence, response.status, refusal.body);
  }

  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiError(BACKEND_SILENT_MESSAGE, 502);
  }
}

// ---------------------------------------------------------------------------
// What the page calls
// ---------------------------------------------------------------------------

export async function fetchSetupStatus(): Promise<SetupStatus> {
  return normaliseStatus(await apiGet<unknown>("/api/setup/status"));
}

/**
 * Test a brain live and, only if it answers, save it.
 *
 * A refusal the backend can put in a sentence comes back as `{ok: false}`; a
 * refusal it puts in a status code (400 for a missing field, 403 for CSRF,
 * 503 for a backend that is not there) is thrown as an `ApiError` whose
 * message is the sentence to show under the field.
 */
export async function saveBrain(request: BrainRequest): Promise<BrainResult> {
  const body = await postJson<unknown>("/api/setup/brain", request, {
    ceilingMs: BRAIN_TEST_CEILING_MS,
    ceilingMessage: "The brain test did not finish in two minutes. The log will say why.",
  });
  return normaliseBrainResult(body);
}

/**
 * One turn. `onSlow` fires at 30 seconds and the request KEEPS GOING — it is a
 * note for the person waiting, not a timeout.
 */
export async function sendChatMessage(
  message: string,
  options: { onSlow?: () => void } = {},
): Promise<ChatReply> {
  const body = await postJson<unknown>(
    "/api/chat/message",
    { message },
    {
      ceilingMs: CHAT_CEILING_MS,
      ceilingMessage: "She did not answer within five minutes. The log will say why.",
      onSlow: options.onSlow,
      slowAfterMs: SLOW_REPLY_AFTER_MS,
    },
  );
  return normaliseChatReply(body);
}

/** One message of the web conversation, as GET /api/chat/history returns it. */
export type ChatHistoryMessage = {
  /** the record's own id: newer messages have larger ids */
  id: number;
  role: "user" | "assistant";
  text: string;
  timestamp: number;
  /** a reminder she delivered on her own, not a reply to something typed */
  reminder: boolean;
};

function normaliseHistory(raw: unknown): ChatHistoryMessage[] {
  return asList(asRecord(raw).messages).flatMap((item) => {
    const row = asRecord(item);
    const id = Number(row.id);
    const role = row.role === "user" || row.role === "assistant" ? row.role : null;
    const text = asText(row.text);
    if (!Number.isFinite(id) || !role || !text.trim()) return [];
    return [{ id, role, text, timestamp: Number(row.timestamp) || 0, reminder: row.reminder === true }];
  });
}

/**
 * The web conversation (web-page reminders, 2026-10-10). With no options: the
 * recent messages, oldest first, for when the page opens. With `sinceId` and
 * `remindersOnly`: only reminders that arrived after the newest message the
 * page has — the 15-second check. A failure throws, like every call here.
 */
export async function fetchChatHistory(
  options: { sinceId?: number; remindersOnly?: boolean; limit?: number } = {},
): Promise<ChatHistoryMessage[]> {
  const query = new URLSearchParams();
  if (options.sinceId) query.set("since_id", String(options.sinceId));
  if (options.remindersOnly) query.set("reminders_only", "true");
  if (options.limit) query.set("limit", String(options.limit));
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return normaliseHistory(await apiGet<unknown>(`/api/chat/history${suffix}`));
}

const HER_BACKEND_BROKE_MESSAGE = "Her backend hit an error of its own while answering.";

/**
 * What to tell a person about a failure, in three parts.
 *
 * `sentence` is the one line to show them. `raw` is whatever the backend or the
 * fetch said in its own words, when that adds something the sentence does not
 * already carry — shown AS WELL, never instead. `silent` answers the only
 * question the page acts on: did anything answer at all? Only then is "start
 * her" the right advice.
 *
 * The three parts are one function because the first version had two
 * (`describeError` and `isBackendSilent`), and they were perfectly correlated:
 * the page asked both, so its "show the underlying words too" branch could
 * never run, and a 500 from a RUNNING backend was reported as silence with the
 * server's own message thrown away (fix round 1, important 1).
 *
 * The rule for a 5xx is therefore not the status but the BODY:
 *
 *   * 5xx with no JSON body — the dashboard reaches the backend through the
 *     Next proxy (`NEXT_PUBLIC_API_PROXY_TARGET`, which is how the launcher
 *     wires it), and a proxy whose upstream is down answers 500 ITSELF, with
 *     nothing in it. The page never sees the refused connection. That is
 *     silence, and the smoke of 2026-09-17 proved it is the shape a stranger
 *     with a stopped backend actually gets.
 *   * 5xx WITH a JSON body — something is running and it said what went wrong.
 *     Telling them to start it would be a lie, and dropping its words would
 *     throw away the only clue.
 *
 * 408 is neither: it is one of this file's own ceilings, and it has a truer
 * sentence of its own ("she did not answer within five minutes").
 */
export type DoorFailure = { sentence: string; raw: string; silent: boolean };

export function readFailure(error: unknown): DoorFailure {
  if (error instanceof ApiError) {
    const words = String(error.message || "").trim();
    // `lib/api.ts` and `readRefusal` both put the PARSED body here and leave it
    // null when there was none — so this is "did it speak", not "did it 500".
    const spoke = error.data !== null && error.data !== undefined;
    if (error.status === 0 || (error.status >= 500 && !spoke)) {
      return {
        sentence: BACKEND_SILENT_MESSAGE,
        raw: words === BACKEND_SILENT_MESSAGE ? "" : words,
        silent: true,
      };
    }
    if (error.status >= 500) {
      return { sentence: HER_BACKEND_BROKE_MESSAGE, raw: words, silent: false };
    }
    return {
      sentence: words || `The backend refused that request (${error.status}).`,
      raw: "",
      silent: false,
    };
  }
  if (error instanceof TypeError) {
    // A raw fetch failure that `lib/api.ts` did not wrap (a non-loopback host).
    return { sentence: BACKEND_SILENT_MESSAGE, raw: String(error.message || "").trim(), silent: true };
  }
  if (error instanceof Error && error.message.trim()) {
    return { sentence: error.message.trim(), raw: "", silent: false };
  }
  return { sentence: "Something went wrong, and it did not say what.", raw: "", silent: false };
}

// ---------------------------------------------------------------------------
// The phone app (the mobile door, 2026-10-13)
// ---------------------------------------------------------------------------

export type PairStart = {
  /** "K7QM-2XPA": good once, for ten minutes. */
  code: string;
  expires_at: number;
  /** MOBILE_PUBLIC_URL, or null when it is not set (the phone types the address). */
  url: string | null;
  link: string | null;
  /** The QR as an SVG document; absent without an address. */
  qr_svg?: string;
};

export type PairedPhone = {
  id: number;
  name: string;
  created_at: number;
  last_seen_at: number | null;
  this_device: boolean;
};

export async function startPhonePairing(): Promise<PairStart> {
  await ensureCsrfCookie();
  return apiPost<PairStart>("/api/mobile/pair/start", {});
}

export async function listPhones(): Promise<PairedPhone[]> {
  const body = await apiGet<{ devices?: PairedPhone[] }>("/api/mobile/devices");
  return Array.isArray(body.devices) ? body.devices : [];
}

export async function unpairPhone(id: number): Promise<void> {
  await ensureCsrfCookie();
  await apiDelete<unknown>(`/api/mobile/devices/${id}`);
}
