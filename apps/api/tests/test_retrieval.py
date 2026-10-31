import pytest
from app.catalog import ACTIVITIES
from app.retrieval import Embedder, MemoryActivityIndex, rrf


def test_rrf_rewards_items_ranked_well_across_lists():
    scores = rrf([["a", "b", "c"], ["b", "a"]])
    assert scores["a"] == pytest.approx(scores["b"])
    assert scores["c"] < scores["a"]


@pytest.mark.asyncio
async def test_index_filters_and_ranks_matches():
    index = await MemoryActivityIndex.create(ACTIVITIES, Embedder())
    filtered = await index.search("dubai", "thrill", max_price_usd=100)
    assert filtered and all(r["activity"].city == "Dubai" and r["activity"].price_from_usd <= 100 for r in filtered)
    assert "Skydive over the Palm" not in [r["activity"].name for r in filtered]
    top = (await index.search("Lisbon", "fado music dinner", limit=1))[0]
    assert top["activity"].name == "Fado dinner show"


@pytest.mark.asyncio
async def test_all_labeled_retrieval_queries_hit_relevant_item_in_top_three():
    index = await MemoryActivityIndex.create(ACTIVITIES, Embedder())
    cases = [
        {"city": "Paris", "query": "romantic evening on the river", "relevant": ["paris-seine-river-evening-cruise"]},
        {"city": "London", "query": "free museum for a rainy day", "relevant": ["london-british-museum-highlights"]},
        {"city": "New York", "query": "Broadway musical", "relevant": ["new-york-broadway-show"]},
        {"city": "Tokyo", "query": "see sumo wrestlers", "relevant": ["tokyo-sumo-morning-practice-visit"]},
        {"city": "Barcelona", "query": "Gaudi basilica", "relevant": ["barcelona-sagrada-familia-with-tower-access"]},
        {"city": "Rome", "query": "ancient gladiator arena", "relevant": ["rome-colosseum-and-roman-forum"]},
        {"city": "Lisbon", "query": "learn to surf", "relevant": ["lisbon-surf-lesson-at-carcavelos"]},
        {"city": "Dubai", "query": "desert adventure with dinner", "relevant": ["dubai-desert-safari-with-dune-bashing"]},
        {"city": "Singapore", "query": "nocturnal animals", "relevant": ["singapore-night-safari"]},
        {"city": "Bali", "query": "volcano sunrise hike", "relevant": ["bali-mount-batur-sunrise-trek"]},
    ]
    ranks = []
    for case in cases:
        results = await index.search(case["city"], case["query"], case.get("categories"), case.get("max_price_usd"), limit=10)
        ids = [row["activity"].id for row in results]
        rank = next((i for i, id_ in enumerate(ids) if id_ in case["relevant"]), None)
        ranks.append(rank)
    hit_at_3 = sum(rank is not None and rank < 3 for rank in ranks) / len(ranks)
    mrr = sum(1 / (rank + 1) if rank is not None else 0 for rank in ranks) / len(ranks)
    assert hit_at_3 >= 0.9
    assert mrr >= 0.75
