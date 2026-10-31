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
                                        │ 4. router (Sonnet / Opus) + circuit breaker
                                        │ 5. Anthropic tool loop with Genie tools
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
| Fallback model | `apps/api/app/policies.py` (Opus → Sonnet 5.5 → Sonnet 5) |

### Agent tools

The Python agent exposes only these explicitly defined tools; it does not provide general-purpose filesystem or shell access:

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

## Tests and retrieval checks

```bash
cd apps/api && python -m pytest         # Python API unit and integration tests
```

| Suite | Measures | Floor |
| --- | --- | --- |
| Suite | Measures |
| --- | --- |
| Retrieval | Labeled top-three relevance checks and reciprocal-rank fusion |
| API | Health endpoint, request validation, SSE events, and kill-switch authorization |
| Agent logic | Intent parsing, trip profile, model routing, safety limits, and tool validation |

CI runs the Python API tests (with a pgvector service) and builds the web frontend.

Session spend is estimated from Claude token usage. The default per-million-token rates are configurable in `.env`; verify them against current Anthropic pricing before relying on the cap for billing control.

## Not done yet

- Real GetYourGuide/Viator and Google Places/Routes adapters (mocks today). Ticketmaster is wired.
- Intent extraction is rule-based; a Haiku extractor can replace it behind the same function.
- The local hashed embedding misses loose paraphrases (the cache eval shows one). Set `VOYAGE_API_KEY` for real embeddings and retune `GENIE_CACHE_THRESHOLD`.
- Haiku reranking, an LLM judge for answer quality, OpenTelemetry tracing.
- Live Anthropic calls require an API key and are not exercised by the default offline tests.
