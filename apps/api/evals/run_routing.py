"""Routing eval: replays the labeled cases through the router and reports accuracy and a confusion matrix.

Run after any change to intent extraction, routing rules or model tiers:
    python -m evals.run_routing
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from app.intent import extract_intent
from app.policies import route_model

CASES = Path(__file__).with_name("routing_cases.jsonl")


def evaluate(path: Path = CASES):
    cases = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    confusion: Counter = Counter()
    misses = []
    model_hits = 0
    for case in cases:
        route = route_model(extract_intent(case["message"]), case.get("rejected", False), message=case["message"])
        confusion[(case["task"], route["task"])] += 1
        model_hits += route["model"] == case["model"]
        if route["task"] != case["task"] or route["model"] != case["model"]:
            misses.append({**case, "got_task": route["task"], "got_model": route["model"], "confidence": route["confidence"]})
    task_hits = sum(count for (want, got), count in confusion.items() if want == got)
    return {"cases": len(cases), "task_accuracy": task_hits / len(cases), "model_accuracy": model_hits / len(cases),
            "confusion": confusion, "misses": misses}


def main():
    report = evaluate()
    labels = sorted({label for pair in report["confusion"] for label in pair})
    print(f"{report['cases']} cases  task accuracy {report['task_accuracy']:.0%}  model accuracy {report['model_accuracy']:.0%}\n")
    print("want \\ got".ljust(12) + "".join(label[:9].rjust(10) for label in labels))
    for want in labels:
        print(want.ljust(12) + "".join(str(report["confusion"][(want, got)] or ".").rjust(10) for got in labels))
    for miss in report["misses"]:
        print(f"\nMISS {miss['message']!r}: want {miss['task']}/{miss['model']}, got {miss['got_task']}/{miss['got_model']} ({miss['confidence']})")
    sys.exit(0 if not report["misses"] else 1)


if __name__ == "__main__":
    main()
