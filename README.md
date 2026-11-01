# Trip Genie

A streaming chat that finds things to do at a travel destination (sightseeing, adventure, shows, food, culture) and links out to book them. The web frontend uses Next.js/React; the backend is Python with FastAPI and can use the Anthropic Python SDK or an offline runner.

Architecture doc: [AI Genie architecture](https://claude.ai/artifact/5MoiEthDJ3TVY7tcoZLLJF)

## Quick start

```bash
pnpm install
python3 -m venv apps/api/.venv
source apps/api/.venv/bin/activate
pip install -r apps/api/requirements.txt -r apps/api/requirements-dev.txt
cp .env.example .env            # add ANTHROPIC_API_KEY for the real agent

# Terminal 1: API on :8787 (in-memory index and cache when DATABASE_URL is empty)
cd apps/api && uvicorn app.main:app --reload --port 8787 --env-file ../../.env

# Terminal 2: web chat on :3000
pnpm --filter @trip-genie/web dev
```

No API key? Set `GENIE_OFFLINE=1` (or leave the key empty). A scripted runner then calls the same tools as the agent, so the UI, streaming, cache, and tests work.

With Docker (Postgres + pgvector, Redis, API, web):

```bash
docker compose up --build
```

The API creates the schema and loads the seed catalog on first boot.

## How a turn works

```
browser ──SSE── web (Next.js proxy) ── API (FastAPI)
                                        │ 1. kill switch, rate limit, session budget
                                        │ 2. intent extraction → trip profile
                                        │ 3. semantic answer cache  ──hit──▶ replay as a stream
                                        │ 4. router: task + model tier (Haiku / Sonnet / Opus) + circuit breaker
                                        │ 5. Anthropic tool loop: validated tools, parallel read-only calls
                                        │ 6. checks: itinerary verifier (capped revisions), answer grounding
                                        │ 7. run trace checkpointed after every step
                                        ▼
                     activity index (pgvector + full text, RRF) · live providers · tool cache
```

| Requirement | Where |
| --- | --- |
| Stream the response | `apps/api/app/main.py` (SSE), `apps/api/app/runner.py`, `apps/web/app/sse.ts` |
| Cache repeated questions | `apps/api/app/stores.py` (answer cache, 24 h; tool cache, 15 min) |
| Route to a bigger model | `apps/api/app/policies.py`: planner for 2+ cities, plans of 5+ days, 3+ hard constraints, or rejected answers |
| Better retrieval | `apps/api/app/retrieval.py` (hard filters, vector + keyword ranking fused with RRF) |
| Step limit + kill switch | Turn timeout and step limit, estimated session spend cap, `apps/api/app/policies.py` (`on` / `degraded` / `off`), Stop button aborts the run |
| Fallback model | `apps/api/app/policies.py` (Opus → Sonnet 5.5 → Sonnet 5; Haiku → Sonnet 5.5) |

### Agentic patterns

| Pattern | How Genie uses it | Where |
| --- | --- | --- |
| Routing | Rules classify each turn as `recommend`, `itinerary`, `events`, `refine` or `clarify`, returning a validated `{task, model, confidence, reason}`. Each task gets its own prompt guidance; `clarify` turns go to the cheap Haiku tier. | `policies.py` (`classify_task`, `route_model`), `prompt.py` (`TASK_GUIDANCE`) |
| Tool use | The model proposes, the runtime disposes: unknown tools are rejected, arguments are validated with Pydantic, errors come back as `{error, error_type, retryable}`, and each tool has its own timeout and retry budget. A per-turn call cap and repeated-identical-call detection stop loops. | `tools.py` (`TOOL_SPECS`, `GenieTools.call`) |
| Retries | Only timeouts, 429 and 5xx are retried, with exponential backoff and full jitter. 4xx and validation errors are not. | `policies.py` (`transient_model_error`, `backoff_delay`) |
| Parallelization | Read-only tool calls from one model turn run concurrently behind a bulkhead semaphore; card and plan calls run afterwards in the model's order. The offline runner searches each city concurrently. | `runner.py` (`run_tool_calls`) |
| Reflection | `show_itinerary` is an evaluator-optimizer loop with an objective checker (known ids, city, budget, duplicates, opening window, travel gaps). The model gets structured issues to fix, for at most 3 attempts, then must fall back to a list. After the answer, a grounding check flags links or prices that no tool returned, and ungrounded answers are not cached. | `tools.py` (`check_itinerary`, `grounding_issues`) |
| Durable state + observability | Every turn has a run trace (route, each model and tool step with latency, tokens, cost, retries, checks, outcome) saved to Redis after every step. | `trace.py`, `GET /api/runs/{runId}` (admin token) |
| Evaluation | A labeled routing set reports task and model accuracy plus a confusion matrix, and runs in CI as a test. | `evals/routing_cases.jsonl`, `python -m evals.run_routing` |

Genie only links out to booking pages, so the booking-side patterns (idempotency keys, saga compensation, approval before payment) are not needed yet. They become necessary if Genie ever books or pays.

### Agent tools

The Python agent exposes only these explicitly defined tools; it does not provide general-purpose filesystem or shell access:

- `search_activities`: hybrid search over the activity index
- `find_events`: dated shows (Ticketmaster when `TICKETMASTER_API_KEY` is set, mock otherwise)
- `get_tour_offers`: bookable tours (mock until GetYourGuide or Viator keys exist)
- `travel_time`: minutes between two activities
- `show_activities`: sends ranked cards to the UI
- `show_itinerary`: sends a day plan, after a feasibility check on times, travel, budget and city

Cards and plans only accept ids that a tool returned in the same turn, so the model can't invent a place or a booking link.

### Kill switch

```bash
curl -X POST localhost:8787/api/admin/kill-switch \
  -H "authorization: Bearer $ADMIN_TOKEN" -H 'content-type: application/json' \
  -d '{"mode":"degraded"}'   # on | degraded | off
```

`degraded` keeps cached and index answers but turns off Opus routing and live provider calls. `off` returns a resting message and aborts running turns. The flag lives in Redis, so a flip applies on the next request without a deploy.

## Tests and retrieval checks

```bash
cd apps/api && python -m pytest         # Python API unit and integration tests
cd apps/api && python -m evals.run_routing   # routing accuracy and confusion matrix
```

| Suite | Measures |
| --- | --- |
| Retrieval | Labeled top-three relevance checks and reciprocal-rank fusion |
| API | Health endpoint, request validation, SSE events, and kill-switch authorization |
| Agent logic | Intent parsing, trip profile, model routing, safety limits, and tool validation |
| Tool runtime | Argument validation, structured errors, retry vs no-retry, loop detection, revision cap, parallel ordering |
| Routing eval | Every labeled case in `evals/routing_cases.jsonl` routes to the expected task and model |
| Tracing | Each turn writes a run trace readable only with the admin token |

CI runs the Python API tests (with a pgvector service) and builds the web frontend.

Session spend is estimated from Claude token usage. The default per-million-token rates are configurable in `.env`; verify them against current Anthropic pricing before relying on the cap for billing control.

## Not done yet

- Real GetYourGuide/Viator and Google Places/Routes adapters (mocks today). Ticketmaster is wired.
- Intent extraction is rule-based; a Haiku extractor can replace it behind the same function.
- The local hashed embedding misses loose paraphrases (the cache eval shows one). Set `VOYAGE_API_KEY` for real embeddings and retune `GENIE_CACHE_THRESHOLD`.
- Haiku reranking, an LLM judge for answer quality, OpenTelemetry tracing.
- Live Anthropic calls require an API key and are not exercised by the default offline tests.
