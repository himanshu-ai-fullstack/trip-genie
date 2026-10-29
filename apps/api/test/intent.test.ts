import { describe, expect, it } from "vitest";
import { effectiveIntent, emptyProfile, extractIntent, mergeProfile } from "../src/agent/intent.js";

describe("extractIntent", () => {
  it("reads city, budget, party and categories", () => {
    const i = extractIntent("Fun things to do in Lisbon with kids under 100 euros in May, maybe a food tour");
    expect(i.cities).toEqual(["Lisbon"]);
    expect(i.budgetUsd).toBe(100);
    expect(i.party).toBe("family");
    expect(i.month).toBe("may");
    expect(i.categories).toContain("food");
    expect(i.constraints).toEqual(expect.arrayContaining(["kids", "budget"]));
  });

  it("detects multi-city day plans", () => {
    const i = extractIntent("Plan a 9-day itinerary across Tokyo and Bali for my wife and me");
    expect(i.cities.sort()).toEqual(["Bali", "Tokyo"]);
    expect(i.days).toBe(9);
    expect(i.wantsItinerary).toBe(true);
    expect(i.party).toBe("couple");
  });

  it("reads word numbers and weeks", () => {
    expect(extractIntent("a half day then seven days in Rome").days).toBe(7);
    expect(extractIntent("two weeks in Paris").days).toBe(14);
    expect(extractIntent("weekend in London").days).toBe(2);
  });

  it("maps aliases and marks follow-ups", () => {
    expect(extractIntent("best pizza in NYC").cities).toEqual(["New York"]);
    expect(extractIntent("swap day 2 for something indoors").followUp).toBe(true);
    expect(extractIntent("what to do in Rome").followUp).toBe(false);
  });

  it("does not treat 'Sunday' or 'today' as a trip length", () => {
    expect(extractIntent("what's on in Paris today or Sunday").days).toBeNull();
  });
});

describe("profiles", () => {
  it("keeps earlier facts and fills the effective intent from them", () => {
    let p = mergeProfile(emptyProfile(), extractIntent("Heading to Barcelona with the kids, budget $80"));
    p = mergeProfile(p, extractIntent("any shows?"));
    const eff = effectiveIntent(extractIntent("any shows?"), p);
    expect(eff.cities).toEqual(["Barcelona"]);
    expect(eff.budgetUsd).toBe(80);
    expect(eff.party).toBe("family");
    expect(eff.categories).toEqual(["show"]);
  });
});
