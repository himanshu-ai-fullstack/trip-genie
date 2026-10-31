from __future__ import annotations

import hashlib
import math
import os
import re
import unicodedata
from collections import Counter
from typing import Optional
import httpx
from pgvector import Vector
from .models import Activity, Category, Party

EMBEDDING_DIM = 256
STOPWORDS = set("a an and are as at be best by can do for from good how i in is it me my of on or our some the things to us we what where which with you your".split())


def tokenize(text: str) -> list[str]:
    value = unicodedata.normalize("NFKD", text.lower())
    value = "".join(c for c in value if not unicodedata.combining(c))
    tokens = re.sub(r"[^a-z0-9 ]+", " ", value).split()
    return [t[:-1] if len(t) > 3 and t.endswith("s") and not t.endswith("ss") else t for t in tokens if len(t) > 1 and t not in STOPWORDS]


def normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector)) or 1
    return [x / norm for x in vector]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * (b[i] if i < len(b) else 0) for i, x in enumerate(a))


class Embedder:
    name = "hashed-v1"
    dim = EMBEDDING_DIM

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    @staticmethod
    def _fnv1a(value: str) -> int:
        h = 0x811C9DC5
        for ch in value:
            h ^= ord(ch)
            h = (h * 0x01000193) & 0xFFFFFFFF
        return h

    def _embed_one(self, text: str) -> list[float]:
        tokens = tokenize(text)
        features = tokens + [f"{tokens[i]}_{tokens[i + 1]}" for i in range(len(tokens) - 1)]
        vector = [0.0] * self.dim
        for feature in features:
            hashed = self._fnv1a(feature)
            idx = (hashed >> 1) % self.dim
            vector[idx] += (1 if hashed & 1 else -1) * (0.5 if "_" in feature else 1)
        return normalize(vector)


