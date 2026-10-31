from app.intent import effective_intent, extract_intent, merge_profile
from app.models import TripProfile


def test_intent_extracts_budget_city_party_and_categories():
    intent = extract_intent("Fun things to do in Lisbon with kids under 100 euros in May, maybe a food tour")
    assert intent.cities == ["Lisbon"]
    assert intent.budget_usd == 100
    assert intent.party == "family"
    assert intent.month == "may"
    assert "food" in intent.categories
    assert {"kids", "budget"}.issubset(intent.constraints)


def test_intent_handles_multi_city_and_word_number_days():
    intent = extract_intent("Plan a nine-day itinerary across Tokyo and Bali for my wife and me")
    assert set(intent.cities) == {"Tokyo", "Bali"}
    assert intent.days == 9 and intent.wants_itinerary
    assert intent.party == "couple"
    assert extract_intent("two weeks in Paris").days == 14
    assert extract_intent("weekend in London").days == 2


def test_aliases_followups_and_date_words():
    assert extract_intent("best pizza in NYC").cities == ["New York"]
    assert extract_intent("swap day 2 for something indoors").follow_up
    assert not extract_intent("what's on in Paris today or Sunday").wants_itinerary


def test_profile_fills_followup_context():
    profile = merge_profile(TripProfile(), extract_intent("Heading to Barcelona with the kids, budget $80"))
    profile = merge_profile(profile, extract_intent("any shows?"))
    effective = effective_intent(extract_intent("any shows?"), profile)
    assert effective.cities == ["Barcelona"]
    assert effective.budget_usd == 80
    assert effective.party == "family"
    assert effective.categories == ["show"]
