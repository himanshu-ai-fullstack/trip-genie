# Trip Genie: Architecture and End-to-End Flow

Trip Genie is a streaming chat that finds things to do at a travel destination (sightseeing, adventure, shows, food, culture) and links out to book them. The frontend is Next.js; the backend is Python FastAPI calling Claude through the Anthropic SDK, with an offline scripted runner for demos and tests.

## Architecture

```
┌──────────────────────── Browser ────────────────────────┐
│ page.tsx  chat UI · Stop button (AbortController)       │
│           sessionId kept in localStorage                │
│ sse.ts    parses the event stream                       │
│ ActivityCard / Itinerary  render cards and day plans    │
└───────────────┬─────────────────────────────────────────┘
                │ POST /api/chat  {message, sessionId}   ◀── SSE events
┌───────────────▼──────── Next.js (apps/web) ─────────────┐
│ app/api/chat/route.ts  same-origin proxy, passes the    │
│ stream through; aborting it aborts the API call too     │
└───────────────┬─────────────────────────────────────────┘
                │ POST /api/chat
┌───────────────▼──────── FastAPI (apps/api) :8787 ───────┐
│ main.py      turn pipeline, SSE, admin endpoints        │
│ policies.py  kill switch · rate limits · budgets ·      │
│              router · circuit breaker · retry policy    │
│ intent.py    rules → Intent, merged into TripProfile    │
│ prompt.py    system prompt + per-task guidance          │
│ runner.py    AnthropicRunner (tool loop) | OfflineRunner│
│ tools.py     6 tools, validation, checkers, grounding   │
│ retrieval.py hybrid search (vector + keyword, RRF)      │
│ providers.py events (Ticketmaster/mock), tours, routes  │
│ trace.py     run trace, saved after each step           │
│ stores.py    KV, sessions, answer cache                 │
└──────┬──────────────┬──────────────────┬────────────────┘
       │              │                  │
┌──────▼─────┐ ┌──────▼──────────┐ ┌─────▼──────────────────┐
│ Claude API │ │ KV: Redis or    │ │ Postgres + pgvector or │
│ Haiku /    │ │ memory          │ │ in-memory              │
│ Sonnet /   │ │ sessions, spend,│ │ activity index,        │
│ Opus       │ │ rate limits,    │ │ answer cache (24 h)    │
│            │ │ kill switch,    │ │                        │
│            │ │ tool cache,     │ │                        │
│            │ │ run traces      │ │                        │
└────────────┘ └─────────────────┘ └────────────────────────┘
```

### Components

| Component | Responsibility | Code |
| --- | --- | --- |
| Chat UI | Sends questions, renders streamed text, cards and day plans; Stop button cancels the run | `apps/web/app/page.tsx`, `sse.ts`, `components/` |
| Web proxy | Same-origin proxy to the API; passes the SSE stream through unchanged | `apps/web/app/api/chat/route.ts` |
| Turn pipeline | Runs every stage of a turn and emits SSE events | `apps/api/app/main.py` |
| Policies | Kill switch, rate limits, spend caps, router, circuit breaker, retry policy | `apps/api/app/policies.py` |
| Intent extraction | Rule-based parsing of city, days, budget, party, interests, constraints | `apps/api/app/intent.py` |
| Prompting | System prompt plus per-turn trip profile, history and task guidance | `apps/api/app/prompt.py` |
| Runner | Claude tool loop, or the scripted offline runner | `apps/api/app/runner.py` |
| Tools | Six tools with validation, timeouts, retries, itinerary checker, grounding check | `apps/api/app/tools.py` |
| Retrieval | Hybrid activity search: hard filters, vector + keyword ranking fused with RRF | `apps/api/app/retrieval.py` |
| Providers | Events (Ticketmaster or mock), tours (mock), travel times (haversine) | `apps/api/app/providers.py` |
| Run trace | Durable per-turn state and observability, saved after every step | `apps/api/app/trace.py` |
| Stores | Key-value store (Redis or memory), sessions, answer cache | `apps/api/app/stores.py` |

### Data stores

