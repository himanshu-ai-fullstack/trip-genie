import type { KV } from "../store/kv.js";

export const LIMITS = {
  /** Turns per IP per minute. */
  ipPerMinute: Number(process.env.GENIE_IP_PER_MINUTE ?? 20),
  /** Turns per session per hour. */
  sessionPerHour: Number(process.env.GENIE_SESSION_PER_HOUR ?? 60),
  /** Dollar ceiling for one turn, enforced by the Agent SDK (maxBudgetUsd). */
  turnBudgetUsd: Number(process.env.GENIE_TURN_BUDGET_USD ?? 0.5),
  /** Dollar ceiling for one session per day. Past it the session is blocked. */
  sessionBudgetUsd: Number(process.env.GENIE_SESSION_BUDGET_USD ?? 3),
  /** Wall-clock ceiling for one turn. */
  turnTimeoutMs: Number(process.env.GENIE_TURN_TIMEOUT_MS ?? 90_000),
  maxQuestionChars: 2_000,
};

export type LimitCheck = { ok: true } | { ok: false; reason: string };

export async function checkRateLimits(kv: KV, ip: string, sessionId: string): Promise<LimitCheck> {
  const minute = Math.floor(Date.now() / 60_000);
  const ipCount = await kv.incrBy(`genie:rl:ip:${ip}:${minute}`, 1, 70);
  if (ipCount > LIMITS.ipPerMinute) return { ok: false, reason: "Too many requests. Please wait a minute." };
  const sessionCount = await kv.incrBy(`genie:rl:session:${sessionId}`, 1, 3600);
  if (sessionCount > LIMITS.sessionPerHour) return { ok: false, reason: "This chat has hit its hourly limit." };
  return { ok: true };
}

export async function checkSessionBudget(kv: KV, sessionId: string): Promise<LimitCheck> {
  const spent = Number((await kv.get(`genie:spend:${sessionId}`)) ?? 0);
  if (spent >= LIMITS.sessionBudgetUsd) return { ok: false, reason: "This chat has reached its daily limit." };
  return { ok: true };
}

export async function recordSpend(kv: KV, sessionId: string, usd: number): Promise<void> {
  if (usd > 0) await kv.incrBy(`genie:spend:${sessionId}`, usd, 86_400);
}
