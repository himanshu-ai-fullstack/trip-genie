import { describe, expect, it } from "vitest";
import { effectiveIntent, emptyProfile, extractIntent } from "../src/agent/intent.js";
import { intentKey, isCacheable, MemoryAnswerCache, similarityThreshold } from "../src/cache/answerCache.js";
import { HashedEmbedder } from "../src/retrieval/embed.js";

const key = (q: string) => intentKey(effectiveIntent(extractIntent(q), emptyProfile()));

describe("intentKey", () => {
  it("matches paraphrases that ask for the same thing", () => {
    expect(key("Fun things to do in Lisbon with kids under 100 euros in May")).toBe(key("Lisbon family activities, max 90 dollars, May"));
  });

  it("separates different budgets, parties, cities and shapes", () => {
    const base = key("things to do in Lisbon with kids under $60");
    expect(key("things to do in Lisbon with kids under $500")).not.toBe(base);
    expect(key("things to do in Lisbon for a couple under $60")).not.toBe(base);
    expect(key("things to do in Porto with kids under $60")).not.toBe(base);
    expect(key("3 day plan for Lisbon with kids under $60")).not.toBe(base);
  });

  it("never caches follow-ups or questions without a destination", () => {
    expect(isCacheable(extractIntent("swap day 2 please"))).toBe(false);
    expect(isCacheable(extractIntent("what should I do?"))).toBe(false);
    expect(isCacheable(extractIntent("things to do in Tokyo"))).toBe(true);
  });
});

describe("MemoryAnswerCache", () => {
  it("hits on a similar question under the same key and misses on another catalog version", async () => {
    const embedder = new HashedEmbedder();
    const cache = new MemoryAnswerCache();
    const [a] = await embedder.embed(["best shows in London for a couple"]);
    const [b] = await embedder.embed(["London shows for a couple, what are the best ones"]);
    await cache.store("k", "q", a!, { events: [{ type: "text", delta: "hi" }], model: "claude-sonnet-5-5" }, "v1");
    expect(await cache.lookup("k", b!, "v1", similarityThreshold(embedder))).not.toBeNull();
    expect(await cache.lookup("k", b!, "v2", similarityThreshold(embedder))).toBeNull();
    expect(await cache.lookup("other", b!, "v1", similarityThreshold(embedder))).toBeNull();
  });

  it("expires entries after the TTL", async () => {
    let now = 0;
    const cache = new MemoryAnswerCache(() => now);
    await cache.store("k", "q", [1, 0], { events: [], model: "claude-sonnet-5-5" }, "v1");
    now = 25 * 3600 * 1000;
    expect(await cache.lookup("k", [1, 0], "v1", 0.5)).toBeNull();
  });
});
