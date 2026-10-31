import type { KV } from "../store/kv.js";

export const TOOL_TTL_SEC = 15 * 60;

function stableStringify(v: unknown): string {
  if (Array.isArray(v)) return `[${v.map(stableStringify).join(",")}]`;
  if (v && typeof v === "object") {
    return `{${Object.keys(v as object)
      .sort()
      .map((k) => `${JSON.stringify(k)}:${stableStringify((v as Record<string, unknown>)[k])}`)
      .join(",")}}`;
  }
  return JSON.stringify(v);
}

/** Memoize a live provider call for 15 minutes, keyed by provider and normalized arguments. */
export async function cachedCall<T>(kv: KV, provider: string, args: unknown, call: () => Promise<T>): Promise<T> {
  const key = `genie:tool:${provider}:${stableStringify(args).toLowerCase()}`;
  const hit = await kv.get(key);
  if (hit) return JSON.parse(hit) as T;
  const value = await call();
  await kv.set(key, JSON.stringify(value), TOOL_TTL_SEC);
  return value;
}
