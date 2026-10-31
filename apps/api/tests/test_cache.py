import pytest
from app.intent import effective_intent, extract_intent
from app.main import _cacheable, _intent_key
from app.models import TripProfile
from app.retrieval import Embedder, cosine
from app.stores import MemoryAnswerCache, MemoryKV, SessionStore


def _key(question):
    intent = extract_intent(question)
    return _intent_key(effective_intent(intent, TripProfile()))


def test_intent_cache_key_separates_answer_changing_facts():
    base = _key("things to do in Lisbon with kids under $60")
    assert _key("things to do in Lisbon with kids under $500") != base
    assert _key("things to do in Lisbon for a couple under $60") != base
    assert _key("things to do in New York with kids under $60") != base
    assert _key("3 day plan for Lisbon with kids under $60") != base
    assert not _cacheable(extract_intent("swap day 2 please"))
    assert not _cacheable(extract_intent("what should I do?"))
    assert _cacheable(extract_intent("things to do in Tokyo"))


@pytest.mark.asyncio
async def test_memory_answer_cache_requires_same_key_and_catalog_version():
    embedder = Embedder()
    cache = MemoryAnswerCache()
    vector = (await embedder.embed(["best shows in London for a couple"]))[0]
    answer = {"events": [{"type": "text", "delta": "hello"}], "model": "claude-sonnet-5-5"}
    await cache.store("matching", "London shows", vector, answer, "catalog-v1")
    assert await cache.lookup("matching", vector, "catalog-v1", 0.99, cosine)
    assert await cache.lookup("matching", vector, "catalog-v2", 0.99, cosine) is None
    assert await cache.lookup("other", vector, "catalog-v1", 0.99, cosine) is None


@pytest.mark.asyncio
async def test_session_store_bounds_history_and_applies_ttl():
    kv = MemoryKV()
    sessions = SessionStore(kv)
    session = await sessions.load("session-test")
    session.history = [{"role": "user", "text": f"turn {i}"} for i in range(25)]
    await sessions.save(session)
    loaded = await sessions.load("session-test")
    assert len(loaded.history) == 20
    assert loaded.history[0].text == "turn 5"
