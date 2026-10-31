from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import uuid
from contextlib import asynccontextmanager
from datetime import date
from typing import Optional
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from .catalog import ACTIVITIES
from .intent import effective_intent, extract_intent, merge_profile
from .models import ChatRequest, KillSwitchRequest, SessionMessage
from .policies import CircuitBreaker, LIMITS, KillSwitch, check_rate_limits, check_session_budget, record_spend, route_model, transient_model_error
from .prompt import build_turn_prompt
from .providers import Providers
from .retrieval import MemoryActivityIndex, PgActivityIndex, create_embedder, cosine
from .runner import create_runner
from .stores import MemoryAnswerCache, SessionStore, create_kv, PgAnswerCache
from .tools import GenieTools

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("trip_genie")
RESTING_MESSAGE = KillSwitch.RESTING_MESSAGE


def _intent_key(intent) -> str:
    budget = intent.budget_usd
    band = "any" if budget is None else "low" if budget <= 30 else "mid" if budget <= 100 else "high" if budget <= 250 else "lux"
    payload = {"cities": sorted(intent.cities), "categories": sorted(intent.categories), "budget": band,
        "month": intent.month or "any", "party": intent.party or "any",
        "constraints": sorted(c for c in intent.constraints if c not in ("budget", "kids")),
        "shape": f"plan-{intent.days if intent.days is not None else '?'}" if intent.wants_itinerary else "list"}
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()[:32]


def _cacheable(intent):
    return not intent.follow_up and bool(intent.cities)


def _similarity_threshold(embedder):
    configured = os.getenv("GENIE_CACHE_THRESHOLD")
    return float(configured) if configured else (0.3 if embedder.name.startswith("hashed") else 0.85)


async def _build_dependencies():
    embedder = create_embedder()
    kv = await create_kv()
    pool = None
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        import asyncpg
        from pgvector.asyncpg import register_vector
        async def init_connection(connection):
            await connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
            await register_vector(connection)
        pool = await asyncpg.create_pool(db_url.replace("postgres://", "postgresql://", 1), min_size=1, max_size=10, init=init_connection)
        async with pool.acquire() as conn:
            for filename in sorted(os.listdir(os.path.join(os.path.dirname(__file__), "..", "db"))):
                if filename.endswith(".sql"):
                    with open(os.path.join(os.path.dirname(__file__), "..", "db", filename), encoding="utf-8") as sql_file:
                        await conn.execute(sql_file.read())
            count = await conn.fetchval("SELECT count(*) FROM activities")
        if count == 0:
            version = f"{embedder.name}-{date.today().isoformat()}"
            vectors = await embedder.embed([f"{a.name}. {a.category}. {' '.join(a.tags)}. {a.description}" for a in ACTIVITIES])
            async with pool.acquire() as conn:
                async with conn.transaction():
                    for activity, vector in zip(ACTIVITIES, vectors):
                        from pgvector import Vector
                        vector_literal = Vector(vector)
                        await conn.execute("""INSERT INTO activities (id,name,city,country,lat,lng,category,tags,price_from_usd,price_band,duration_hours,rating,description,provider,booking_url,search_text,embedding,refreshed_at)
                          VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17::vector,now())
                          ON CONFLICT(id) DO NOTHING""", activity.id, activity.name, activity.city, activity.country, activity.lat, activity.lng,
                          activity.category, activity.tags, activity.price_from_usd, activity.price_band, activity.duration_hours,
                          activity.rating, activity.description, activity.provider, activity.booking_url,
                          f"{activity.name}. {activity.category}. {' '.join(activity.tags)}. {activity.description}", vector_literal)
                    await conn.execute("""INSERT INTO catalog_meta(key,value) VALUES('catalog_version',$1)
                      ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value""", version)
        index, cache = PgActivityIndex(pool, embedder), PgAnswerCache(pool)
    else:
        index = await MemoryActivityIndex.create(ACTIVITIES, embedder)
        cache = MemoryAnswerCache()
    return {"embedder": embedder, "kv": kv, "pool": pool, "index": index, "cache": cache,
            "providers": Providers(), "sessions": SessionStore(kv), "kill_switch": KillSwitch(kv),
            "breaker": CircuitBreaker(), "runner": create_runner()}


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.deps = await _build_dependencies()
    logger.info("Genie API ready (%s index)", "postgres" if app.state.deps["pool"] else "in-memory")
    yield
    await app.state.deps["kv"].close()
    if app.state.deps["pool"]:
        await app.state.deps["pool"].close()


