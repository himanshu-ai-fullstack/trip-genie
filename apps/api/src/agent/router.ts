import type { Intent, ModelId } from "../types.js";

export const MODELS = {
  helper: "claude-haiku-5-5",
  standard: "claude-sonnet-5-5",
  planner: "claude-opus-5-5",
} as const satisfies Record<string, ModelId>;

/** Backup for each primary, used when it is overloaded or its breaker is open. */
export const FALLBACK: Partial<Record<ModelId, ModelId>> = {
  "claude-opus-5-5": "claude-sonnet-5-5",
  "claude-sonnet-5-5": "claude-sonnet-5",
};

/** Agent turn ceilings per model. Opus plans need more tool calls. */
export const MAX_TURNS: Partial<Record<ModelId, number>> = {
  "claude-sonnet-5-5": 8,
  "claude-sonnet-5": 8,
  "claude-opus-5-5": 14,
};

export type Route = { model: ModelId; reason: string };

export type RouteInput = {
  intent: Intent;
  /** The traveler pushed back on the last answer or the plan checker failed it. */
  previousAnswerRejected: boolean;
  /** Degraded mode from the kill switch: never route to the planner. */
  plannerDisabled: boolean;
};

const PLANNER_DAYS = 5;
const PLANNER_CONSTRAINTS = 3;

/** Rules first, so every decision has a reason we can log and test. */
export function routeModel({ intent, previousAnswerRejected, plannerDisabled }: RouteInput): Route {
  const reasons: string[] = [];
  if (intent.cities.length >= 2) reasons.push(`${intent.cities.length} cities`);
  if (intent.wantsItinerary && (intent.days ?? 0) >= PLANNER_DAYS) reasons.push(`${intent.days}-day plan`);
  if (intent.constraints.length >= PLANNER_CONSTRAINTS) reasons.push(`${intent.constraints.length} hard constraints`);
  if (previousAnswerRejected) reasons.push("previous answer rejected");

  if (reasons.length === 0) return { model: MODELS.standard, reason: "standard request" };
  if (plannerDisabled) return { model: MODELS.standard, reason: `planner disabled (would route: ${reasons.join(", ")})` };
  return { model: MODELS.planner, reason: reasons.join(", ") };
}

const REJECTION = /\b(doesn'?t work|does not work|not what i|don'?t like|too expensive|wrong|try again|that'?s not|no good|bad plan)\b/i;

export function isRejection(message: string): boolean {
  return REJECTION.test(message);
}
