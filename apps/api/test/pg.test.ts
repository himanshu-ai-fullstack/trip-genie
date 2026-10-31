import pg from "pg";
import { afterAll, describe, expect, it } from "vitest";
import { PgAnswerCache } from "../src/cache/answerCache.js";
import { HashedEmbedder } from "../src/retrieval/embed.js";
import { PgActivityIndex } from "../src/retrieval/pgIndex.js";
import { migrate } from "../src/store/migrate.js";
import { seed } from "../src/store/seed.js";

// Runs only when DATABASE_URL points at a Postgres with pgvector (CI starts one).
const url = process.env.DATABASE_URL;
const pool = url ? new pg.Pool({ connectionString: url }) : null;

describe.skipIf(!pool)("Postgres backends", () => {
  afterAll(() => pool?.end());

  it("seeds, searches with hybrid ranking and filters", async () => {
    await migrate(pool!);
    await seed(pool!);
    const index = new PgActivityIndex(pool!, new HashedEmbedder());
    const [top] = await index.search({ city: "lisbon", text: "fado music dinner" });
    expect(top?.activity.name).toBe("Fado dinner show");
    const cheap = await index.search({ city: "Dubai", text: "thrill", maxPriceUsd: 100 });
    expect(cheap.every((r) => r.activity.priceFromUsd <= 100)).toBe(true);
  });

  it("stores and finds cached answers by key, version and similarity", async () => {
    const index = new PgActivityIndex(pool!, new HashedEmbedder());
    const version = await index.version();
    const cache = new PgAnswerCache(pool!);
    const [q] = await new HashedEmbedder().embed(["best shows in London"]);
    await cache.store("pg-test", "best shows in London", q!, { events: [{ type: "text", delta: "x" }], model: "claude-sonnet-5-5" }, version);
    expect(await cache.lookup("pg-test", q!, version, 0.3)).toMatchObject({ question: "best shows in London" });
    expect(await cache.lookup("pg-test", q!, "old-version", 0.3)).toBeNull();
  });
});