app = FastAPI(title="Trip Genie API", version="0.1.0", lifespan=lifespan)


@app.get("/api/health")
async def health(request: Request):
    deps = request.app.state.deps
    return {"ok": True, "killSwitch": await deps["kill_switch"].mode()}


@app.post("/api/admin/kill-switch")
async def set_kill_switch(body: KillSwitchRequest, request: Request, authorization: Optional[str] = Header(default=None)):
    token = os.getenv("ADMIN_TOKEN")
    if not token or authorization != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="unauthorized")
    await request.app.state.deps["kill_switch"].set(body.mode)
    logger.info("kill_switch mode=%s", body.mode)
    return {"mode": body.mode}


def _sse(event: dict) -> bytes:
    return f"event: {event['type']}\ndata: {json.dumps(event, separators=(',', ':'), ensure_ascii=False)}\n\n".encode()


@app.post("/api/chat")
async def chat(body: ChatRequest, request: Request, x_forwarded_for: Optional[str] = Header(default=None)):
    deps = request.app.state.deps
    session_id = body.sessionId or str(uuid.uuid4())
    message = body.message.strip()[:LIMITS["max_question_chars"]]
    ip = (x_forwarded_for or (request.client.host if request.client else "unknown")).split(",")[0].strip()

    async def events():
        yield _sse({"type": "session", "sessionId": session_id})
        if not message:
            yield _sse({"type": "error", "message": "Ask me about a destination to get started."})
            return
        try:
            async for event in run_turn(deps, session_id, message, ip):
                yield _sse(event)
        except asyncio.CancelledError:
            logger.info("turn aborted session=%s", session_id)
            raise
        except Exception:
            logger.exception("unhandled chat error")
            yield _sse({"type": "error", "message": "Genie hit a problem answering that. Please try again."})

    return StreamingResponse(events(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform", "Connection": "keep-alive", "X-Accel-Buffering": "no",
        "X-Session-Id": session_id,
    })


