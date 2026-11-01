from __future__ import annotations

import asyncio
import os
import random
import re
import time
from collections import defaultdict
from .models import Intent, Route

MODELS = {"helper": "claude-haiku-5-5", "standard": "claude-sonnet-5-5", "planner": "claude-opus-5-5"}
FALLBACK = {"claude-opus-5-5": "claude-sonnet-5-5", "claude-sonnet-5-5": "claude-sonnet-5", "claude-haiku-5-5": "claude-sonnet-5-5"}
MAX_TURNS = {"claude-haiku-5-5": 4, "claude-sonnet-5-5": 8, "claude-sonnet-5": 8, "claude-opus-5-5": 14}
LIMITS = {
    "ip_per_minute": int(os.getenv("GENIE_IP_PER_MINUTE", "20")),
    "session_per_hour": int(os.getenv("GENIE_SESSION_PER_HOUR", "60")),
    "turn_budget_usd": float(os.getenv("GENIE_TURN_BUDGET_USD", "0.5")),
    "session_budget_usd": float(os.getenv("GENIE_SESSION_BUDGET_USD", "3")),
    "turn_timeout_ms": int(os.getenv("GENIE_TURN_TIMEOUT_MS", "90000")),
    "max_tool_calls": int(os.getenv("GENIE_MAX_TOOL_CALLS", "24")),
    "max_question_chars": 2000,
}
REJECTION = re.compile(r"\b(doesn'?t work|does not work|not what i|don'?t like|too expensive|wrong|try again|that'?s not|no good|bad plan)\b", re.I)
EVENT_WORDS = re.compile(r"\b(tonight|tomorrow|this (?:week|weekend)|next (?:week|weekend)|tickets?|concerts?|gigs?|match(?:es)?|festivals?|events?)\b", re.I)
PLAN_WORDS = re.compile(r"\b(itinerary|plan|schedule|day by day|day-by-day)\b", re.I)
NAMED_PLACE = re.compile(r"\b(?:in|to|visit(?:ing)?|around)\s+[A-Z][a-z]+")


def is_rejection(message: str) -> bool:
    return REJECTION.search(message) is not None


def classify_task(intent: Intent, message: str = "", rejected: bool = False):
    """Rules-first task classification. Returns (task, confidence, reason); every branch is testable."""
    if rejected:
        return "refine", 0.9, "previous answer rejected"
    if not intent.cities:
        if NAMED_PLACE.search(message):
            return "clarify", 0.6, "destination not in the catalog"
        return "clarify", 0.9, "no destination yet"
    if intent.follow_up:
        return "refine", 0.75, "follow-up on an earlier answer"
    events = EVENT_WORDS.search(message) is not None
    # "concerts this weekend" carries a day count but asks for events, not a plan, unless they say plan.
    if (events or ("show" in intent.categories and intent.month)) and not PLAN_WORDS.search(message):
        return "events", 0.8 if events and "show" in intent.categories else 0.65, "dated shows or events"
    if intent.wants_itinerary:
        return ("itinerary", 0.9, f"{intent.days}-day plan") if intent.days else ("itinerary", 0.65, "plan without a length")
    return "recommend", 0.85 if intent.categories or intent.budget_usd is not None else 0.7, "activity recommendations"


def route_model(intent: Intent, rejected: bool = False, planner_disabled: bool = False, message: str = ""):
    """Picks the task handler, then the cheapest model tier that can do it (helper < standard < planner)."""
    task, confidence, task_reason = classify_task(intent, message, rejected)
    reasons = []
    if len(intent.cities) >= 2:
        reasons.append(f"{len(intent.cities)} cities")
    if intent.wants_itinerary and (intent.days or 0) >= 5:
        reasons.append(f"{intent.days}-day plan")
    if len(intent.constraints) >= 3:
        reasons.append(f"{len(intent.constraints)} hard constraints")
    if rejected:
        reasons.append("previous answer rejected")
    if reasons and not planner_disabled:
        model, reason = MODELS["planner"], ", ".join(reasons)
    elif reasons:
        model, reason = MODELS["standard"], f"planner disabled (would route: {', '.join(reasons)})"
    elif task == "clarify":
        model, reason = MODELS["helper"], f"{task_reason}, using helper model"
    else:
        model, reason = MODELS["standard"], "standard request"
    return Route(task=task, model=model, confidence=confidence, reason=reason, needs_clarification=task == "clarify").model_dump()


class CircuitBreaker:
    def __init__(self, threshold=3, window_ms=60_000, cooldown_ms=300_000):
        self.threshold, self.window, self.cooldown = threshold, window_ms / 1000, cooldown_ms / 1000
        self.failures = defaultdict(list)
        self.open_until = {}

    def is_open(self, model):
        return self.open_until.get(model, 0) > time.monotonic()

    def record_failure(self, model):
        now = time.monotonic()
        failures = [t for t in self.failures[model] if now - t < self.window] + [now]
        if len(failures) >= self.threshold:
            self.open_until[model] = now + self.cooldown
            failures = []
        self.failures[model] = failures

    def record_success(self, model):
        self.failures[model] = []

    def resolve(self, primary):
        fallback = FALLBACK.get(primary)
        if fallback and self.is_open(primary):
            return {"model": fallback, "fallback": FALLBACK.get(fallback), "tripped": True}
        return {"model": primary, "fallback": fallback, "tripped": False}


def transient_model_error(error: Exception) -> bool:
    """Timeouts, 429 and 5xx are worth retrying; 4xx validation and auth errors are not."""
    if isinstance(error, (asyncio.TimeoutError, TimeoutError, ConnectionError)):
        return True
    status = getattr(error, "status_code", None) or getattr(getattr(error, "response", None), "status_code", None)
    if isinstance(status, int):
        return status == 429 or status >= 500
    return re.search(r"\b(overloaded|529|500|502|503|504|rate.?limit|429|timeout|timed out|ECONNRESET)\b", str(error), re.I) is not None


def backoff_delay(attempt: int, base: float = 0.25, cap: float = 2.0) -> float:
    """Exponential backoff with full jitter, so concurrent retries don't line up."""
    return random.uniform(0, min(cap, base * 2 ** attempt))


class KillSwitch:
    KEY = "genie:killswitch"
    RESTING_MESSAGE = "Genie is resting for a moment while we do some maintenance. Please try again in a few minutes."

    def __init__(self, kv):
        self.kv = kv
        self.default = os.getenv("GENIE_KILL_SWITCH", "on")

    async def mode(self):
        return await self.kv.get(self.KEY) or self.default

    async def set(self, mode):
        await self.kv.set(self.KEY, mode)


async def check_rate_limits(kv, ip, session_id):
    minute = int(time.time() // 60)
    ip_count = await kv.incrby(f"genie:rl:ip:{ip}:{minute}", 1, 70)
    if ip_count > LIMITS["ip_per_minute"]:
        return "Too many requests. Please wait a minute."
    session_count = await kv.incrby(f"genie:rl:session:{session_id}", 1, 3600)
    if session_count > LIMITS["session_per_hour"]:
        return "This chat has hit its hourly limit."
    return None


async def check_session_budget(kv, session_id):
    spent = float(await kv.get(f"genie:spend:{session_id}") or 0)
    return "This chat has reached its daily limit." if spent >= LIMITS["session_budget_usd"] else None


async def record_spend(kv, session_id, amount):
    if amount > 0:
        await kv.incrby(f"genie:spend:{session_id}", amount, 86400)
