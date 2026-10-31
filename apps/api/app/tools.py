from __future__ import annotations

import json
from datetime import datetime
from .models import Activity, ItineraryDay, ItineraryStop

TOOL_TTL_SEC = 15 * 60


def tool_schemas():
    return [
        {"name": "search_activities", "description": "Search Genie activity index for one city. Use before recommendations.", "input_schema": {"type": "object", "properties": {"city": {"type": "string"}, "query": {"type": "string"}, "categories": {"type": "array", "items": {"type": "string", "enum": ["sightseeing", "adventure", "show", "food", "culture"]}}, "max_price_usd": {"type": "number"}, "limit": {"type": "integer", "minimum": 1, "maximum": 15}}, "required": ["city", "query"], "additionalProperties": False}},
        {"name": "find_events", "description": "Find dated shows, concerts and sports events in a city between two dates.", "input_schema": {"type": "object", "properties": {"city": {"type": "string"}, "from": {"type": "string"}, "to": {"type": "string"}, "keyword": {"type": "string"}}, "required": ["city", "from", "to"], "additionalProperties": False}},
        {"name": "get_tour_offers", "description": "Find bookable tours and experiences with prices.", "input_schema": {"type": "object", "properties": {"city": {"type": "string"}, "query": {"type": "string"}, "max_price_usd": {"type": "number"}}, "required": ["city", "query"], "additionalProperties": False}},
        {"name": "travel_time", "description": "Estimate minutes between two activities returned earlier this turn.", "input_schema": {"type": "object", "properties": {"from_id": {"type": "string"}, "to_id": {"type": "string"}, "mode": {"type": "string", "enum": ["walk", "transit"]}}, "required": ["from_id", "to_id"], "additionalProperties": False}},
        {"name": "show_activities", "description": "Show ranked activity cards by previously returned activity ids.", "input_schema": {"type": "object", "properties": {"activity_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 12}}, "required": ["activity_ids"], "additionalProperties": False}},
        {"name": "show_itinerary", "description": "Show a feasible day plan using activity ids returned during this turn.", "input_schema": {"type": "object", "properties": {"days": {"type": "array", "minItems": 1, "maxItems": 21, "items": {"type": "object", "properties": {"day": {"type": "integer", "minimum": 1}, "city": {"type": "string"}, "stops": {"type": "array", "minItems": 1, "items": {"type": "object", "properties": {"activity_id": {"type": "string"}, "start": {"type": "string"}, "note": {"type": "string"}}, "required": ["activity_id", "start"], "additionalProperties": False}}}, "required": ["day", "city", "stops"], "additionalProperties": False}}}, "required": ["days"], "additionalProperties": False}},
    ]


class GenieTools:
    def __init__(self, index, providers, kv, emit, party, live_disabled=False):
        self.index, self.providers, self.kv, self.emit = index, providers, kv, emit
        self.party, self.live_disabled = party, live_disabled
        self.seen: dict[str, Activity] = {}

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

    async def call(self, name, args):
        if name == "search_activities":
            city, query = args["city"], args["query"]
            await self.emit({"type": "status", "message": f"Searching activities in {city}"})
            results = await self.index.search(city, query, args.get("categories"), args.get("max_price_usd"), self.party, min(args.get("limit", 10), 15))
            for result in results:
                self.seen[result["activity"].id] = result["activity"]
            if not results:
                cities = await self.index.cities()
                return {"results": [], "note": f"No matches. Genie currently covers: {', '.join(cities)}."}
            return {"results": [self.brief(r["activity"]) for r in results]}

        if name == "find_events":
            if self.live_disabled:
                return {"error": "Live event search is paused right now. Recommend from search_activities instead."}
            await self.emit({"type": "status", "message": f"Looking up shows in {args['city']}"})
            params = {"city": args["city"], "from": args["from"], "to": args["to"], "keyword": args.get("keyword")}
            events = await self._cached(self.providers.events.name, params, lambda: self.providers.events.find_events(**params))
            self.seen.update({a.id: a for a in events})
            return {"events": [self.brief(a) for a in events]}

        if name == "get_tour_offers":
            if self.live_disabled:
                return {"error": "Live tour offers are paused right now. Recommend from search_activities instead."}
            await self.emit({"type": "status", "message": f"Checking tour offers in {args['city']}"})
            params = {"city": args["city"], "query": args["query"], "max_price_usd": args.get("max_price_usd")}
            offers = await self._cached(self.providers.tours.name, params, lambda: self.providers.tours.find_tours(**params))
            self.seen.update({a.id: a for a in offers})
            return {"offers": [self.brief(a) for a in offers]}

        if name == "travel_time":
            source, target = self.seen.get(args["from_id"]), self.seen.get(args["to_id"])
            if not source or not target:
                return {"error": "Both ids must come from a search earlier in this turn."}
            return {"minutes": await self.providers.routes.travel_minutes(source, target, args.get("mode", "transit"))}

        if name == "show_activities":
            ids = args["activity_ids"]
            unknown = [item for item in ids if item not in self.seen]
            if unknown:
                return {"error": f"Unknown ids (search for them first): {', '.join(unknown)}"}
            await self.emit({"type": "cards", "activities": [self.seen[item].model_dump(by_alias=True, exclude_none=True) for item in ids]})
            return {"shown": len(ids)}

        if name == "show_itinerary":
            days = [ItineraryDay.model_validate(day) for day in args["days"]]
            issues = await self.check_itinerary(days)
            if issues:
                return {"error": "The plan is not feasible yet:\n- " + "\n- ".join(issues)}
            ids = list(dict.fromkeys(stop.activity_id for day in days for stop in day.stops))
            activities = [self.seen[item] for item in ids]
            await self.emit({"type": "itinerary", "days": [d.model_dump(by_alias=True) for d in days],
                       "activities": [a.model_dump(by_alias=True, exclude_none=True) for a in activities]})
            return {"shown_days": len(days)}
        return {"error": f"Unknown tool: {name}"}

    async def check_itinerary(self, days: list[ItineraryDay]):
        issues = []
        for day in days:
            previous = None
            for stop in day.stops:
                activity = self.seen.get(stop.activity_id)
                if not activity:
                    issues.append(f"Day {day.day}: {stop.activity_id} is not an activity returned by a search this turn.")
                    continue
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

    def search_result(self, activity_ids):
        return [self.seen[i] for i in activity_ids if i in self.seen]
