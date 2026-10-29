import { Redis } from "ioredis";

/** The small key-value surface Genie needs: flags, counters, sessions, tool cache. */
export interface KV {
  get(key: string): Promise<string | null>;
  set(key: string, value: string, ttlSec?: number): Promise<void>;
  /** Increment a counter, starting its TTL on first write. Returns the new value. */
  incrBy(key: string, amount: number, ttlSec: number): Promise<number>;
  close(): Promise<void>;
}

export class MemoryKV implements KV {
  private data = new Map<string, { value: string; expiresAt: number | null }>();

  constructor(private readonly now: () => number = Date.now) {}

  async get(key: string): Promise<string | null> {
    const e = this.data.get(key);
    if (!e) return null;
    if (e.expiresAt !== null && e.expiresAt <= this.now()) {
      this.data.delete(key);
      return null;
    }
    return e.value;
  }

  async set(key: string, value: string, ttlSec?: number): Promise<void> {
    this.data.set(key, { value, expiresAt: ttlSec ? this.now() + ttlSec * 1000 : null });
  }

  async incrBy(key: string, amount: number, ttlSec: number): Promise<number> {
    const current = await this.get(key);
    const next = Number(current ?? 0) + amount;
    const existing = this.data.get(key);
    this.data.set(key, { value: String(next), expiresAt: existing?.expiresAt ?? this.now() + ttlSec * 1000 });
    return next;
  }

  async close(): Promise<void> {}
}

export class RedisKV implements KV {
  private readonly redis: Redis;

  constructor(url: string) {
    this.redis = new Redis(url, { maxRetriesPerRequest: 2 });
  }

  async get(key: string): Promise<string | null> {
    return this.redis.get(key);
  }

  async set(key: string, value: string, ttlSec?: number): Promise<void> {
    if (ttlSec) await this.redis.set(key, value, "EX", ttlSec);
    else await this.redis.set(key, value);
  }

  async incrBy(key: string, amount: number, ttlSec: number): Promise<number> {
    const [[, value]] = (await this.redis
      .multi()
      .incrbyfloat(key, amount)
      .expire(key, ttlSec, "NX")
      .exec()) as [[Error | null, string], unknown];
    return Number(value);
  }

  async close(): Promise<void> {
    await this.redis.quit();
  }
}
