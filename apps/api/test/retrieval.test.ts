import { describe, expect, it } from "vitest";
import { ACTIVITIES } from "../src/data/activities.js";
import { HashedEmbedder } from "../src/retrieval/embed.js";
import { MemoryActivityIndex, rrf } from "../src/retrieval/index.js";

describe("rrf", () => {
  it("rewards items ranked well in both lists", () => {
    const s = rrf([["a", "b", "c"], ["b", "a"]]);
    expect(s.get("a")).toBeCloseTo(s.get("b")!);
    expect(s.get("c")!).toBeLessThan(s.get("a")!);
  });
});

describe("MemoryActivityIndex", () => {
  it("applies hard filters before ranking", async () => {
    const index = await MemoryActivityIndex.create(ACTIVITIES, new HashedEmbedder());
    const res = await index.search({ city: "dubai", text: "thrill", maxPriceUsd: 100 });
    expect(res.length).toBeGreaterThan(0);
    expect(res.every((r) => r.activity.city === "Dubai" && r.activity.priceFromUsd <= 100)).toBe(true);
    expect(res.map((r) => r.activity.name)).not.toContain("Skydive over the Palm");
  });

  it("ranks the obvious match first", async () => {
    const index = await MemoryActivityIndex.create(ACTIVITIES, new HashedEmbedder());
    const [top] = await index.search({ city: "Lisbon", text: "fado music dinner" });
    expect(top?.activity.name).toBe("Fado dinner show");
  });
});
