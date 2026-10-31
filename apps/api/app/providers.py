from __future__ import annotations

import hashlib
import math
import os
from datetime import date, timedelta
from typing import Optional
import httpx
from .catalog import ACTIVITIES
from .models import Activity


class Providers:
    def __init__(self):
        self.events = TicketmasterEvents(os.environ["TICKETMASTER_API_KEY"]) if os.getenv("TICKETMASTER_API_KEY") else MockEvents()
        self.tours = MockTours()
        self.routes = HaversineRoutes()


class MockEvents:
    name = "mock-events"

    async def find_events(self, city: str, start: str, end: str, keyword: Optional[str] = None):
        shows = [a for a in ACTIVITIES if a.city.lower() == city.lower() and a.category == "show"]
        span = max(0, (date.fromisoformat(end) - date.fromisoformat(start)).days)
        result = []
        for activity in shows:
            if keyword and keyword.lower() not in (activity.name + " " + " ".join(activity.tags)).lower():
                continue
            offset = int(hashlib.sha256(activity.id.encode()).hexdigest()[:8], 16) % (span + 1)
            result.append(activity.model_copy(update={"id": f"{activity.id}@{date.fromisoformat(start) + timedelta(days=offset)}", "date": str(date.fromisoformat(start) + timedelta(days=offset)), "venue": activity.name, "provider": self.name}))
        return result


class MockTours:
    name = "mock-tours"

    async def find_tours(self, city: str, query: str, max_price_usd: Optional[float] = None):
        words = [word for word in __import__("re").split(r"\W+", query.lower()) if len(word) > 2]
        offers = [a for a in ACTIVITIES if a.city.lower() == city.lower() and a.price_from_usd > 0
                  and (max_price_usd is None or a.price_from_usd <= max_price_usd)
                  and (not words or any(w in (a.name + " " + a.description + " " + " ".join(a.tags)).lower() for w in words))]
        return [a.model_copy(update={"provider": self.name}) for a in offers[:6]]


class HaversineRoutes:
    name = "haversine"

    async def travel_minutes(self, source: Activity, target: Activity, mode: str = "transit") -> int:
        radius = 6371
        dlat = math.radians(target.lat - source.lat)
        dlng = math.radians(target.lng - source.lng)
        a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(source.lat)) * math.cos(math.radians(target.lat)) * math.sin(dlng / 2) ** 2
        km = 2 * radius * math.asin(math.sqrt(a)) * 1.3
        speed = 4.5 if mode == "walk" else 18
        return max(5, round(km / speed * 60 + (8 if mode == "transit" else 0)))


class TicketmasterEvents:
    name = "ticketmaster"

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def find_events(self, city: str, start: str, end: str, keyword: Optional[str] = None):
        params = {"apikey": self.api_key, "city": city, "startDateTime": f"{start}T00:00:00Z", "endDateTime": f"{end}T23:59:59Z", "size": 10, "sort": "relevance,desc"}
        if keyword:
            params["keyword"] = keyword
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get("https://app.ticketmaster.com/discovery/v2/events.json", params=params)
            response.raise_for_status()
            body = response.json()
        offers = []
        for event in body.get("_embedded", {}).get("events", []):
            venue = (event.get("_embedded", {}).get("venues") or [{}])[0]
            price = (event.get("priceRanges") or [{}])[0].get("min", 0)
            offers.append(Activity(id=f"tm-{event['id']}", name=event["name"], city=city,
                country=(venue.get("country") or {}).get("name", ""), lat=float((venue.get("location") or {}).get("latitude") or 0),
                lng=float((venue.get("location") or {}).get("longitude") or 0), category="show",
                tags=[(((event.get("classifications") or [{}])[0].get("genre") or {}).get("name") or "event").lower()],
                priceFromUsd=price, priceBand="free" if price == 0 else "$" if price < 30 else "$$" if price < 100 else "$$$",
                durationHours=2, rating=0, description=f"{event['name']} at {venue.get('name', 'the venue')}",
                provider=self.name, bookingUrl=event["url"], date=(event.get("dates", {}).get("start") or {}).get("localDate"), venue=venue.get("name")))
        return offers
