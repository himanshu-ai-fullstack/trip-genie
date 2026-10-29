import pg from "pg";
import { ACTIVITIES } from "../data/activities.js";
import { createEmbedder } from "../retrieval/embed.js";
import { activityText } from "../retrieval/index.js";
import { toVectorLiteral } from "../retrieval/pgIndex.js";
import { migrate } from "./migrate.js";

/** Load the seed catalog and bump the catalog version so stale cached answers retire. */
export async function seed(pool: pg.Pool): Promise<string> {
  const embedder = createEmbedder();
  const vectors = await embedder.embed(ACTIVITIES.map(activityText));
  const client = await pool.connect();
  const version = `${embedder.name}-${new Date().toISOString()}`;
  try {
    await client.query("BEGIN");
    for (const [i, a] of ACTIVITIES.entries()) {
      await client.query(
        `INSERT INTO activities (id, name, city, country, lat, lng, category, tags, price_from_usd, price_band,
           duration_hours, rating, description, provider, booking_url, search_text, embedding, refreshed_at)
         VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17::vector, now())
         ON CONFLICT (id) DO UPDATE SET
           name = EXCLUDED.name, category = EXCLUDED.category, tags = EXCLUDED.tags,
           price_from_usd = EXCLUDED.price_from_usd, price_band = EXCLUDED.price_band,
           duration_hours = EXCLUDED.duration_hours, rating = EXCLUDED.rating,
           description = EXCLUDED.description, booking_url = EXCLUDED.booking_url,
           search_text = EXCLUDED.search_text, embedding = EXCLUDED.embedding, refreshed_at = now()`,
        [a.id, a.name, a.city, a.country, a.lat, a.lng, a.category, a.tags, a.priceFromUsd, a.priceBand,
         a.durationHours, a.rating, a.description, a.provider, a.bookingUrl, activityText(a), toVectorLiteral(vectors[i] ?? [])],
      );
    }
    await client.query(
      `INSERT INTO catalog_meta (key, value) VALUES ('catalog_version', $1)
       ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value`,
      [version],
    );
    await client.query("COMMIT");
  } catch (err) {
    await client.query("ROLLBACK");
    throw err;
  } finally {
    client.release();
  }
  return version;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const pool = new pg.Pool({ connectionString: process.env.DATABASE_URL });
  await migrate(pool);
  const version = await seed(pool);
  console.log(`seeded ${ACTIVITIES.length} activities, catalog ${version}`);
  await pool.end();
}
