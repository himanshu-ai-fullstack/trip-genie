import pytest
from app.catalog import ACTIVITIES
from app.providers import HaversineRoutes, MockEvents, MockTours
from app.retrieval import Embedder, MemoryActivityIndex
from app.stores import MemoryKV
from app.tools import GenieTools


@pytest.mark.asyncio
async def test_itinerary_validates_activity_ids_times_and_travel_gaps():
    index = await MemoryActivityIndex.create(ACTIVITIES, Embedder())
    events = []
    async def emit(event):
        events.append(event)
    tools = GenieTools(index, type("Providers", (), {"events": MockEvents(), "tours": MockTours(), "routes": HaversineRoutes()})(), MemoryKV(), emit, None)
    results = await tools.call("search_activities", {"city": "Rome", "query": "history"})
    ids = [row["id"] for row in results["results"]]
    result = await tools.call("show_itinerary", {"days": [{"day": 1, "city": "Rome", "stops": [
        {"activity_id": ids[0], "start": "09:00"}, {"activity_id": ids[1], "start": "09:30"}]}]})
    assert "error" in result
    bad_id = await tools.call("show_activities", {"activity_ids": ["not-from-search"]})
    assert "error" in bad_id


@pytest.mark.asyncio
async def test_provider_stubs_return_activities():
    events = await MockEvents().find_events("London", "2026-11-01", "2026-11-03")
    assert events and all(event.date for event in events)
    offers = await MockTours().find_tours("Lisbon", "surf", 50)
    assert offers and all(a.price_from_usd <= 50 for a in offers)
