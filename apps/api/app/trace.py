from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from typing import Optional


class RunTrace:
    """Durable state for one chat turn: route, every model and tool step, budget and outcome.

    It is saved to the key-value store after each step, so a slow, failed or aborted run can be
    inspected afterwards (GET /api/runs/{run_id}) without relying on what is left in the model context.
    """

    TTL_SEC = 24 * 3600

    def __init__(self, kv, session_id: str, message: str, run_id: Optional[str] = None):
        self.kv = kv
        self.run_id = run_id or str(uuid.uuid4())
        self.started = time.monotonic()
        self.state = {
            "runId": self.run_id,
            "sessionId": session_id,
            "question": message[:200],
            "status": "running",
            "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "route": None,
            "steps": [],
            "checks": [],
            "totals": {"costUsd": 0.0, "inputTokens": 0, "outputTokens": 0, "toolCalls": 0, "retries": 0, "errors": 0},
        }

    async def set_route(self, route: dict):
        self.state["route"] = route
        await self.save()

    async def step(self, kind: str, name: str, status: str, latency_ms: float, **fields):
        """Records one model call, tool call or pipeline stage, then checkpoints the run."""
        self.state["steps"].append({"kind": kind, "name": name, "status": status, "latencyMs": round(latency_ms), **fields})
        totals = self.state["totals"]
        totals["costUsd"] += fields.get("costUsd", 0)
        totals["inputTokens"] += fields.get("inputTokens", 0)
        totals["outputTokens"] += fields.get("outputTokens", 0)
        totals["retries"] += fields.get("retries", 0)
        totals["toolCalls"] += kind == "tool"
        totals["errors"] += status != "ok"
        await self.save()

    async def check(self, name: str, issues: list):
        self.state["checks"].append({"name": name, "passed": not issues, "issues": issues})
        await self.save()

    async def finish(self, status: str, **fields):
        self.state.update(status=status, latencyMs=round((time.monotonic() - self.started) * 1000), **fields)
        await self.save()

    async def save(self):
        await self.kv.set(f"genie:run:{self.run_id}", json.dumps(self.state, default=str), self.TTL_SEC)

    @staticmethod
    async def load(kv, run_id: str):
        raw = await kv.get(f"genie:run:{run_id}")
        return json.loads(raw) if raw else None
