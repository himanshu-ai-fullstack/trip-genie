from app.intent import extract_intent
from app.policies import CircuitBreaker, backoff_delay, check_rate_limits, is_rejection, route_model, transient_model_error
from app.stores import MemoryKV


def test_model_router_escalates_complex_and_degraded_requests():
    assert route_model(extract_intent("things to do in Rome"))["model"] == "claude-sonnet-5-5"
    route = route_model(extract_intent("Plan a 7 day trip to Tokyo and Bali"))
    assert route["model"] == "claude-opus-5-5"
    assert "7-day plan" in route["reason"]
    assert route_model(extract_intent("Plan 10 days in Rome and Paris"), planner_disabled=True)["model"] == "claude-sonnet-5-5"


def test_circuit_breaker_falls_back_after_three_failures():
    breaker = CircuitBreaker()
    for _ in range(3):
        breaker.record_failure("claude-opus-5-5")
    assert breaker.resolve("claude-opus-5-5") == {"model": "claude-sonnet-5-5", "fallback": "claude-sonnet-5", "tripped": True}
    assert transient_model_error(RuntimeError("529 overloaded_error"))
    assert not transient_model_error(RuntimeError("400 invalid_request_error"))


async def test_rate_limit_counter_uses_key_value_store():
    kv = MemoryKV()
    for _ in range(20):
        assert await check_rate_limits(kv, "127.0.0.1", str(_)) is None
    assert await check_rate_limits(kv, "127.0.0.1", "last") == "Too many requests. Please wait a minute."


def test_task_router_returns_validated_route_with_confidence():
    route = route_model(extract_intent("things to do in Rome"), message="things to do in Rome")
    assert route["task"] == "recommend" and 0 < route["confidence"] <= 1 and not route["needs_clarification"]
    assert route_model(extract_intent("Plan a 3 day trip to Lisbon"))["task"] == "itinerary"
    assert route_model(extract_intent("concerts in London this weekend"), message="concerts in London this weekend")["task"] == "events"
    assert route_model(extract_intent("things to do in Rome"), rejected=True)["task"] == "refine"


def test_clarify_route_uses_the_cheap_helper_model():
    unknown = route_model(extract_intent("what can I do in Berlin"), message="what can I do in Berlin")
    assert unknown["task"] == "clarify" and unknown["needs_clarification"]
    assert unknown["model"] == "claude-haiku-5-5" and unknown["confidence"] < 0.9
    assert route_model(extract_intent("hi there"), message="hi there")["confidence"] == 0.9
    assert is_rejection("that doesn't work for us") and not is_rejection("love it")


def test_retry_policy_only_retries_transient_errors_with_bounded_jitter():
    class Status(Exception):
        def __init__(self, status_code):
            self.status_code = status_code
    assert transient_model_error(Status(429)) and transient_model_error(Status(503))
    assert not transient_model_error(Status(400)) and not transient_model_error(Status(403))
    assert all(0 <= backoff_delay(attempt) <= 2.0 for attempt in range(10))
