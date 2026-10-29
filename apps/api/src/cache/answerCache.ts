import { createHash } from "node:crypto";
import type pg from "pg";
import { cosine, type Embedder } from "../retrieval/embed.js";
import { toVectorLiteral } from "../retrieval/pgIndex.js";
import type { GenieEvent, Intent, ModelId } from "../types.js";

/** What a cache hit replays: the content events of the original answer. */
export type CachedAnswer = {
  events: Extract<GenieEvent, { type: "text" | "cards" | "itinerary" }>[];
  model: ModelId;
};

export type CacheHit = { answer: CachedAnswer; similarity: number; question: string };

export const ANSWER_TTL_SEC = 24 * 3600;

/** Similarity a question needs, inside the same intent key, to reuse an answer. Tuned by the cache eval. */
export function similarityThreshold(embedder: Embedder): number {
  const fromEnv = process.env.GENIE_CACHE_THRESHOLD;
  if (fromEnv) return Number(fromEnv);
  return embedder.name.startsWith("hashed") ? 0.3 : 0.85;
}

function budgetBand(usd: number | null): string {
  if (usd === null) return "any";
  if (usd <= 30) return "low";
  if (usd <= 100) return "mid";
  if (usd <= 250) return "high";
  return "lux";
}

/**
 * The exact part of the key. Two questions can share an answer only if they
 * agree on every field that changes the answer: places, interests, budget
 * band, month, party, constraints, and list versus day plan.
 */
export function intentKey(intent: Intent): string {
  const parts = {
    cities: [...intent.cities].sort(),
    categories: [...intent.categories].sort(),
    budget: budgetBand(intent.budgetUsd),
    month: intent.month ?? "any",
    party: intent.party ?? "any",
    // "budget" is covered by the band and "kids" by the party.
    constraints: intent.constraints.filter((c) => c !== "budget" && c !== "kids").sort(),
    shape: intent.wantsItinerary ? `plan-${intent.days ?? "?"}` : "list",
  };
  return createHash("sha256").update(JSON.stringify(parts)).digest("hex").slice(0, 32);
}

/**
 * Follow-ups depend on earlier turns, and a question with no destination has
 * nothing stable to key on, so neither is cached.
 */
export function isCacheable(intent: Intent): boolean {
  return !intent.followUp && intent.cities.length > 0;
}

export interface AnswerCache {
  lookup(key: string, embedding: number[], catalogVersion: string, threshold: number): Promise<CacheHit | null>;
  store(key: string, question: string, embedding: number[], answer: CachedAnswer, catalogVersion: string): Promise<void>;
}

type Entry = { key: string; question: string; embedding: number[]; answer: CachedAnswer; version: string; expiresAt: number; hits: number };

export class MemoryAnswerCache implements AnswerCache {
  private entries: Entry[] = [];

  constructor(private readonly now: () => number = Date.now) {}

  async lookup(key: string, embedding: number[], catalogVersion: string, threshold: number): Promise<CacheHit | null> {
    const t = this.now();
    this.entries = this.entries.filter((e) => e.expiresAt > t);
    let best: Entry | null = null;
    let bestSim = -1;
    for (const e of this.entries) {
      if (e.key !== key || e.version !== catalogVersion) continue;
      const sim = cosine(embedding, e.embedding);
      if (sim > bestSim) {
        best = e;
        bestSim = sim;
      }
    }
    if (!best || bestSim < threshold) return null;
    best.hits++;
    return { answer: best.answer, similarity: bestSim, question: best.question };
  }

  async store(key: string, question: string, embedding: number[], answer: CachedAnswer, catalogVersion: string): Promise<void> {
    this.entries.push({ key, question, embedding, answer, version: catalogVersion, expiresAt: this.now() + ANSWER_TTL_SEC * 1000, hits: 0 });
  }
}

export class PgAnswerCache implements AnswerCache {
  constructor(private readonly pool: pg.Pool) {}

  async lookup(key: string, embedding: number[], catalogVersion: string, threshold: number): Promise<CacheHit | null> {
    const { rows } = await this.pool.query<{ id: string; question: string; answer: CachedAnswer; sim: number }>(
      `SELECT id, question, answer, 1 - (embedding <=> $3::vector) AS sim
       FROM answer_cache
       WHERE intent_key = $1 AND catalog_version = $2 AND expires_at > now()
       ORDER BY embedding <=> $3::vector
       LIMIT 1`,
      [key, catalogVersion, toVectorLiteral(embedding)],
    );
    const row = rows[0];
    if (!row || row.sim < threshold) return null;
    await this.pool.query("UPDATE answer_cache SET hits = hits + 1 WHERE id = $1", [row.id]);
    return { answer: row.answer, similarity: row.sim, question: row.question };
  }

  async store(key: string, question: string, embedding: number[], answer: CachedAnswer, catalogVersion: string): Promise<void> {
    await this.pool.query(
      `INSERT INTO answer_cache (intent_key, question, embedding, answer, model, catalog_version, expires_at)
       VALUES ($1, $2, $3::vector, $4, $5, $6, now() + make_interval(secs => $7))`,
      [key, question, toVectorLiteral(embedding), JSON.stringify(answer), answer.model, catalogVersion, ANSWER_TTL_SEC],
    );
  }
}
