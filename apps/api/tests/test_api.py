import json
from fastapi.testclient import TestClient
from app.main import app


def _events(stream):
    parsed = []
    for frame in stream.split("\n\n"):
        data = next((line[6:] for line in frame.splitlines() if line.startswith("data: ")), None)
        if data:
            parsed.append(json.loads(data))
    return parsed


def test_health_and_streamed_chat_contract(monkeypatch):
    monkeypatch.setenv("GENIE_OFFLINE", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200 and health.json()["ok"] is True
        response = client.post("/api/chat", json={"message": "Fun things to do in Lisbon with kids under $60", "sessionId": "test-session"})
        assert response.status_code == 200
        events = _events(response.text)
        assert events[0] == {"type": "session", "sessionId": "test-session"}
        cards = next(event for event in events if event["type"] == "cards")
        assert cards["activities"]
        assert all(activity["city"] == "Lisbon" and activity["priceFromUsd"] <= 60 for activity in cards["activities"])
        assert any(event["type"] == "done" for event in events)


def test_chat_input_and_kill_switch_auth(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin")
    with TestClient(app) as client:
        assert client.post("/api/chat", json={"message": ""}).status_code == 422
        assert client.post("/api/admin/kill-switch", json={"mode": "off"}).status_code == 401
        changed = client.post("/api/admin/kill-switch", json={"mode": "off"}, headers={"Authorization": "Bearer test-admin"})
        assert changed.json() == {"mode": "off"}
        response = client.post("/api/chat", json={"message": "things to do in Rome"})
        events = _events(response.text)
        assert any(event.get("type") == "text" and "resting" in event.get("delta", "") for event in events)


def test_transient_model_failure_retries_with_fallback(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("GENIE_OFFLINE", "1")

    class FailingOnceRunner:
        def __init__(self):
            self.models = []

        async def run(self, model, prompt, message, intent, tools, emit_text):
            self.models.append(model)
            if len(self.models) == 1:
                raise RuntimeError("529 overloaded_error")
            await emit_text("Fallback answer.")
            return {"text": "Fallback answer.", "cost_usd": 0, "turns": 1,
                    "stopped_early": None, "answered_by": model}

    with TestClient(app) as client:
        runner = FailingOnceRunner()
        app.state.deps["runner"] = runner
        response = client.post("/api/chat", json={"message": "things to do in Rome", "sessionId": "fallback-test"})
        events = _events(response.text)
        done = next(event for event in events if event["type"] == "done")
        assert runner.models == ["claude-sonnet-5-5", "claude-sonnet-5"]
        assert done["model"] == "claude-sonnet-5"
        assert "using fallback" in done["routeReason"]
