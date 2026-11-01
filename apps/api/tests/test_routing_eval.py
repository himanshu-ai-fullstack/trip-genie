from evals.run_routing import evaluate


def test_router_matches_every_labeled_case():
    report = evaluate()
    assert report["misses"] == [], report["misses"]
    assert report["cases"] >= 25
