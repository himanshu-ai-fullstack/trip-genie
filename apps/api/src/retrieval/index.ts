import type { Activity, Category, Party } from "../types.js";
import { cosine, tokenize, type Embedder } from "./embed.js";

export type SearchQuery = {
  city: string;
  text: string;
  categories?: Category[];
  maxPriceUsd?: number | null;
  party?: Party | null;
  limit?: number;
};

export type ScoredActivity = { activity: Activity; score: number };

export interface ActivityIndex {
  search(q: SearchQuery): Promise<ScoredActivity[]>;
  get(ids: string[]): Promise<Activity[]>;
  cities(): Promise<string[]>;
  /** Changes whenever the catalog is refreshed, so cached answers built on the old catalog retire. */
  version(): Promise<string>;
}

export const RRF_K = 60;

/** Reciprocal rank fusion of several ranked id lists. */
export function rrf(lists: string[][], k = RRF_K): Map<string, number> {
  const scores = new Map<string, number>();
  for (const list of lists) {
    list.forEach((id, rank) => scores.set(id, (scores.get(id) ?? 0) + 1 / (k + rank + 1)));
  }
  return scores;
}

export function activityText(a: Activity): string {
  return `${a.name}. ${a.category}. ${a.tags.join(" ")}. ${a.description}`;
}

/** Soft boost applied after fusion: rating, and a family nudge for family-tagged activities. */
export function boost(a: Activity, q: SearchQuery): number {
  let b = (a.rating - 4) * 0.004;
  if (q.party === "family" && a.tags.includes("family")) b += 0.006;
  if (q.party === "couple" && a.tags.includes("romantic")) b += 0.004;
  if (q.party === "family" && a.tags.includes("adults")) b -= 0.02;
  return b;
}

/**
 * In-process hybrid search: hard filters, then vector and keyword rankings
 * fused with RRF. Same algorithm as the Postgres index, used by tests, evals
 * and when DATABASE_URL is unset.
 */
export class MemoryActivityIndex implements ActivityIndex {
  private vectors = new Map<string, number[]>();
  private tokens = new Map<string, Set<string>>();
  private docFreq = new Map<string, number>();

  private constructor(
    private readonly activities: Activity[],
    private readonly embedder: Embedder,
    private readonly catalogVersion: string,
  ) {}

  static async create(activities: Activity[], embedder: Embedder, version = "seed-v1"): Promise<MemoryActivityIndex> {
    const index = new MemoryActivityIndex(activities, embedder, version);
    const vectors = await embedder.embed(activities.map(activityText));
    activities.forEach((a, i) => {
      index.vectors.set(a.id, vectors[i] ?? []);
      const toks = new Set(tokenize(activityText(a)));
      index.tokens.set(a.id, toks);
      for (const t of toks) index.docFreq.set(t, (index.docFreq.get(t) ?? 0) + 1);
    });
    return index;
  }

  async search(q: SearchQuery): Promise<ScoredActivity[]> {
    const limit = q.limit ?? 10;
    const candidates = this.activities.filter(
      (a) =>
        a.city.toLowerCase() === q.city.toLowerCase() &&
        (!q.categories?.length || q.categories.includes(a.category)) &&
        (q.maxPriceUsd == null || a.priceFromUsd <= q.maxPriceUsd),
    );
    if (candidates.length === 0) return [];

    const [qv] = await this.embedder.embed([q.text]);
    const byVector = [...candidates]
      .sort((a, b) => cosine(qv ?? [], this.vectors.get(b.id) ?? []) - cosine(qv ?? [], this.vectors.get(a.id) ?? []))
      .map((a) => a.id);

    const qTokens = tokenize(q.text);
    const n = this.activities.length;
    const keywordScore = (a: Activity) =>
      qTokens.reduce((s, t) => {
        if (!this.tokens.get(a.id)?.has(t)) return s;
        return s + Math.log(1 + n / (this.docFreq.get(t) ?? 1));
      }, 0);
    const byKeyword = candidates
      .map((a) => ({ id: a.id, s: keywordScore(a) }))
      .filter((x) => x.s > 0)
      .sort((a, b) => b.s - a.s)
      .map((x) => x.id);

    const fused = rrf([byVector, byKeyword]);
    return candidates
      .map((a) => ({ activity: a, score: (fused.get(a.id) ?? 0) + boost(a, q) }))
      .sort((a, b) => b.score - a.score)
      .slice(0, limit);
  }

  async get(ids: string[]): Promise<Activity[]> {
    const wanted = new Set(ids);
    return this.activities.filter((a) => wanted.has(a.id));
  }

  async cities(): Promise<string[]> {
    return [...new Set(this.activities.map((a) => a.city))];
  }

  async version(): Promise<string> {
    return this.catalogVersion;
  }
}
