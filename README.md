# Trip Genie

A streaming chat that finds things to do at a travel destination (sightseeing, adventure, shows, food, culture) and links out to book them. The agent runs on the [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk) (formerly the Claude Code SDK).

Architecture doc: [AI Genie architecture](https://claude.ai/artifact/5MoiEthDJ3TVY7tcoZLLJF)

## Quick start

```bash
pnpm install
cp .env.example .env            # add ANTHROPIC_API_KEY for the real agent

# Terminal 1: API on :8787 (in-memory index and cache when DATABASE_URL is empty)
pnpm --filter @trip-genie/api dev

# Terminal 2: web chat on :3000
pnpm --filter @trip-genie/web dev
```

No API key? Set `GENIE_OFFLINE=1` (or leave the key empty). A scripted runner then calls the same tools as the agent, so the UI, streaming, cache and evals all work.

With Docker (Postgres + pgvector, Redis, API, web):

```bash
docker compose up --build
```

The API creates the schema and loads the seed catalog on first boot.

## How a turn works

```
browser ──SSE── web (Next.js proxy) ── API (Fastify)
                                        │ 1. kill switch, rate limit, session budget
                                        │ 2. intent extraction → trip profile
                                        │ 3. semantic answer cache  ──hit──▶ replay as a stream
                                        │ 4. router (Sonnet / Opus) + circuit breaker
                                        │ 5. Agent SDK query() with Genie's MCP tools
                                        ▼
                     activity index (pgvector + full text, RRF) · live providers · tool cache
```

| Requirement | Where |
| --- | --- |
| Stream the response | `apps/api/src/server.ts` (SSE), `includePartialMessages` in `agent/runner.ts`, `apps/web/app/sse.ts` |
| Cache repeated questions | `cache/answerCache.ts` (intent key + embedding similarity, 24 h, retired on catalog refresh), `cache/toolCache.ts` (15 min), stable system prompt for prompt caching |
| Route to a bigger model | `agent/router.ts`: Opus 5.5 for 2+ cities, plans of 5+ days, 3+ hard constraints, or a rejected answer |
| Better retrieval + evals | `retrieval/` (hard filters, vector + keyword ranking fused with RRF), `evals/` |
| Step limit + kill switch | `maxTurns` / `maxBudgetUsd` per turn, session spend cap, `safety/killSwitch.ts` (`on` / `degraded` / `off`), Stop button aborts the run |
| Fallback model | `fallbackModel` in the SDK call plus `safety/circuitBreaker.ts` (Opus → Sonnet 5.5 → Sonnet 5) |

### Agent tools

The SDK's built-in file and shell tools are switched off (`tools: []`, `permissionMode: "dontAsk"`, no settings from disk). Genie gets one in-process MCP server:

- `search_activities`: hybrid search over the activity index
- `find_events`: dated shows (Ticketmaster when `TICKETMASTER_API_KEY` is set, mock otherwise)
- `get_tour_offers`: bookable tours (mock until GetYourGuide or Viator keys exist)
- `travel_time`: minutes between two activities
- `show_activities`: sends ranked cards to the UI
- `show_itinerary`: sends a day plan, after a feasibility check on times and travel

Cards and plans only accept ids that a tool returned in the same turn, so the model can't invent a place or a booking link.

### Kill switch

```bash
curl -X POST localhost:8787/api/admin/kill-switch \
  -H "authorization: Bearer $ADMIN_TOKEN" -H 'content-type: application/json' \
  -d '{"mode":"degraded"}'   # on | degraded | off
```

`degraded` keeps cached and index answers but turns off Opus routing and live provider calls. `off` returns a resting message and aborts running turns. The flag lives in Redis, so a flip applies on the next request without a deploy.

## Tests and evals

```bash
pnpm --filter @trip-genie/api test     # unit + turn tests; Postgres tests run when DATABASE_URL is set
pnpm --filter @trip-genie/api evals    # retrieval, router, cache and answer checks with floors
pnpm --filter @trip-genie/api evals -- --live   # answer checks against the real agent (needs a key)
```

| Suite | Measures | Floor |
| --- | --- | --- |
| Retrieval | hit@3 and MRR on 40 labelled queries | 0.9 / 0.75 |
| Router | accuracy on 16 labelled questions | 1.0 |
| Cache | accuracy on 16 same/different pairs, false hits | 0.9 / 0 |
| Answers | right city, within budget, no invented links, plan when asked | all pass |

CI runs typecheck, tests (with a pgvector service), the offline evals and the web build.

## Not done yet

- Real GetYourGuide/Viator and Google Places/Routes adapters (mocks today). Ticketmaster is wired.
- Intent extraction is rule-based; a Haiku extractor can replace it behind the same function.
- The local hashed embedding misses loose paraphrases (the cache eval shows one). Set `VOYAGE_API_KEY` for real embeddings and retune `GENIE_CACHE_THRESHOLD`.
- Haiku reranking, an LLM judge for answer quality, OpenTelemetry tracing.
- The real Agent SDK path has not been exercised against the API in this repo's tests; the offline runner covers the pipeline around it.
