from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Optional
from pgvector import Vector
from .models import Session, SessionMessage, TripProfile


class MemoryKV:
    def __init__(self):
        self.data: dict[str, tuple[str, Optional[float]]] = {}
        self.lock = asyncio.Lock()

    async def get(self, key: str):
        entry = self.data.get(key)
        if entry is None:
            return None
        value, expires = entry
        if expires is not None and expires <= time.time():
            self.data.pop(key, None)
            return None
        return value

    async def set(self, key: str, value: str, ttl_sec: Optional[int] = None):
        self.data[key] = (value, time.time() + ttl_sec if ttl_sec else None)

    async def incrby(self, key: str, amount: float, ttl_sec: int):
        async with self.lock:
            current = await self.get(key)
            old = self.data.get(key)
            expiry = old[1] if old else time.time() + ttl_sec
            value = float(current or 0) + amount
            self.data[key] = (str(value), expiry)
            return value

    async def close(self):
        return None


class RedisKV:
    def __init__(self, client):
        self.client = client

    async def get(self, key: str):
        return await self.client.get(key)

    async def set(self, key: str, value: str, ttl_sec: Optional[int] = None):
        await self.client.set(key, value, ex=ttl_sec)

    async def incrby(self, key: str, amount: float, ttl_sec: int):
        value = await self.client.incrbyfloat(key, amount)
        await self.client.expire(key, ttl_sec, nx=True)
        return value

    async def close(self):
        await self.client.aclose()


class SessionStore:
    TTL_SEC = 7 * 24 * 3600
    MAX_HISTORY = 20

    def __init__(self, kv):
        self.kv = kv

    async def load(self, session_id: str) -> Session:
        raw = await self.kv.get(f"genie:session:{session_id}")
        if raw:
            return Session.model_validate_json(raw)
        return Session(id=session_id, profile=TripProfile())

    async def save(self, session: Session):
        session.history = [SessionMessage.model_validate(item) for item in session.history[-self.MAX_HISTORY:]]
        await self.kv.set(f"genie:session:{session.id}", session.model_dump_json(), self.TTL_SEC)


class MemoryAnswerCache:
    def __init__(self):
        self.entries = []

    async def lookup(self, key, embedding, version, threshold, cosine):
        now = time.time()
        self.entries = [e for e in self.entries if e["expires"] > now]
        candidates = [e for e in self.entries if e["key"] == key and e["version"] == version]
        if not candidates:
            return None
        best = max(candidates, key=lambda e: cosine(embedding, e["embedding"]))
        similarity = cosine(embedding, best["embedding"])
        if similarity < threshold:
            return None
        return {"answer": best["answer"], "similarity": similarity, "question": best["question"]}

    async def store(self, key, question, embedding, answer, version):
        self.entries.append({"key": key, "question": question, "embedding": embedding, "answer": answer,
                             "version": version, "expires": time.time() + 86400})


class PgAnswerCache:
    def __init__(self, pool):
        self.pool = pool

    async def lookup(self, key, embedding, version, threshold, cosine):
        vector = Vector(embedding)
        row = await self.pool.fetchrow("""SELECT id, question, answer, 1-(embedding <=> $3::vector) AS sim
          FROM answer_cache WHERE intent_key=$1 AND catalog_version=$2 AND expires_at>now()
          ORDER BY embedding <=> $3::vector LIMIT 1""", key, version, vector)
        if not row or row["sim"] < threshold:
            return None
        await self.pool.execute("UPDATE answer_cache SET hits=hits+1 WHERE id=$1", row["id"])
        answer = row["answer"] if isinstance(row["answer"], dict) else json.loads(row["answer"])
        return {"answer": answer, "similarity": float(row["sim"]), "question": row["question"]}

    async def store(self, key, question, embedding, answer, version):
        vector = Vector(embedding)
        await self.pool.execute("""INSERT INTO answer_cache (intent_key,question,embedding,answer,model,catalog_version,expires_at)
          VALUES ($1,$2,$3::vector,$4::jsonb,$5,$6,now()+interval '24 hours')""",
          key, question, vector, json.dumps(answer), answer["model"], version)


async def create_kv():
    url = os.getenv("REDIS_URL")
    if not url:
        return MemoryKV()
    from redis.asyncio import Redis
    client = Redis.from_url(url, decode_responses=True, max_connections=20)
    return RedisKV(client)
