from app.intent import extract_intent
from app.policies import CircuitBreaker, check_rate_limits, route_model, transient_model_error
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
