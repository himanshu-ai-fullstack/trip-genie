import pg from "pg";
import { MemoryAnswerCache, PgAnswerCache, type AnswerCache } from "./cache/answerCache.js";
import type { GenieDeps } from "./agent/genie.js";
import { OfflineRunner } from "./agent/offlineRunner.js";
import { AgentSdkRunner } from "./agent/runner.js";
import { ACTIVITIES } from "./data/activities.js";
import { createProviders } from "./providers/index.js";
import { createEmbedder } from "./retrieval/embed.js";
import { MemoryActivityIndex, type ActivityIndex } from "./retrieval/index.js";
import { PgActivityIndex } from "./retrieval/pgIndex.js";
import { CircuitBreaker } from "./safety/circuitBreaker.js";
import { KillSwitch } from "./safety/killSwitch.js";
import { MemoryKV, RedisKV, type KV } from "./store/kv.js";
import { migrate } from "./store/migrate.js";
import { seed } from "./store/seed.js";
import { SessionStore } from "./store/sessions.js";

/** Wires the real or in-memory backends from the environment. */
export async function createDeps(env = process.env): Promise<GenieDeps & { close: () => Promise<void> }> {
  const embedder = createEmbedder();
  const kv: KV = env.REDIS_URL ? new RedisKV(env.REDIS_URL) : new MemoryKV();

  let index: ActivityIndex;
  let cache: AnswerCache;
  let pool: pg.Pool | null = null;
  if (env.DATABASE_URL) {
    pool = new pg.Pool({ connectionString: env.DATABASE_URL, max: 10 });
    // First boot: create the schema and load the seed catalog.
    await migrate(pool);
    const { rows } = await pool.query<{ n: string }>("SELECT count(*) AS n FROM activities");
    if (Number(rows[0]?.n ?? 0) === 0) await seed(pool);
    index = new PgActivityIndex(pool, embedder);
    cache = new PgAnswerCache(pool);
  } else {
    index = await MemoryActivityIndex.create(ACTIVITIES, embedder);
    cache = new MemoryAnswerCache();
  }

  return {
    index,
    cache,
    kv,
    embedder,
    providers: createProviders(),
    sessions: new SessionStore(kv),
    killSwitch: new KillSwitch(kv),
    breaker: new CircuitBreaker(),
    runner: env.GENIE_OFFLINE === "1" || !env.ANTHROPIC_API_KEY ? new OfflineRunner() : new AgentSdkRunner(),
    log: (entry) => console.log(JSON.stringify({ t: new Date().toISOString(), ...entry })),
    close: async () => {
      await kv.close();
      await pool?.end();
    },
  };
}
