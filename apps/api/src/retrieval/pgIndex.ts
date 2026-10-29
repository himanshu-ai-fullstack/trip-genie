import type pg from "pg";
import type { Activity, Category, PriceBand } from "../types.js";
import { tokenize, type Embedder } from "./embed.js";
import { boost, RRF_K, type ActivityIndex, type ScoredActivity, type SearchQuery } from "./index.js";

type ActivityRow = {
  id: string;
  name: string;
  city: string;
  country: string;
  lat: number;
  lng: number;
  category: string;
  tags: string[];
  price_from_usd: string;
  price_band: string;
  duration_hours: string;
  rating: string;
  description: string;
  provider: string;
  booking_url: string;
  score?: string;
};

const COLUMNS = "id, name, city, country, lat, lng, category, tags, price_from_usd, price_band, duration_hours, rating, description, provider, booking_url";

function toActivity(r: ActivityRow): Activity {
  return {
    id: r.id,
    name: r.name,
    city: r.city,
    country: r.country,
    lat: r.lat,
    lng: r.lng,
    category: r.category as Category,
    tags: r.tags,
    priceFromUsd: Number(r.price_from_usd),
    priceBand: r.price_band as PriceBand,
    durationHours: Number(r.duration_hours),
    rating: Number(r.rating),
    description: r.description,
    provider: r.provider,
    bookingUrl: r.booking_url,
  };
}

export function toVectorLiteral(v: number[]): string {
  return `[${v.map((x) => x.toFixed(6)).join(",")}]`;
}

/** Hybrid search in Postgres: HNSW vector ranking and full-text ranking fused with RRF in SQL. */
export class PgActivityIndex implements ActivityIndex {
  constructor(
    private readonly pool: pg.Pool,
    private readonly embedder: Embedder,
  ) {}

  async search(q: SearchQuery): Promise<ScoredActivity[]> {
    const [qv] = await this.embedder.embed([q.text]);
    const tsquery = tokenize(q.text).join(" | ");
    const { rows } = await this.pool.query<ActivityRow>(
      `WITH c AS (
         SELECT * FROM activities
         WHERE lower(city) = lower($1)
           AND ($2::text[] IS NULL OR category = ANY($2))
           AND ($3::numeric IS NULL OR price_from_usd <= $3)
       ),
       v AS (
         SELECT id, row_number() OVER (ORDER BY embedding <=> $4::vector) AS r
         FROM c ORDER BY embedding <=> $4::vector LIMIT 50
       ),
       k AS (
         SELECT id, row_number() OVER (ORDER BY ts_rank_cd(tsv, q) DESC) AS r
         FROM c, to_tsquery('english', $5) q
         WHERE $5 <> '' AND tsv @@ q
         LIMIT 50
       )
       SELECT ${COLUMNS.split(", ").map((col) => `c.${col}`).join(", ")},
              COALESCE(1.0 / (${RRF_K} + v.r), 0) + COALESCE(1.0 / (${RRF_K} + k.r), 0) AS score
       FROM c LEFT JOIN v USING (id) LEFT JOIN k USING (id)
       ORDER BY score DESC
       LIMIT 30`,
      [q.city, q.categories?.length ? q.categories : null, q.maxPriceUsd ?? null, toVectorLiteral(qv ?? []), tsquery],
    );
    return rows
      .map((r) => {
        const activity = toActivity(r);
        return { activity, score: Number(r.score ?? 0) + boost(activity, q) };
      })
      .sort((a, b) => b.score - a.score)
      .slice(0, q.limit ?? 10);
  }

  async get(ids: string[]): Promise<Activity[]> {
    const { rows } = await this.pool.query<ActivityRow>(`SELECT ${COLUMNS} FROM activities WHERE id = ANY($1)`, [ids]);
    return rows.map(toActivity);
  }

  async cities(): Promise<string[]> {
    const { rows } = await this.pool.query<{ city: string }>("SELECT DISTINCT city FROM activities ORDER BY city");
    return rows.map((r) => r.city);
  }

  async version(): Promise<string> {
    const { rows } = await this.pool.query<{ value: string }>("SELECT value FROM catalog_meta WHERE key = 'catalog_version'");
    return rows[0]?.value ?? "unknown";
  }
}
