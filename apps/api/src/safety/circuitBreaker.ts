import { FALLBACK } from "../agent/router.js";
import type { ModelId } from "../types.js";

type State = { failures: number[]; openUntil: number };

/**
 * Per-model breaker. After `threshold` overload or server errors inside
 * `windowMs`, traffic for that model goes straight to its fallback for
 * `cooldownMs`, so travelers don't each wait through a failing first try.
 */
export class CircuitBreaker {
  private states = new Map<ModelId, State>();

  constructor(
    private readonly threshold = 3,
    private readonly windowMs = 60_000,
    private readonly cooldownMs = 5 * 60_000,
    private readonly now: () => number = Date.now,
  ) {}

  isOpen(model: ModelId): boolean {
    return (this.states.get(model)?.openUntil ?? 0) > this.now();
  }

  recordFailure(model: ModelId): void {
    const t = this.now();
    const s = this.states.get(model) ?? { failures: [], openUntil: 0 };
    s.failures = [...s.failures.filter((f) => t - f < this.windowMs), t];
    if (s.failures.length >= this.threshold) {
      s.openUntil = t + this.cooldownMs;
      s.failures = [];
    }
    this.states.set(model, s);
  }

  recordSuccess(model: ModelId): void {
    const s = this.states.get(model);
    if (s) s.failures = [];
  }

  /** The model to actually call, and the one to fall back to inside the SDK. */
  resolve(primary: ModelId): { model: ModelId; fallback: ModelId | undefined; tripped: boolean } {
    const fallback = FALLBACK[primary];
    if (fallback && this.isOpen(primary)) return { model: fallback, fallback: FALLBACK[fallback], tripped: true };
    return { model: primary, fallback, tripped: false };
  }
}

/** Errors worth tripping the breaker for: overload, rate limit and 5xx, not bad requests. */
export function isTransientModelError(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err);
  return /\b(overloaded|529|500|502|503|504|rate.?limit|429|timeout|ECONNRESET)\b/i.test(msg);
}