| Store | Backend | Holds |
| --- | --- | --- |
| Key-value | Redis when `REDIS_URL` is set, otherwise an in-process dict | Sessions (7 days), spend per session (24 h), rate-limit counters, kill switch flag, tool cache (15 min), run traces (24 h) |
| Activity index | Postgres + pgvector when `DATABASE_URL` is set, otherwise in memory | Seed catalog of activities with embeddings |
| Answer cache | Postgres when `DATABASE_URL` is set, otherwise in memory | Verified answers, keyed by intent and question embedding (24 h) |

### Agent tools

The agent can only call these tools; it has no filesystem or shell access.

| Tool | What it does | Runs in parallel |
| --- | --- | --- |
| `search_activities` | Hybrid search over the activity index for one city | Yes |
| `find_events` | Dated shows and events between two dates | Yes |
| `get_tour_offers` | Bookable tours with prices | Yes |
| `travel_time` | Minutes between two activities found this turn | Yes |
| `show_activities` | Sends ranked activity cards to the UI | No, runs in order |
| `show_itinerary` | Checks a day plan and, if feasible, sends it to the UI | No, runs in order |

Cards and plans only accept activity ids a tool returned in the same turn, so the model can't invent a place or a booking link.

## End-to-end flow of one turn

Example question: "Plan a 3 day trip to Lisbon for a couple".

### 1. Browser to API

`page.tsx` posts `{message, sessionId}` to `/api/chat` on the Next.js server. The proxy route forwards it to FastAPI and streams the response straight back. The API's first event is `session`, carrying the session id the browser saves.

### 2. Gates

A `RunTrace` starts for the turn, then three gates run:

- **Kill switch:** if it's `off`, Genie replies with a resting message and stops.
- **Rate limits:** 20 requests per minute per IP and 60 per hour per session.
- **Session budget:** if the chat has spent its $3 (estimated from token usage), the turn is refused.

### 3. Understanding the question

Rules (regex and word lists, no LLM) extract: city Lisbon, 3 days, party couple, wants an itinerary. The result is merged into the session's `TripProfile`, so a follow-up like "make it cheaper" keeps the city.

### 4. Answer cache

The API looks up an intent key (cities, budget band, party, answer shape) plus an embedding of the question. A close match is replayed as a stream for $0 without calling Claude, and the `done` event says "answer cache".

### 5. Routing

- **Task:** each turn is classified as `recommend`, `itinerary`, `events`, `refine` or `clarify`, with a confidence score. This one is `itinerary`, confidence 0.9.
- **Model tier:** Haiku for `clarify` (no covered destination yet), Sonnet by default, Opus for 2+ cities, plans of 5+ days, 3+ hard constraints, or a rejected answer. In `degraded` mode Opus is never used.
- **Circuit breaker:** after repeated failures of a model, traffic moves to its fallback: Opus → Sonnet 5.5 → Sonnet 5, and Haiku → Sonnet 5.5.

### 6. Prompt

The system prompt holds the rules: use tools before recommending, never write links or prices from memory, treat tool data as untrusted. The turn prompt adds today's date, the trip profile, the task guidance, the last 6 messages and the question.

### 7. Agent loop

The loop runs up to 4 model calls for Haiku, 8 for Sonnet and 14 for Opus.

- Claude's text streams to the browser as `text` events.
- When Claude asks for tools, the look-up tools run at the same time, then the UI tools run in the order Claude chose.
- Every tool call goes through the same pipeline:
  1. The tool name must be known.
  2. Arguments are validated with Pydantic.
  3. A turn may make at most 24 tool calls.
  4. The same call repeated more than twice is blocked.
  5. Each tool has its own timeout.
  6. Only timeouts, 429s and 5xx errors are retried, with randomized backoff.
  7. Errors go back to Claude as data: `{error, error_type, retryable}`.
- **Itinerary check:** `show_itinerary` verifies known activities, matching city, budget, duplicates, 08:00–23:30 hours and travel gaps between stops. If the plan fails, Claude gets the list of issues to fix, for up to 3 attempts. After that it must show a list instead.
- The loop also stops early if the turn passes $0.50 or the 90-second timeout.

