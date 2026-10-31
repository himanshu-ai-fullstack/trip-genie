from __future__ import annotations

import re
from .catalog import CITY_NAMES
from .models import Intent, TripProfile

CITY_ALIASES = {"nyc": "New York", "new york city": "New York", "manhattan": "New York", "ubud": "Bali", "the uk": "London"}
CATEGORY_WORDS = {
    "sightseeing": ["sightseeing", "sights", "landmark", "landmarks", "views", "viewpoint", "must see", "must-see", "iconic", "tour"],
    "adventure": ["adventure", "adventurous", "thrill", "hike", "hiking", "trek", "rafting", "surf", "surfing", "skydive", "outdoor", "active", "bike", "snorkel", "diving"],
    "show": ["show", "shows", "theatre", "theater", "musical", "broadway", "concert", "cabaret", "opera", "performance", "nightlife", "flamenco", "fado"],
    "food": ["food", "eat", "eating", "restaurant", "restaurants", "tapas", "street food", "cooking", "foodie", "wine", "dinner"],
    "culture": ["museum", "museums", "history", "historic", "art", "culture", "cultural", "temple", "palace"],
}
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
NUMBER_WORDS = {**{w: i for i, w in enumerate(["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve" ]) if i}, "fourteen": 14}
CONSTRAINT_WORDS = {
    "kids": ["kid", "kids", "child", "children", "toddler", "baby", "teen", "teens"],
    "accessibility": ["wheelchair", "accessible", "mobility", "elderly", "stroller", "pram"],
    "diet": ["vegetarian", "vegan", "halal", "kosher", "gluten"],
    "fixedDates": ["tickets for", "on the", "already booked", "fixed date", "arriving on"],
    "weather": ["rain", "rainy", "indoor", "hot weather"],
}


def _has_word(text: str, word: str) -> bool:
    return re.search(r"(^|[^a-z])" + re.escape(word) + r"([^a-z]|$)", text) is not None


def _number(token: str):
    return int(token) if token.isdigit() else NUMBER_WORDS.get(token)


def extract_intent(question: str) -> Intent:
    text = question.lower()
    cities = []
    for city in CITY_NAMES:
        if _has_word(text, city.lower()) and city not in cities:
            cities.append(city)
    for alias, city in CITY_ALIASES.items():
        if _has_word(text, alias) and city not in cities:
            cities.append(city)
    categories = [category for category, words in CATEGORY_WORDS.items() if any(_has_word(text, word) for word in words)]

    budget = None
    patterns = [r"(?:under|below|less than|max(?:imum)?|budget(?: of| is)?|up to|cheaper than)\s*[$€£]?\s*(\d[\d,]*)", r"[$€£]\s*(\d[\d,]*)", r"(\d[\d,]*)\s*(?:usd|dollars?|euros?|eur|pounds?|gbp)"]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            budget = float(match.group(1).replace(",", ""))
            break
    if budget is None and re.search(r"\b(cheap|budget|free|low cost|affordable)\b", text):
        budget = 30

    days = None
    for match in re.finditer(r"\b(\d+|[a-z]+)[- ]?(?:day|days|night|nights)\b", text):
        days = _number(match.group(1))
        if days is not None:
            break
    if days is None:
        week = re.search(r"\b(a|one|two|\d+)[- ]weeks?\b", text)
        if week:
            days = 7 * (1 if week.group(1) == "a" else (_number(week.group(1)) or 1))
        elif re.search(r"\bweekend\b", text):
            days = 2

    month = next((m for m in MONTHS if _has_word(text, m)), None)
    party = None
    if re.search(r"\b(kids?|children|family|toddler|son|daughter)\b", text):
        party = "family"
    elif re.search(r"\b(wife|husband|partner|girlfriend|boyfriend|honeymoon|couple|anniversary|romantic)\b", text):
        party = "couple"
    elif re.search(r"\b(friends|group|team|bachelor|bachelorette|stag|hen)\b", text):
        party = "group"
    elif re.search(r"\b(solo|alone|by myself|on my own)\b", text):
        party = "solo"

    wants = bool(re.search(r"\b(itinerary|day plan|day-by-day|day by day|schedule|plan my|plan a|plan our|plan the)\b", text)) or days is not None
    constraints = [name for name, words in CONSTRAINT_WORDS.items() if any(word in text for word in words)]
    if budget is not None:
        constraints.append("budget")
    follow_up = bool(re.search(r"\b(swap|replace|instead|that one|those|more like|another one|change day|day \d+|the second|the first|it again)\b", text))
    return Intent(cities=cities, categories=categories, budget_usd=budget, month=month, days=days, party=party,
                  wants_itinerary=wants, constraints=constraints, follow_up=follow_up)


def merge_profile(profile: TripProfile, intent: Intent) -> TripProfile:
    return TripProfile(
        cities=intent.cities or profile.cities,
        budget_usd=intent.budget_usd if intent.budget_usd is not None else profile.budget_usd,
        month=intent.month or profile.month,
        days=intent.days if intent.days is not None else profile.days,
        party=intent.party or profile.party,
        interests=list(dict.fromkeys(profile.interests + intent.categories)),
        constraints=list(dict.fromkeys(profile.constraints + intent.constraints)),
    )


def effective_intent(intent: Intent, profile: TripProfile) -> Intent:
    data = intent.model_dump()
    data["cities"] = intent.cities or profile.cities
    data["categories"] = intent.categories or profile.interests
    data["budget_usd"] = intent.budget_usd if intent.budget_usd is not None else profile.budget_usd
    data["month"] = intent.month or profile.month
    data["days"] = intent.days if intent.days is not None else (profile.days if intent.wants_itinerary else None)
    data["party"] = intent.party or profile.party
    data["constraints"] = list(dict.fromkeys(intent.constraints + profile.constraints))
    return Intent(**data)
