from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import Counter
from datetime import date, datetime
from typing import NamedTuple, Optional
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from .models import Activity, Category, ItineraryDay
from .policies import LIMITS, backoff_delay, transient_model_error

logger = logging.getLogger("trip_genie")
TOOL_TTL_SEC = 15 * 60
MAX_IDENTICAL_CALLS = 2
# Initial plan plus two revisions, then the agent must stop revising and fall back to a list.
MAX_PLAN_ATTEMPTS = 3
CONCURRENT_TOOL_CALLS = 4
URL_PATTERN = re.compile(r"https?://\S+")
PRICE_PATTERN = re.compile(r"[$€£]\s?(\d+(?:\.\d+)?)")


class SearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    city: str = Field(min_length=1, max_length=80)
    query: str = Field(min_length=1, max_length=300)
    categories: Optional[list[Category]] = None
    max_price_usd: Optional[float] = Field(default=None, ge=0)
    limit: int = Field(default=10, ge=1, le=15)


class EventsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    city: str = Field(min_length=1, max_length=80)
    start: date = Field(alias="from")
    end: date = Field(alias="to")
    keyword: Optional[str] = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def _window(self):
        if self.end < self.start:
            raise ValueError("'to' must be on or after 'from'")
        if (self.end - self.start).days > 31:
            raise ValueError("date window must be 31 days or less")
        return self


class TourArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    city: str = Field(min_length=1, max_length=80)
    query: str = Field(min_length=1, max_length=300)
    max_price_usd: Optional[float] = Field(default=None, ge=0)


class TravelArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_id: str
    to_id: str
    mode: str = Field(default="transit", pattern="^(walk|transit)$")


class ShowActivitiesArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    activity_ids: list[str] = Field(min_length=1, max_length=12)


class ShowItineraryArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    days: list[ItineraryDay] = Field(min_length=1, max_length=21)


class ToolSpec(NamedTuple):
    """Runtime contract for one tool. The model proposes a call; this decides how it runs."""
    args: type
    timeout_s: float
    retries: int
    # Read-only tools have no visible side effect, so they are safe to run in parallel and to retry.
    read_only: bool


TOOL_SPECS = {
    "search_activities": ToolSpec(SearchArgs, 5, 1, True),
    "find_events": ToolSpec(EventsArgs, 8, 2, True),
    "get_tour_offers": ToolSpec(TourArgs, 8, 2, True),
    "travel_time": ToolSpec(TravelArgs, 3, 1, True),
    "show_activities": ToolSpec(ShowActivitiesArgs, 5, 0, False),
    "show_itinerary": ToolSpec(ShowItineraryArgs, 10, 0, False),
}
READ_ONLY_TOOLS = frozenset(name for name, spec in TOOL_SPECS.items() if spec.read_only)


def tool_error(error_type: str, message: str, retryable: bool = False, **extra):
    """Errors go back to the model as data it can act on, never as a raw exception."""
    return {"error": message, "error_type": error_type, "retryable": retryable, **extra}


