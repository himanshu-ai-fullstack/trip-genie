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


def _providers(**overrides):
    base = {"events": MockEvents(), "tours": MockTours(), "routes": HaversineRoutes()}
    return type("Providers", (), {**base, **overrides})()


async def _tools(providers=None, **kwargs):
    index = await MemoryActivityIndex.create(ACTIVITIES, Embedder())
    async def emit(event):
        pass
    return GenieTools(index, providers or _providers(), MemoryKV(), emit, None, **kwargs)


@pytest.mark.asyncio
async def test_tool_calls_are_validated_and_errors_are_structured():
    tools = await _tools()
    bad = await tools.call("search_activities", {"city": "Rome", "query": "art", "limit": 99})
    assert bad["error_type"] == "validation_error" and bad["retryable"] is False and "limit" in bad["error"]
    assert (await tools.call("book_flight", {}))["error_type"] == "unknown_tool"
    window = await tools.call("find_events", {"city": "London", "from": "2026-11-05", "to": "2026-11-01"})
    assert window["error_type"] == "validation_error"
    events = await tools.call("find_events", {"city": "London", "from": "2026-11-01", "to": "2026-11-03"})
    assert events["events"], "event search must reach the provider with start/end"


@pytest.mark.asyncio
async def test_repeated_identical_calls_are_cut_off():
    tools = await _tools()
    args = {"city": "Rome", "query": "history"}
    assert "results" in await tools.call("search_activities", args)
    assert "results" in await tools.call("search_activities", args)
    assert (await tools.call("search_activities", args))["error_type"] == "loop_detected"


@pytest.mark.asyncio
async def test_transient_provider_failures_retry_then_degrade(monkeypatch):
    monkeypatch.setattr("app.tools.backoff_delay", lambda attempt: 0)

    class FlakyTours:
        name = "flaky-tours"
        def __init__(self, failures):
            self.failures, self.calls = failures, 0
        async def find_tours(self, city, query, max_price_usd=None):
            self.calls += 1
            if self.calls <= self.failures:
                raise RuntimeError("503 service unavailable")
            return await MockTours().find_tours(city, query, max_price_usd)

    recovers = FlakyTours(failures=2)
    tools = await _tools(_providers(tours=recovers))
    assert (await tools.call("get_tour_offers", {"city": "Lisbon", "query": "surf"}))["offers"]
    assert recovers.calls == 3

    down = FlakyTours(failures=99)
    tools = await _tools(_providers(tours=down))
    result = await tools.call("get_tour_offers", {"city": "Lisbon", "query": "surf"})
    assert result["error_type"] == "upstream_error" and result["retryable"] and down.calls == 3


@pytest.mark.asyncio
async def test_non_transient_errors_are_not_retried(monkeypatch):
    class BrokenTours:
        name = "broken-tours"
        calls = 0
        async def find_tours(self, city, query, max_price_usd=None):
            BrokenTours.calls += 1
            raise ValueError("400 bad request")
    tools = await _tools(_providers(tours=BrokenTours()))
    result = await tools.call("get_tour_offers", {"city": "Lisbon", "query": "surf"})
    assert result["error_type"] == "tool_error" and not result["retryable"] and BrokenTours.calls == 1


@pytest.mark.asyncio
async def test_itinerary_revision_loop_is_capped():
    tools = await _tools()
    ids = [row["id"] for row in (await tools.call("search_activities", {"city": "Rome", "query": "history"}))["results"]]
    def clash(start):
        return {"days": [{"day": 1, "city": "Rome", "stops": [{"activity_id": ids[0], "start": "09:00"}, {"activity_id": ids[1], "start": start}]}]}
    first = await tools.call("show_itinerary", clash("09:05"))
    assert first["error_type"] == "plan_infeasible" and first["issues"] and first["attempts_left"] == 2
    assert (await tools.call("show_itinerary", clash("09:10")))["attempts_left"] == 1
    assert (await tools.call("show_itinerary", clash("09:15")))["error_type"] == "revision_limit"
    assert (await tools.call("show_itinerary", clash("09:20")))["error_type"] == "revision_limit"


@pytest.mark.asyncio
async def test_itinerary_checker_flags_budget_city_and_duplicates():
    tools = await _tools(budget_usd=5)
    rome = (await tools.call("search_activities", {"city": "Rome", "query": "food tour"}))["results"]
    paris = (await tools.call("search_activities", {"city": "Paris", "query": "museum"}))["results"]
    pricey = next(row for row in rome if row["price_from_usd"] > 5)
    plan = {"days": [{"day": 1, "city": "Rome", "stops": [{"activity_id": pricey["id"], "start": "09:00"}, {"activity_id": paris[0]["id"], "start": "18:00"}]},
                     {"day": 2, "city": "Rome", "stops": [{"activity_id": pricey["id"], "start": "09:00"}]}]}
    issues = " ".join((await tools.call("show_itinerary", plan))["issues"])
    assert "over the $5 budget" in issues and "not Rome" in issues and "appears 2 times" in issues


@pytest.mark.asyncio
async def test_grounding_check_flags_links_and_unknown_prices():
    tools = await _tools(budget_usd=60)
    row = (await tools.call("search_activities", {"city": "Lisbon", "query": "surf"}))["results"][0]
    assert tools.grounding_issues(f"Try {row['name']} from ${row['price_from_usd']:g}, all under $60.") == []
    issues = tools.grounding_issues("Book at https://example.com for $999.")
    assert len(issues) == 2


@pytest.mark.asyncio
async def test_read_only_tool_calls_run_in_parallel_and_keep_order():
    import asyncio
    from app.runner import run_tool_calls

    class SlowRoutes(HaversineRoutes):
        active = peak = 0
        async def travel_minutes(self, source, target, mode="transit"):
            SlowRoutes.active += 1
            SlowRoutes.peak = max(SlowRoutes.peak, SlowRoutes.active)
            await asyncio.sleep(0.05)
            SlowRoutes.active -= 1
            return 10

    tools = await _tools(_providers(routes=SlowRoutes()))
    ids = [row["id"] for row in (await tools.call("search_activities", {"city": "Rome", "query": "history"}))["results"]]
    Call = type("Call", (), {})
    def call(i, name, payload):
        c = Call(); c.id, c.name, c.input = f"t{i}", name, payload
        return c
    calls = [call(0, "show_activities", {"activity_ids": ids[:2]})] + [
        call(i, "travel_time", {"from_id": ids[0], "to_id": ids[i]}) for i in range(1, 4)]
    results = await run_tool_calls(tools, calls)
    assert [r["tool_use_id"] for r in results] == ["t0", "t1", "t2", "t3"]
    assert SlowRoutes.peak == 3


def test_response_blocks_are_echoed_without_sdk_only_fields():
    from anthropic.types import TextBlock, ToolUseBlock
    from app.runner import content_param
    text = TextBlock(type="text", text="Checking times.", citations=None)
    tool = ToolUseBlock(type="tool_use", id="t1", name="travel_time", input={"from_id": "a", "to_id": "b"})
    assert content_param(text) == {"type": "text", "text": "Checking times."}
    assert content_param(tool) == {"type": "tool_use", "id": "t1", "name": "travel_time", "input": {"from_id": "a", "to_id": "b"}}