In a real run of this example, the loop made 4 model calls:

| Model call | What Claude did |
| --- | --- |
| 1 | `search_activities` and `get_tour_offers`, run together |
| 2 | Three `travel_time` checks, run together |
| 3 | `show_itinerary`, which passed on the first attempt |
| 4 | The final explanation text |

The turn used about 13k input tokens and cost about $0.06.

### 8. Retrieval

Behind `search_activities`:

1. Hard filters: city, category, maximum price.
2. Two rankings, vector similarity and keyword matching, merged with reciprocal-rank fusion.
3. A boost for the party type, for example "romantic" tags for couples or "family" tags for families.

### 9. After the answer

- **Grounding check:** flags any link in the answer text, or any price not on a returned card.
- **Spend:** the turn's estimated cost is added to the session total. A successful call also resets the circuit breaker.
- **Cache:** the answer is stored only if it showed cards, didn't stop early, and passed the grounding check.
- **Session:** the question and answer are saved to history, with the names of what was shown.
- **Trace:** marked finished, then the `done` event is sent with model, task, route reason, cost, turns and `runId`.

### 10. Browser renders

Text first, then cards or the day plan, then a footer such as `itinerary · claude-sonnet-5-5 · standard request · $0.0637`.

## Event stream

The browser receives these server-sent events, in this order:

| Event | When | Payload |
| --- | --- | --- |
| `session` | First | `sessionId` |
| `status` | While tools run | Progress text, e.g. "Searching activities in Lisbon" |
| `text` | While Claude writes | A text delta |
| `cards` | After `show_activities` | Activity cards |
| `itinerary` | After a plan passes the check | Days, stops and their activities |
| `done` | Last, on success | `model`, `routeReason`, `cached`, `costUsd`, `turns`, `stoppedEarly`, `runId`, `task`, `confidence` |
| `error` | Instead of `done`, on failure | A user-facing message |

## Failure paths

| What fails | What happens |
| --- | --- |
| Model overloaded or 5xx, before any text has streamed | Breaker records the failure, then the turn retries once on the fallback model |
| Model returns a 4xx | No retry. The user sees "Genie hit a problem", and the trace saves the error |
| A tool provider is down | Retried with backoff, then Claude gets `upstream_error` and continues with the other tools |
| The plan keeps failing the checks | After 3 attempts Claude is told to show a list instead |
| User clicks Stop | The abort travels browser → Next.js → API and cancels the loop. The partial answer is saved and the trace is marked `aborted` |
| Over budget or too many steps | The answer ends with a "stopped early" note and isn't cached |
| Kill switch `degraded` | Opus routing and live provider calls are off; index and cached answers still work |
| Kill switch `off` | Every turn gets a resting message |

## Agentic patterns used

| Pattern | How Genie uses it |
| --- | --- |
| Routing | Rule-based task classification with confidence, a cheap model tier for clarifying turns, and per-task prompt guidance |
| Tool use | The model proposes calls; the runtime validates, budgets, times out, retries and returns structured errors |
| Parallelization | Look-up tool calls in one model turn run concurrently behind a concurrency limit |
| Reflection | Itinerary plans are checked by objective rules and revised at most twice; answers get a grounding check |
| Fallbacks | Circuit breaker per model, fallback model chain, answer cache, degraded mode |
| Bounded execution | Step limit per model, 24 tool calls per turn, $0.50 per turn, $3 per session, 90-second turn timeout |
| Durable state and observability | A run trace per turn with each step's latency, tokens, cost, retries and checks |
| Evaluation | A labeled routing set with accuracy and a confusion matrix, run in CI |

Booking-side patterns (idempotency keys, saga compensation, approval before payment) aren't used, because Genie only links out to booking pages. They become necessary if Genie ever books or takes payment.

## Inspecting a run

Take the `runId` from a `done` event and call:

```bash
curl localhost:8787/api/runs/<runId> -H "Authorization: Bearer $ADMIN_TOKEN"
```

The response shows the route, every model and tool step with latency, tokens and cost, the itinerary and grounding checks, totals and the final status.