class VoyageEmbedder(Embedder):
    name = "voyage-3.5-lite"

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def embed(self, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post("https://api.voyageai.com/v1/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"input": texts, "model": "voyage-3.5-lite", "output_dimension": self.dim})
            response.raise_for_status()
            return [normalize(item["embedding"]) for item in response.json()["data"]]


def create_embedder() -> Embedder:
    return VoyageEmbedder(os.environ["VOYAGE_API_KEY"]) if os.getenv("VOYAGE_API_KEY") else Embedder()


def activity_text(activity: Activity) -> str:
    return f"{activity.name}. {activity.category}. {' '.join(activity.tags)}. {activity.description}"


def boost(activity: Activity, party: Optional[Party]) -> float:
    value = (activity.rating - 4) * 0.004
    if party == "family" and "family" in activity.tags:
        value += 0.006
    if party == "couple" and "romantic" in activity.tags:
        value += 0.004
    if party == "family" and "adults" in activity.tags:
        value -= 0.02
    return value


def rrf(lists: list[list[str]], k: int = 60) -> dict[str, float]:
    scores: dict[str, float] = {}
    for items in lists:
        for rank, item in enumerate(items):
            scores[item] = scores.get(item, 0) + 1 / (k + rank + 1)
    return scores


class MemoryActivityIndex:
    def __init__(self, activities: list[Activity], embedder: Embedder, version: str = "seed-v1"):
        self.activities = activities
        self.embedder = embedder
        self.catalog_version = version
        self.vectors: dict[str, list[float]] = {}
        self.tokens: dict[str, set[str]] = {}
        self.doc_freq: Counter[str] = Counter()

    @classmethod
    async def create(cls, activities: list[Activity], embedder: Embedder, version: str = "seed-v1"):
        index = cls(activities, embedder, version)
        vectors = await embedder.embed([activity_text(a) for a in activities])
        for activity, vector in zip(activities, vectors):
            index.vectors[activity.id] = vector
            tokens = set(tokenize(activity_text(activity)))
            index.tokens[activity.id] = tokens
            index.doc_freq.update(tokens)
        return index

    async def search(self, city: str, text: str, categories: Optional[list[Category]] = None,
                     max_price_usd: Optional[float] = None, party: Optional[Party] = None, limit: int = 10):
        candidates = [a for a in self.activities if a.city.lower() == city.lower()
                      and (not categories or a.category in categories)
                      and (max_price_usd is None or a.price_from_usd <= max_price_usd)]
        if not candidates:
            return []
        query_vector = (await self.embedder.embed([text]))[0]
        vector_rank = [a.id for a in sorted(candidates, key=lambda a: cosine(query_vector, self.vectors.get(a.id, [])), reverse=True)]
        query_tokens = tokenize(text)
        def keyword_score(activity: Activity) -> float:
            return sum(math.log(1 + len(self.activities) / max(self.doc_freq[t], 1)) for t in query_tokens if t in self.tokens.get(activity.id, set()))
        keyword_rank = [a.id for a in sorted((a for a in candidates if keyword_score(a) > 0), key=keyword_score, reverse=True)]
        scores = rrf([vector_rank, keyword_rank])
        ranked = sorted(candidates, key=lambda a: scores.get(a.id, 0) + boost(a, party), reverse=True)
        return [{"activity": a, "score": scores.get(a.id, 0) + boost(a, party)} for a in ranked[:limit]]

    async def get(self, ids: list[str]) -> list[Activity]:
        wanted = set(ids)
        return [a for a in self.activities if a.id in wanted]

    async def cities(self) -> list[str]:
        return list(dict.fromkeys(a.city for a in self.activities))

    async def version(self) -> str:
        return self.catalog_version


class PgActivityIndex:
    def __init__(self, pool, embedder: Embedder):
        self.pool, self.embedder = pool, embedder

    async def search(self, city: str, text: str, categories=None, max_price_usd=None, party=None, limit: int = 10):
        query_vector = await self.embedder.embed([text])
        terms = tokenize(text)
        tsquery = " | ".join(re.sub(r"[^a-zA-Z0-9_]", "", term) for term in terms)
        rows = await self.pool.fetch("""
            WITH c AS (
              SELECT * FROM activities WHERE lower(city)=lower($1)
                AND ($2::text[] IS NULL OR category=ANY($2))
                AND ($3::numeric IS NULL OR price_from_usd <= $3)
            ), v AS (
              SELECT id, row_number() OVER (ORDER BY embedding <=> $4::vector) AS r
              FROM c ORDER BY embedding <=> $4::vector LIMIT 50
            ), k AS (
              SELECT id, row_number() OVER (ORDER BY ts_rank_cd(tsv, q) DESC) AS r
              FROM c, to_tsquery('english', $5) q WHERE $5 <> '' AND tsv @@ q LIMIT 50
            )
            SELECT c.*, COALESCE(1.0/(60+v.r),0)+COALESCE(1.0/(60+k.r),0) AS score
            FROM c LEFT JOIN v USING(id) LEFT JOIN k USING(id)
            ORDER BY score DESC LIMIT 30
        """, city, categories or None, max_price_usd, Vector(query_vector[0]), tsquery)
        results = []
        for row in rows:
            activity = _row_activity(row)
            results.append({"activity": activity, "score": float(row["score"] or 0) + boost(activity, party)})
        results.sort(key=lambda result: result["score"], reverse=True)
        return results[:limit]

    async def get(self, ids: list[str]) -> list[Activity]:
        rows = await self.pool.fetch("SELECT * FROM activities WHERE id=ANY($1)", ids)
        return [_row_activity(row) for row in rows]

    async def cities(self) -> list[str]:
        return [row["city"] for row in await self.pool.fetch("SELECT DISTINCT city FROM activities ORDER BY city")]

    async def version(self) -> str:
        row = await self.pool.fetchrow("SELECT value FROM catalog_meta WHERE key='catalog_version'")
        return row["value"] if row else "unknown"


def _row_activity(row) -> Activity:
    return Activity(id=row["id"], name=row["name"], city=row["city"], country=row["country"], lat=row["lat"], lng=row["lng"],
        category=row["category"], tags=list(row["tags"]), priceFromUsd=float(row["price_from_usd"]), priceBand=row["price_band"],
        durationHours=float(row["duration_hours"]), rating=float(row["rating"]), description=row["description"],
        provider=row["provider"], bookingUrl=row["booking_url"])
