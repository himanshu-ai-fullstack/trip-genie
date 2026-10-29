import { intentKey, isCacheable, similarityThreshold, type AnswerCache, type CachedAnswer } from "../cache/answerCache.js";
import type { Providers } from "../providers/index.js";
import type { Embedder } from "../retrieval/embed.js";
import type { ActivityIndex } from "../retrieval/index.js";
import { CircuitBreaker, isTransientModelError } from "../safety/circuitBreaker.js";
import { KillSwitch, RESTING_MESSAGE } from "../safety/killSwitch.js";
import { checkRateLimits, checkSessionBudget, LIMITS, recordSpend } from "../safety/limits.js";
import type { KV } from "../store/kv.js";
import type { SessionStore } from "../store/sessions.js";
import type { Activity, GenieEvent, ModelId } from "../types.js";
import { effectiveIntent, extractIntent, mergeProfile } from "./intent.js";
import { buildTurnPrompt, SYSTEM_PROMPT } from "./prompt.js";
import { isRejection, MAX_TURNS, routeModel } from "./router.js";
import type { AgentRunner } from "./runner.js";
import { createGenieTools } from "./tools.js";

export type GenieDeps = {
  index: ActivityIndex;
  providers: Providers;
  kv: KV;
  cache: AnswerCache;
  embedder: Embedder;
  sessions: SessionStore;
  killSwitch: KillSwitch;
  breaker: CircuitBreaker;
  runner: AgentRunner;
  log?: (entry: Record<string, unknown>) => void;
};

export type TurnRequest = { sessionId: string; message: string; ip: string; signal?: AbortSignal; today?: string };

type Emit = (event: GenieEvent) => void;

function done(emit: Emit, model: ModelId, routeReason: string, extra: Partial<Extract<GenieEvent, { type: "done" }>> = {}) {
  emit({ type: "done", model, routeReason, cached: false, costUsd: 0, turns: 0, stoppedEarly: null, ...extra });
}

function shownIn(events: CachedAnswer["events"]): Activity[] {
  return events.flatMap((e) => (e.type === "cards" || e.type === "itinerary" ? e.activities : []));
}

/** Replays a cached answer as a stream so the UI behaves the same on a hit. */
function replay(answer: CachedAnswer, emit: Emit) {
  for (const ev of answer.events) {
    if (ev.type === "text") for (const chunk of ev.delta.match(/.{1,32}(\s|$)/g) ?? [ev.delta]) emit({ type: "text", delta: chunk });
    else emit(ev);
  }
}