def tool_schemas():
    return [
        {"name": "search_activities", "description": "Search Genie activity index for one city. Use before recommendations.", "input_schema": {"type": "object", "properties": {"city": {"type": "string"}, "query": {"type": "string"}, "categories": {"type": "array", "items": {"type": "string", "enum": ["sightseeing", "adventure", "show", "food", "culture"]}}, "max_price_usd": {"type": "number"}, "limit": {"type": "integer", "minimum": 1, "maximum": 15}}, "required": ["city", "query"], "additionalProperties": False}},
        {"name": "find_events", "description": "Find dated shows, concerts and sports events in a city between two dates (YYYY-MM-DD, at most 31 days apart).", "input_schema": {"type": "object", "properties": {"city": {"type": "string"}, "from": {"type": "string"}, "to": {"type": "string"}, "keyword": {"type": "string"}}, "required": ["city", "from", "to"], "additionalProperties": False}},
        {"name": "get_tour_offers", "description": "Find bookable tours and experiences with prices.", "input_schema": {"type": "object", "properties": {"city": {"type": "string"}, "query": {"type": "string"}, "max_price_usd": {"type": "number"}}, "required": ["city", "query"], "additionalProperties": False}},
        {"name": "travel_time", "description": "Estimate minutes between two activities returned earlier this turn.", "input_schema": {"type": "object", "properties": {"from_id": {"type": "string"}, "to_id": {"type": "string"}, "mode": {"type": "string", "enum": ["walk", "transit"]}}, "required": ["from_id", "to_id"], "additionalProperties": False}},
        {"name": "show_activities", "description": "Show ranked activity cards by previously returned activity ids.", "input_schema": {"type": "object", "properties": {"activity_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 12}}, "required": ["activity_ids"], "additionalProperties": False}},
        {"name": "show_itinerary", "description": "Show a feasible day plan using activity ids returned during this turn. If it returns issues, fix them and call it again; after 3 attempts it stops accepting plans.", "input_schema": {"type": "object", "properties": {"days": {"type": "array", "minItems": 1, "maxItems": 21, "items": {"type": "object", "properties": {"day": {"type": "integer", "minimum": 1}, "city": {"type": "string"}, "stops": {"type": "array", "minItems": 1, "items": {"type": "object", "properties": {"activity_id": {"type": "string"}, "start": {"type": "string"}, "note": {"type": "string"}}, "required": ["activity_id", "start"], "additionalProperties": False}}}, "required": ["day", "city", "stops"], "additionalProperties": False}}}, "required": ["days"], "additionalProperties": False}},
    ]


class GenieTools:
    def __init__(self, index, providers, kv, emit, party, live_disabled=False, budget_usd=None, trace=None):
        self.index, self.providers, self.kv, self.emit = index, providers, kv, emit
        self.party, self.live_disabled, self.budget_usd, self.trace = party, live_disabled, budget_usd, trace
        self.seen: dict[str, Activity] = {}
        self.calls = 0
        self.repeats: Counter = Counter()
        self.plan_attempts = 0
        # Bulkhead: parallel tool calls from one turn can't flood a provider.
        self.bulkhead = asyncio.Semaphore(CONCURRENT_TOOL_CALLS)

    @staticmethod
    def brief(activity: Activity):
        return {"id": activity.id, "name": activity.name, "category": activity.category,
                "price_from_usd": activity.price_from_usd, "duration_hours": activity.duration_hours,
                "rating": activity.rating, "tags": activity.tags, "description": activity.description,
                **({"date": activity.date} if activity.date else {})}

    async def _cached(self, provider, args, call):
        normalized = json.dumps(args, sort_keys=True, separators=(",", ":")).lower()
        key = f"genie:tool:{provider}:{normalized}"
        cached = await self.kv.get(key)
        if cached:
            return [Activity.model_validate(item) for item in json.loads(cached)]
        result = await call()
        await self.kv.set(key, json.dumps([a.model_dump(by_alias=True) for a in result]), TOOL_TTL_SEC)
        return result

    async def call(self, name, raw_args):
        """Validate, budget, de-loop, then execute with a timeout and bounded retries."""
        started = time.monotonic()
        spec = TOOL_SPECS.get(name)
        if spec is None:
            return await self._finish(name, started, tool_error("unknown_tool", f"Unknown tool: {name}. Use one of: {', '.join(TOOL_SPECS)}."))
        self.calls += 1
        if self.calls > LIMITS["max_tool_calls"]:
            return await self._finish(name, started, tool_error("tool_budget", "Tool call limit reached for this turn. Answer with what you already have."))
        try:
            args = spec.args.model_validate(raw_args or {})
        except ValidationError as error:
            problems = "; ".join(f"{'.'.join(str(part) for part in item['loc']) or 'arguments'}: {item['msg']}" for item in error.errors())
            return await self._finish(name, started, tool_error("validation_error", f"Invalid arguments: {problems}. Fix them and call again."))
        signature = name + json.dumps(args.model_dump(mode="json", by_alias=True), sort_keys=True)
        self.repeats[signature] += 1
        if self.repeats[signature] > MAX_IDENTICAL_CALLS:
            return await self._finish(name, started, tool_error("loop_detected", "You already made this exact call. Use the earlier result or change the arguments."))

        handler = getattr(self, f"_{name}")
        attempt = 0
        while True:
            try:
                async with self.bulkhead:
                    output = await asyncio.wait_for(handler(args), spec.timeout_s)
                return await self._finish(name, started, output, attempt)
            except Exception as error:
                retryable = transient_model_error(error)
                if retryable and attempt < spec.retries:
                    attempt += 1
                    await asyncio.sleep(backoff_delay(attempt))
                    continue
                logger.warning("tool failed name=%s attempts=%s error=%r", name, attempt + 1, error)
                kind = "timeout" if isinstance(error, asyncio.TimeoutError) else "upstream_error" if retryable else "tool_error"
                message = f"{name} is unavailable right now ({kind}). Continue with the other tools." if retryable else f"{name} failed: {error}"
                return await self._finish(name, started, tool_error(kind, message, retryable), attempt)

    async def _finish(self, name, started, output, retries=0):
        if self.trace:
            await self.trace.step("tool", name, output.get("error_type", "ok"), (time.monotonic() - started) * 1000, retries=retries)
        return output

    async def _search_activities(self, args: SearchArgs):
        await self.emit({"type": "status", "message": f"Searching activities in {args.city}"})
        results = await self.index.search(args.city, args.query, args.categories, args.max_price_usd, self.party, args.limit)
        for result in results:
            self.seen[result["activity"].id] = result["activity"]
        if not results:
            cities = await self.index.cities()
            return {"results": [], "note": f"No matches. Genie currently covers: {', '.join(cities)}."}
        return {"results": [self.brief(r["activity"]) for r in results]}

    async def _find_events(self, args: EventsArgs):
        if self.live_disabled:
            return tool_error("paused", "Live event search is paused right now. Recommend from search_activities instead.")
        await self.emit({"type": "status", "message": f"Looking up shows in {args.city}"})
        params = {"city": args.city, "start": args.start.isoformat(), "end": args.end.isoformat(), "keyword": args.keyword}
        events = await self._cached(self.providers.events.name, params, lambda: self.providers.events.find_events(**params))
        self.seen.update({a.id: a for a in events})
        return {"events": [self.brief(a) for a in events]}

    async def _get_tour_offers(self, args: TourArgs):
        if self.live_disabled:
            return tool_error("paused", "Live tour offers are paused right now. Recommend from search_activities instead.")
        await self.emit({"type": "status", "message": f"Checking tour offers in {args.city}"})
        params = args.model_dump()
        offers = await self._cached(self.providers.tours.name, params, lambda: self.providers.tours.find_tours(**params))
        self.seen.update({a.id: a for a in offers})
        return {"offers": [self.brief(a) for a in offers]}

    async def _travel_time(self, args: TravelArgs):
        source, target = self.seen.get(args.from_id), self.seen.get(args.to_id)
        if not source or not target:
            return tool_error("unknown_id", "Both ids must come from a search earlier in this turn.")
        return {"minutes": await self.providers.routes.travel_minutes(source, target, args.mode)}

    async def _show_activities(self, args: ShowActivitiesArgs):
        unknown = [item for item in args.activity_ids if item not in self.seen]
        if unknown:
            return tool_error("unknown_id", f"Unknown ids (search for them first): {', '.join(unknown)}")
        await self.emit({"type": "cards", "activities": [self.seen[item].model_dump(by_alias=True, exclude_none=True) for item in args.activity_ids]})
        return {"shown": len(args.activity_ids)}

    async def _show_itinerary(self, args: ShowItineraryArgs):
        """Evaluator-optimizer: an objective checker scores each draft plan and the model revises, up to a cap."""
        self.plan_attempts += 1
        attempt = self.plan_attempts
        limit_message = (f"The plan is still not feasible after {MAX_PLAN_ATTEMPTS} attempts. Stop revising: show the best "
                         "activities with show_activities and offer a lighter plan.")
        if attempt > MAX_PLAN_ATTEMPTS:
            return tool_error("revision_limit", limit_message)
        issues = await self.check_itinerary(args.days)
        if self.trace:
            await self.trace.check(f"itinerary attempt {attempt}", issues)
        if issues:
            if attempt == MAX_PLAN_ATTEMPTS:
                return tool_error("revision_limit", limit_message, issues=issues)
            return tool_error("plan_infeasible", "The plan is not feasible yet:\n- " + "\n- ".join(issues),
                              issues=issues, attempt=attempt, attempts_left=MAX_PLAN_ATTEMPTS - attempt)
        ids = list(dict.fromkeys(stop.activity_id for day in args.days for stop in day.stops))
        await self.emit({"type": "itinerary", "days": [d.model_dump(by_alias=True) for d in args.days],
                         "activities": [self.seen[item].model_dump(by_alias=True, exclude_none=True) for item in ids]})
        return {"shown_days": len(args.days), "attempt": attempt}

    async def check_itinerary(self, days: list[ItineraryDay]):
        issues = []
        used = Counter(stop.activity_id for day in days for stop in day.stops)
        issues.extend(f"{self.seen[item].name if item in self.seen else item} appears {count} times; use each activity once."
                      for item, count in used.items() if count > 1)
        for day in days:
            previous = None
            for stop in day.stops:
                activity = self.seen.get(stop.activity_id)
                if not activity:
                    issues.append(f"Day {day.day}: {stop.activity_id} is not an activity returned by a search this turn.")
                    continue
                if activity.city.lower() != day.city.lower():
                    issues.append(f"Day {day.day}: {activity.name} is in {activity.city}, not {day.city}.")
                if self.budget_usd is not None and activity.price_from_usd > self.budget_usd:
                    issues.append(f"Day {day.day}: {activity.name} costs from ${activity.price_from_usd:g}, over the ${self.budget_usd:g} budget per activity.")
                try:
                    start = datetime.strptime(stop.start, "%H:%M")
                    if len(stop.start) != 5:
                        raise ValueError()
                except ValueError:
                    issues.append(f'Day {day.day}: "{stop.start}" is not a HH:MM time.')
                    continue
                start_min = start.hour * 60 + start.minute
                if start_min < 480:
                    issues.append(f"Day {day.day}: {activity.name} starts before 08:00.")
                end = start_min + round(activity.duration_hours * 60)
                if end > 1410 and activity.duration_hours < 10:
                    issues.append(f"Day {day.day}: {activity.name} ends after 23:30.")
                if previous:
                    prior, prior_end = previous
                    travel = await self.providers.routes.travel_minutes(prior, activity, "transit")
                    if start_min < prior_end + travel:
                        issues.append(f"Day {day.day}: {activity.name} starts at {stop.start} but {prior.name} ends at {prior_end // 60:02}:{prior_end % 60:02} and travel takes about {travel} min.")
                previous = (activity, end)
        return issues

    def grounding_issues(self, text: str):
        """Cheap post-answer check: links and prices in the prose must come from tool results, not memory."""
        issues = []
        if URL_PATTERN.search(text):
            issues.append("answer text contains a link; links belong on the cards")
        known = {round(a.price_from_usd) for a in self.seen.values()}
        if self.budget_usd is not None:
            known.add(round(self.budget_usd))
        for match in PRICE_PATTERN.finditer(text):
            value = round(float(match.group(1)))
            if value not in known:
                issues.append(f"price ${value} in the answer is not on any returned activity")
        return list(dict.fromkeys(issues))

    def search_result(self, activity_ids):
        return [self.seen[i] for i in activity_ids if i in self.seen]
