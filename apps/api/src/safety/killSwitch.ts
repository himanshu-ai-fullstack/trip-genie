import type { KV } from "../store/kv.js";

/**
 * Global kill switch, read from the KV store on every turn so a flip takes
 * effect within one request, with no deploy.
 * - on:       normal service
 * - degraded: no Opus routing and no live provider calls; index and cache answers only
 * - off:      new turns get a resting message and running turns are aborted
 */
export type KillSwitchMode = "on" | "degraded" | "off";

const KEY = "genie:killswitch";

export class KillSwitch {
  private listeners = new Set<(mode: KillSwitchMode) => void>();

  constructor(
    private readonly kv: KV,
    private readonly envDefault: KillSwitchMode = (process.env.GENIE_KILL_SWITCH as KillSwitchMode) || "on",
  ) {}

  async mode(): Promise<KillSwitchMode> {
    const v = (await this.kv.get(KEY)) as KillSwitchMode | null;
    return v ?? this.envDefault;
  }

  async set(mode: KillSwitchMode): Promise<void> {
    await this.kv.set(KEY, mode);
    for (const l of this.listeners) l(mode);
  }

  /** Running turns subscribe so an "off" flip on this instance aborts them at once. */
  onChange(listener: (mode: KillSwitchMode) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }
}

export const RESTING_MESSAGE =
  "Genie is resting for a moment while we do some maintenance. Please try again in a few minutes.";