/** One traveler turn: gates, cache, routing, the agent run, then bookkeeping. */
export async function runGenieTurn(deps: GenieDeps, req: TurnRequest, emit: Emit): Promise<void> {
  const log = deps.log ?? (() => {});
  const message = req.message.trim().slice(0, LIMITS.maxQuestionChars);
  if (!message) return emit({ type: "error", message: "Ask me about a destination to get started." });

  // 1. Kill switch and limits run before anything costs money.
  const mode = await deps.killSwitch.mode();
  if (mode === "off") {
    emit({ type: "text", delta: RESTING_MESSAGE });
    return done(emit, "offline", "kill switch off");
  }
  for (const check of [await checkRateLimits(deps.kv, req.ip, req.sessionId), await checkSessionBudget(deps.kv, req.sessionId)]) {
    if (!check.ok) return emit({ type: "error", message: check.reason });
  }

  // 2. Intent and trip profile.
  const session = await deps.sessions.load(req.sessionId);
  const rawIntent = extractIntent(message);
  session.profile = mergeProfile(session.profile, rawIntent);
  const intent = effectiveIntent(rawIntent, session.profile);
  const today = req.today ?? new Date().toISOString().slice(0, 10);

  const remember = async (answerText: string, shown: Activity[], model: ModelId) => {
    const shownNote = shown.length ? ` [showed: ${shown.map((a) => a.name).join("; ")}]` : "";
    session.history.push({ role: "user", text: message }, { role: "assistant", text: `${answerText}${shownNote}`.trim() });
    session.lastModel = model;
    await deps.sessions.save(session);
  };

  // 3. Semantic answer cache.
  const cacheable = isCacheable(intent);
  const key = intentKey(intent);
  const catalogVersion = await deps.index.version();
  const [embedding] = cacheable ? await deps.embedder.embed([message]) : [undefined];
  if (cacheable && embedding) {
    const hit = await deps.cache.lookup(key, embedding, catalogVersion, similarityThreshold(deps.embedder));
    if (hit) {
      log({ event: "cache_hit", key, similarity: hit.similarity, matched: hit.question });
      replay(hit.answer, emit);
      const text = hit.answer.events.flatMap((e) => (e.type === "text" ? [e.delta] : [])).join("");
      const shown = shownIn(hit.answer.events);
      await remember(text, shown, hit.answer.model);
      return done(emit, hit.answer.model, "answer cache", { cached: true });
    }
  }

  // 4. Route, then let the breaker swap in the fallback if the primary is failing.
  const route = routeModel({
    intent,
    previousAnswerRejected: session.history.length > 0 && isRejection(message),
    plannerDisabled: mode === "degraded",
  });
  const { model, fallback, tripped } = deps.breaker.resolve(route.model);
  const routeReason = tripped ? `${route.reason}; breaker open, using fallback` : route.reason;
  log({ event: "route", model, fallback, reason: routeReason, intent });

  // 5. Run the agent with every way to stop it wired to one abort controller.
  const abort = new AbortController();
  const onAbort = () => abort.abort();
  req.signal?.addEventListener("abort", onAbort);
  const timer = setTimeout(onAbort, LIMITS.turnTimeoutMs);
  const unsubscribe = deps.killSwitch.onChange((m) => m === "off" && abort.abort());

  const recorded: CachedAnswer["events"] = [];
  const seen = new Map<string, Activity>();
  const tools = createGenieTools({
    index: deps.index,
    providers: deps.providers,
    kv: deps.kv,
    seen,
    party: intent.party,
    liveDisabled: mode === "degraded",
    emit: (ev) => {
      if (ev.type === "cards" || ev.type === "itinerary") recorded.push(ev);
      emit(ev);
    },
  });

  let streamed = "";
  try {
    const result = await deps.runner.run(
      {
        model,
        fallbackModel: fallback,
        maxTurns: MAX_TURNS[model] ?? 8,
        budgetUsd: LIMITS.turnBudgetUsd,
        systemPrompt: SYSTEM_PROMPT,
        prompt: buildTurnPrompt(session, message, today),
        tools,
        abortController: abort,
        message,
      },
      (delta) => {
        streamed += delta;
        emit({ type: "text", delta });
      },
    );
    deps.breaker.recordSuccess(model);
    await recordSpend(deps.kv, req.sessionId, result.costUsd);

    const stoppedEarly = abort.signal.aborted ? "aborted" : result.stoppedEarly;
    if (stoppedEarly === "step limit" || stoppedEarly === "budget") {
      emit({ type: "text", delta: "\n\n(I stopped early to keep this quick. Ask me to keep going if you'd like more.)" });
    }

    const shown = shownIn(recorded);
    if (cacheable && embedding && !stoppedEarly && shown.length > 0) {
      await deps.cache.store(key, message, embedding, { events: [{ type: "text", delta: result.text }, ...recorded], model: result.answeredBy }, catalogVersion);
    }
    await remember(result.text, shown, result.answeredBy);
    log({ event: "turn", model: result.answeredBy, costUsd: result.costUsd, turns: result.turns, stoppedEarly });
    done(emit, result.answeredBy, routeReason, { costUsd: result.costUsd, turns: result.turns, stoppedEarly });
  } catch (err) {
    if (abort.signal.aborted) {
      await remember(streamed, shownIn(recorded), model);
      return done(emit, model, routeReason, { stoppedEarly: "aborted" });
    }
    if (isTransientModelError(err)) deps.breaker.recordFailure(model);
    log({ event: "turn_error", model, error: err instanceof Error ? err.message : String(err) });
    emit({ type: "error", message: "Genie hit a problem answering that. Please try again." });
  } finally {
    clearTimeout(timer);
    unsubscribe();
    req.signal?.removeEventListener("abort", onAbort);
  }
}