async def run_turn(deps, session_id: str, message: str, ip: str):
    queue: asyncio.Queue = asyncio.Queue()
    async def emit(event):
        await queue.put(event)

    async def work():
        try:
            async for event in _turn_events(deps, session_id, message, ip, emit):
                await queue.put(event)
        finally:
            await queue.put(None)

    task = asyncio.create_task(work())
    try:
        while True:
            event = await queue.get()
            if event is None:
                break
            yield event
    finally:
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def _turn_events(deps, session_id, message, ip, emit):
    kv, kill_switch = deps["kv"], deps["kill_switch"]
    mode = await kill_switch.mode()
    if mode == "off":
        await emit({"type": "text", "delta": RESTING_MESSAGE})
        yield {"type": "done", "model": "offline", "routeReason": "kill switch off", "cached": False, "costUsd": 0, "turns": 0, "stoppedEarly": None}
        return
    reason = await check_rate_limits(kv, ip, session_id) or await check_session_budget(kv, session_id)
    if reason:
        yield {"type": "error", "message": reason}
        return

    session = await deps["sessions"].load(session_id)
    raw_intent = extract_intent(message)
    session.profile = merge_profile(session.profile, raw_intent)
    intent = effective_intent(raw_intent, session.profile)
    cacheable = _cacheable(intent)
    key = _intent_key(intent)
    catalog_version = await deps["index"].version()
    embedding = (await deps["embedder"].embed([message]))[0] if cacheable else None
    if cacheable and embedding:
        hit = await deps["cache"].lookup(key, embedding, catalog_version, _similarity_threshold(deps["embedder"]), cosine)
        if hit:
            logger.info("cache_hit key=%s similarity=%.3f", key, hit["similarity"])
            for event in hit["answer"]["events"]:
                if event["type"] == "text":
                    chunks = re.findall(r".{1,32}(?:\s|$)", event["delta"]) or [event["delta"]]
                    for chunk in chunks:
                        await emit({"type": "text", "delta": chunk})
                else:
                    await emit(event)
            text = "".join(e["delta"] for e in hit["answer"]["events"] if e["type"] == "text")
            shown = [a for e in hit["answer"]["events"] if e["type"] in ("cards", "itinerary") for a in e["activities"]]
            await _remember(deps, session, message, text, shown, hit["answer"]["model"])
            yield {"type": "done", "model": hit["answer"]["model"], "routeReason": "answer cache", "cached": True, "costUsd": 0, "turns": 0, "stoppedEarly": None}
            return

    route = route_model(intent, bool(session.history and re.search(r"\b(doesn'?t work|does not work|not what i|don'?t like|too expensive|wrong|try again|that'?s not|no good|bad plan)\b", message, re.I)), mode == "degraded")
    resolved = deps["breaker"].resolve(route["model"])
    model = resolved["model"]
    route_reason = route["reason"] + ("; breaker open, using fallback" if resolved["tripped"] else "")
    logger.info("route model=%s reason=%s", model, route_reason)
    recorded = []
    async def tool_emit(event):
        if event["type"] in ("cards", "itinerary"):
            recorded.append(event)
        await emit(event)
    tools = GenieTools(deps["index"], deps["providers"], kv, tool_emit, intent.party, mode == "degraded")
    streamed = ""
    async def emit_text(delta):
        nonlocal streamed
        streamed += delta
        await emit({"type": "text", "delta": delta})
    try:
        prompt = build_turn_prompt(session, message, date.today().isoformat())
        async def execute(selected_model):
            return await asyncio.wait_for(
                deps["runner"].run(selected_model, prompt, message, intent, tools, emit_text),
                timeout=LIMITS["turn_timeout_ms"] / 1000,
            )
        try:
            result = await execute(model)
        except Exception as error:
            fallback = resolved["fallback"]
            if not transient_model_error(error) or not fallback or streamed:
                raise
            deps["breaker"].record_failure(model)
            logger.warning("model fallback primary=%s fallback=%s", model, fallback)
            route_reason += "; primary failed, using fallback"
            model = fallback
            result = await execute(model)
        deps["breaker"].record_success(model)
        await record_spend(kv, session_id, result["cost_usd"])
        stopped = result["stopped_early"]
        shown = [a for e in recorded for a in e["activities"]]
        if cacheable and embedding and not stopped and shown:
            await deps["cache"].store(key, message, embedding, {"events": [{"type": "text", "delta": result["text"]}] + recorded, "model": result["answered_by"]}, catalog_version)
        await _remember(deps, session, message, result["text"], shown, result["answered_by"])
        yield {"type": "done", "model": result["answered_by"], "routeReason": route_reason, "cached": False,
               "costUsd": result["cost_usd"], "turns": result["turns"], "stoppedEarly": stopped}
    except (TimeoutError, asyncio.TimeoutError, asyncio.CancelledError):
        await _remember(deps, session, message, streamed, [a for e in recorded for a in e["activities"]], model)
        if isinstance(asyncio.current_task(), asyncio.Task) and asyncio.current_task().cancelling():
            raise
        yield {"type": "done", "model": model, "routeReason": route_reason, "cached": False, "costUsd": 0, "turns": 0, "stoppedEarly": "aborted"}
    except Exception as error:
        if transient_model_error(error):
            deps["breaker"].record_failure(model)
        logger.exception("turn error model=%s", model)
        yield {"type": "error", "message": "Genie hit a problem answering that. Please try again."}


async def _remember(deps, session, message, answer_text, shown, model):
    note = " [showed: " + "; ".join(a.get("name", "") for a in shown) + "]" if shown else ""
    session.history.extend([SessionMessage(role="user", text=message), SessionMessage(role="assistant", text=(answer_text + note).strip())])
    session.last_model = model
    await deps["sessions"].save(session)
