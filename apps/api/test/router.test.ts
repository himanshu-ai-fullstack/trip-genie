import { describe, expect, it } from "vitest";
import { extractIntent } from "../src/agent/intent.js";
import { isRejection, routeModel } from "../src/agent/router.js";

const route = (q: string, opts: Partial<{ previousAnswerRejected: boolean; plannerDisabled: boolean }> = {}) =>
  routeModel({ intent: extractIntent(q), previousAnswerRejected: false, plannerDisabled: false, ...opts });

describe("routeModel", () => {
  it("keeps simple single-city asks on Sonnet", () => {
    expect(route("things to do in Rome").model).toBe("claude-sonnet-5-5");
    expect(route("3 day plan for Lisbon").model).toBe("claude-sonnet-5-5");
  });

  it("sends multi-city, long plans and heavy constraints to Opus", () => {
    expect(route("Paris and London highlights").model).toBe("claude-opus-5-5");
    expect(route("Plan a 7 day trip to Tokyo").reason).toContain("7-day plan");
    expect(route("Dubai with kids, wheelchair access, vegetarian food, under $50").model).toBe("claude-opus-5-5");
  });

  it("escalates after a rejected answer", () => {
    expect(route("things to do in Rome", { previousAnswerRejected: true }).model).toBe("claude-opus-5-5");
    expect(isRejection("that doesn't work for us")).toBe(true);
  });

  it("never picks Opus in degraded mode", () => {
    const r = route("Plan 10 days across Paris and Rome", { plannerDisabled: true });
    expect(r.model).toBe("claude-sonnet-5-5");
    expect(r.reason).toContain("planner disabled");
  });
});
