import { describe, expect, it } from "vitest";
import { runGenieTurn } from "../src/agent/genie.js";
import type { AgentRunInput, AgentRunner, AgentRunResult } from "../src/agent/runner.js";
import { collector, testDeps } from "./helpers.js";

const turn = (sessionId: string, message: string) => ({ sessionId, message, ip: "10.0.0.1", today: "2026-11-01" });

describe("runGenieTurn (offline runner)", () => {
  it("streams cards and text, then a done event", async () => {
    const deps = await testDeps();
    const c = collector();
    await runGenieTurn(deps, turn("s-1", "Fun things to do in Lisbon with kids under $60"), c.emit);
    const cards = c.events.find((e) => e.type === "cards");
    expect(cards && cards.type === "cards" && cards.activities.every((a) => a.city === "Lisbon" && a.priceFromUsd <= 60)).toBe(true);
    expect(c.text()).toContain("Lisbon");
    expect(c.done()).toMatchObject({ cached: false, routeReason: "standard request" });
  });

  it("asks for the destination when it doesn't know one", async () => {
    const deps = await testDeps();
    const c = collector();
    await runGenieTurn(deps, turn("s-2", "I want adventure!"), c.emit);
    expect(c.text()).toMatch(/where are you heading/i);
    expect(c.events.some((e) => e.type === "cards")).toBe(false);
  });

  it("answers a similar question from the cache in another session", async () => {
    const deps = await testDeps();
    await runGenieTurn(deps, turn("s-3", "Fun things to do in Lisbon with kids under $60"), collector().emit);
    const c = collector();
    await runGenieTurn(deps, turn("s-4", "Lisbon family activities under 60 dollars please"), c.emit);
    expect(c.done()).toMatchObject({ cached: true, routeReason: "answer cache" });
    expect(c.events.some((e) => e.type === "cards")).toBe(true);
  });

  it("routes multi-city plans to the planner and checks the plan", async () => {
    const deps = await testDeps();
    const c = collector();
    await runGenieTurn(deps, turn("s-5", "Plan a 7 day trip to Paris and Rome for a couple"), c.emit);
    expect(c.done()?.routeReason).toContain("2 cities");
    expect(c.events.some((e) => e.type === "itinerary")).toBe(true);
  });

  it("uses the session profile on follow-ups", async () => {
    const deps = await testDeps();
    await runGenieTurn(deps, turn("s-6", "Heading to Tokyo next month"), collector().emit);
    const c = collector();
    await runGenieTurn(deps, turn("s-6", "any shows?"), c.emit);
    const cards = c.events.find((e) => e.type === "cards");
    expect(cards && cards.type === "cards" && cards.activities[0]?.city).toBe("Tokyo");
  });
});

describe("kill switch", () => {
  it("off: answers with the resting message and never runs the agent", async () => {
    let ran = false;
    const deps = await testDeps({ run: async () => ((ran = true), ({} as AgentRunResult)) });
    await deps.killSwitch.set("off");
    const c = collector();
    await runGenieTurn(deps, turn("s-7", "things to do in Rome"), c.emit);
    expect(ran).toBe(false);
    expect(c.text()).toMatch(/resting/);
  });

  it("degraded: no planner and no live tools", async () => {
    let seen: AgentRunInput | null = null;
    let liveError: boolean | undefined;
    const runner: AgentRunner = {
      run: async (input) => {
        seen = input;
        const res = await input.tools.handlers.find_events({ city: "London", from: "2026-11-01", to: "2026-11-03", keyword: undefined }, undefined);
        liveError = res.isError;
        return { text: "ok", costUsd: 0, turns: 1, stoppedEarly: null, answeredBy: input.model };
      },
    };
    const deps = await testDeps(runner);
    await deps.killSwitch.set("degraded");
    await runGenieTurn(deps, turn("s-8", "Plan 10 days across Paris and London"), collector().emit);
    expect(seen!.model).toBe("claude-sonnet-5-5");
    expect(liveError).toBe(true);
  });

  it("aborts a running turn when flipped off mid-turn", async () => {
    const runner: AgentRunner = {
      run: (input) =>
        new Promise((_, reject) => {
          input.abortController.signal.addEventListener("abort", () => reject(new Error("aborted")));
          setTimeout(() => void deps.killSwitch.set("off"), 10);
        }),
    };
    const deps = await testDeps(runner);
    const c = collector();
    await runGenieTurn(deps, turn("s-9", "things to do in Rome"), c.emit);
    expect(c.done()?.stoppedEarly).toBe("aborted");
  });
});

describe("limits and fallback", () => {
  it("passes the step limit, budget and fallback model to the agent", async () => {
    let seen: AgentRunInput | null = null;
    const deps = await testDeps({
      run: async (input) => {
        seen = input;
        return { text: "", costUsd: 0.01, turns: 8, stoppedEarly: "step limit", answeredBy: input.model };
      },
    });
    const c = collector();
    await runGenieTurn(deps, turn("s-10", "things to do in Rome"), c.emit);
    expect(seen).toMatchObject({ model: "claude-sonnet-5-5", fallbackModel: "claude-sonnet-5", maxTurns: 8 });
    expect(seen!.budgetUsd).toBeGreaterThan(0);
    expect(c.done()?.stoppedEarly).toBe("step limit");
    expect(c.text()).toMatch(/stopped early/);
  });

  it("trips the breaker on repeated overloads and then calls the fallback", async () => {
    const models: string[] = [];
    const deps = await testDeps({
      run: async (input) => {
        models.push(input.model);
        throw new Error("529 overloaded_error");
      },
    });
    for (let i = 0; i < 4; i++) await runGenieTurn(deps, turn(`s-11-${i}`, "things to do in Rome"), collector().emit);
    expect(models).toEqual(["claude-sonnet-5-5", "claude-sonnet-5-5", "claude-sonnet-5-5", "claude-sonnet-5"]);
  });

  it("refuses card ids the tools never returned", async () => {
    let rejected: boolean | undefined;
    const deps = await testDeps({
      run: async (input) => {
        const res = await input.tools.handlers.show_activities({ activity_ids: ["rome-made-up-tour"] }, undefined);
        rejected = res.isError;
        return { text: "", costUsd: 0, turns: 1, stoppedEarly: null, answeredBy: input.model };
      },
    });
    const c = collector();
    await runGenieTurn(deps, turn("s-12", "things to do in Rome"), c.emit);
    expect(rejected).toBe(true);
    expect(c.events.some((e) => e.type === "cards")).toBe(false);
  });
});
