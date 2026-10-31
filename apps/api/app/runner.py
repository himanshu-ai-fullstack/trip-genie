from __future__ import annotations

import json
import os
from datetime import datetime
from .prompt import SYSTEM_PROMPT
from .tools import GenieTools, tool_schemas
from .policies import MAX_TURNS, LIMITS

COST_RATES = {
    "claude-opus-5-5": (float(os.getenv("GENIE_OPUS_INPUT_USD_PER_MILLION", "15")), float(os.getenv("GENIE_OPUS_OUTPUT_USD_PER_MILLION", "75"))),
    "claude-sonnet-5-5": (float(os.getenv("GENIE_SONNET_INPUT_USD_PER_MILLION", "3")), float(os.getenv("GENIE_SONNET_OUTPUT_USD_PER_MILLION", "15"))),
    "claude-sonnet-5": (float(os.getenv("GENIE_SONNET_INPUT_USD_PER_MILLION", "3")), float(os.getenv("GENIE_SONNET_OUTPUT_USD_PER_MILLION", "15"))),
    "claude-haiku-5-5": (float(os.getenv("GENIE_HAIKU_INPUT_USD_PER_MILLION", "0.8")), float(os.getenv("GENIE_HAIKU_OUTPUT_USD_PER_MILLION", "4"))),
}


class OfflineRunner:
    async def run(self, model, prompt, message, intent, tools: GenieTools, emit_text):
        turns = 0
        cities = intent.cities
        if not cities:
            profile = re_search(r"destination: (.*)", prompt)
            cities = [c.strip() for c in profile.split(",") if c.strip() and c.strip() != "unknown"] if profile else []
        budget = intent.budget_usd
        if budget is None:
            found = re_search(r"budget per activity \(USD\): ([\d.]+)", prompt)
            budget = float(found) if found else None
        if not cities:
            text = "Happy to help! Where are you heading, and roughly what's your budget per activity?"
            await emit_text(text)
            return {"text": text, "cost_usd": 0, "turns": 1, "stopped_early": None, "answered_by": "offline"}

        activities = []
        for city in cities:
            turns += 1
            result = await tools.call("search_activities", {"city": city, "query": message,
                "categories": intent.categories or None, "max_price_usd": budget, "limit": 10})
            activities.extend(tools.search_result([a["id"] for a in result.get("results", [])]))
        if not activities:
            text = f"I couldn't find matches in {' and '.join(cities)} for that. Try a higher budget or different interests."
            await emit_text(text)
            return {"text": text, "cost_usd": 0, "turns": turns, "stopped_early": None, "answered_by": "offline"}

        days = intent.days
        if days is None and intent.wants_itinerary:
            match = re_search(r"trip length \(days\): (\d+)", prompt)
            days = int(match) if match else None
        if intent.wants_itinerary and days:
            plan = await self._plan(activities, cities, days, tools)
            result = await tools.call("show_itinerary", {"days": plan})
            turns += 1
            text = f"Here's a {len(plan)}-day plan for {' and '.join(cities)}. Each day mixes a big sight with something local, with time to travel between stops."
            if "error" in result:
                text = "I put together a rough plan but the timings didn't fit. Ask me for the activity list instead."
            await emit_text(text)
            return {"text": text, "cost_usd": 0, "turns": turns, "stopped_early": None, "answered_by": "offline"}

        await tools.call("show_activities", {"activity_ids": [a.id for a in activities[:6]]})
        suffix = f" under ${budget:g}" if budget is not None else ""
        text = f"Here are my top picks in {' and '.join(cities)}{suffix}. Tap a card to see it and book. Want me to turn these into a day by day plan?"
        await emit_text(text)
        return {"text": text, "cost_usd": 0, "turns": turns + 1, "stopped_early": None, "answered_by": "offline"}

    async def _plan(self, activities, cities, days, tools):
        pools = {city: [a for a in activities if a.city == city] for city in cities}
        plan = []
        for d in range(days):
            city = cities[min(len(cities) - 1, d * len(cities) // days)]
            pool = pools.get(city, [])
            stops, clock, previous = [], 9 * 60, None
            while len(stops) < 3 and pool:
                activity = pool.pop(0)
                if previous:
                    clock += await tools.providers.routes.travel_minutes(previous, activity, "transit") + 30
                clock = ((clock + 14) // 15) * 15
                if stops and clock + activity.duration_hours * 60 > 22 * 60:
                    pool.insert(0, activity)
                    break
                stops.append({"activity_id": activity.id, "start": f"{clock // 60:02}:{clock % 60:02}"})
                clock += round(activity.duration_hours * 60)
                previous = activity
                if activity.duration_hours >= 7:
                    break
            if stops:
                plan.append({"day": len(plan) + 1, "city": city, "stops": stops})
        return plan


class AnthropicRunner:
    def __init__(self, api_key: str):
        from anthropic import AsyncAnthropic
        self.client = AsyncAnthropic(api_key=api_key)

    async def run(self, model, prompt, message, intent, tools: GenieTools, emit_text):
        transcript = [{"role": "user", "content": prompt}]
        final_text = ""
        cost_usd = 0.0
        input_rate, output_rate = COST_RATES.get(model, (0.0, 0.0))
        for turn in range(1, MAX_TURNS.get(model, 8) + 1):
            async with self.client.messages.stream(model=model, max_tokens=2048, system=SYSTEM_PROMPT,
                messages=transcript, tools=tool_schemas()) as stream:
                async for event in stream:
                    if event.type == "content_block_delta" and getattr(event.delta, "type", None) == "text_delta":
                        final_text += event.delta.text
                        await emit_text(event.delta.text)
                response = await stream.get_final_message()
            cost_usd += (response.usage.input_tokens * input_rate + response.usage.output_tokens * output_rate) / 1_000_000
            transcript.append({"role": "assistant", "content": [block.model_dump() for block in response.content]})
            tool_uses = [block for block in response.content if block.type == "tool_use"]
            if not tool_uses:
                return {"text": final_text, "cost_usd": cost_usd, "turns": turn, "stopped_early": None, "answered_by": response.model}
            if cost_usd >= LIMITS["turn_budget_usd"]:
                note = "\n\n(I stopped early to keep this quick. Ask me to keep going if you'd like more.)"
                await emit_text(note)
                final_text += note
                return {"text": final_text, "cost_usd": cost_usd, "turns": turn, "stopped_early": "budget", "answered_by": response.model}
            results = []
            for call in tool_uses:
                try:
                    output = await tools.call(call.name, call.input)
                    is_error = "error" in output
                except Exception as error:
                    output, is_error = {"error": str(error)}, True
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": json.dumps(output), "is_error": is_error})
            transcript.append({"role": "user", "content": results})
        note = "\n\n(I stopped early to keep this quick. Ask me to keep going if you'd like more.)"
        await emit_text(note)
        final_text += note
        return {"text": final_text, "cost_usd": cost_usd, "turns": MAX_TURNS.get(model, 8), "stopped_early": "step limit", "answered_by": model}


def re_search(pattern, text):
    import re
    match = re.search(pattern, text)
    return match.group(1) if match else None


def create_runner():
    key = os.getenv("ANTHROPIC_API_KEY")
    return OfflineRunner() if os.getenv("GENIE_OFFLINE") == "1" or not key else AnthropicRunner(key)
