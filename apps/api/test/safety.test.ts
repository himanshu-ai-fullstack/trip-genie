import { describe, expect, it } from "vitest";
import { CircuitBreaker, isTransientModelError } from "../src/safety/circuitBreaker.js";
import { checkRateLimits, LIMITS } from "../src/safety/limits.js";
import { MemoryKV } from "../src/store/kv.js";

describe("CircuitBreaker", () => {
  it("opens after three transient failures and falls back until the cooldown ends", () => {
    let now = 0;
    const b = new CircuitBreaker(3, 60_000, 300_000, () => now);
    expect(b.resolve("claude-opus-5-5")).toMatchObject({ model: "claude-opus-5-5", tripped: false });
    b.recordFailure("claude-opus-5-5");
    b.recordFailure("claude-opus-5-5");
    b.recordFailure("claude-opus-5-5");
    expect(b.resolve("claude-opus-5-5")).toMatchObject({ model: "claude-sonnet-5-5", fallback: "claude-sonnet-5", tripped: true });
    now = 301_000;
    expect(b.resolve("claude-opus-5-5").tripped).toBe(false);
  });

  it("forgets failures outside the window", () => {
    let now = 0;
    const b = new CircuitBreaker(3, 60_000, 300_000, () => now);
    b.recordFailure("claude-sonnet-5-5");
    b.recordFailure("claude-sonnet-5-5");
    now = 61_000;
    b.recordFailure("claude-sonnet-5-5");
    expect(b.isOpen("claude-sonnet-5-5")).toBe(false);
  });

  it("only counts overload-style errors", () => {
    expect(isTransientModelError(new Error("529 overloaded_error"))).toBe(true);
    expect(isTransientModelError(new Error("400 invalid_request_error"))).toBe(false);
  });
});

describe("rate limits", () => {
  it("blocks an IP after the per-minute limit", async () => {
    const kv = new MemoryKV();
    for (let i = 0; i < LIMITS.ipPerMinute; i++) expect((await checkRateLimits(kv, "1.2.3.4", `s${i}`)).ok).toBe(true);
    expect((await checkRateLimits(kv, "1.2.3.4", "s-last")).ok).toBe(false);
  });
});
