import os
import pytest
from app.main import _build_dependencies
from app.retrieval import cosine


@pytest.mark.asyncio
async def test_pgvector_activity_index_and_answer_cache():
    if not os.getenv("DATABASE_URL"):
        pytest.skip("DATABASE_URL not configured")
    deps = await _build_dependencies()
    try:
        results = await deps["index"].search("Lisbon", "fado music dinner", limit=1)
        assert results[0]["activity"].name == "Fado dinner show"
        vector = (await deps["embedder"].embed(["Lisbon show"]))[0]
        answer = {"events": [{"type": "text", "delta": "test"}], "model": "claude-sonnet-5-5"}
        await deps["cache"].store("pytest-pgvector", "Lisbon show", vector, answer, await deps["index"].version())
        hit = await deps["cache"].lookup("pytest-pgvector", vector, await deps["index"].version(), 0.99, cosine)
        assert hit and hit["answer"]["model"] == "claude-sonnet-5-5"
    finally:
        await deps["kv"].close()
        await deps["pool"].close()
