import { mkdir, writeFile } from "node:fs/promises";
import { runGenieTurn } from "../src/agent/genie.js";
import { effectiveIntent, emptyProfile, extractIntent } from "../src/agent/intent.js";
import { AgentSdkRunner } from "../src/agent/runner.js";
import { OfflineRunner } from "../src/agent/offlineRunner.js";
import { routeModel } from "../src/agent/router.js";
import { intentKey, isCacheable, similarityThreshold } from "../src/cache/answerCache.js";
import { ACTIVITIES } from "../src/data/activities.js";
import { cosine, createEmbedder } from "../src/retrieval/embed.js";
import { MemoryActivityIndex } from "../src/retrieval/index.js";
import type { GenieEvent } from "../src/types.js";
import { testDeps } from "../test/helpers.js";
import { ANSWER_CASES, CACHE_PAIRS, RETRIEVAL_CASES, ROUTER_CASES } from "./cases.js";

// Each suite has a floor; CI fails when a score drops below it.
const FLOORS = { hitAt3: 0.9, mrr: 0.75, router: 1, cacheAccuracy: 0.9, cacheFalseHits: 0, answers: 1 };

type Suite = { name: string; score: Record<string, number>; pass: boolean; failures: string[] };

async function retrieval(): Promise<Suite> {
  const index = await MemoryActivityIndex.create(ACTIVITIES, createEmbedder());
  let hits = 0;
  let rr = 0;
  const failures: string[] = [];
  for (const c of RETRIEVAL_CASES) {
    const res = await index.search({ city: c.city, text: c.query, categories: c.categories, maxPriceUsd: c.maxPriceUsd, limit: 10 });
    const ids = res.map((r) => r.activity.id);
    const rank = ids.findIndex((id) => c.relevant.includes(id));
    // Each city has only six seed activities, so hit@3 and MRR say more than recall@10.
    if (rank >= 0 && rank < 3) hits++;
    else failures.push(`${c.city}: "${c.query}" ${rank < 0 ? "missed" : `ranked ${rank + 1}`} ${c.relevant.join(", ")}`);
    rr += rank >= 0 ? 1 / (rank + 1) : 0;
  }
  const hitAt3 = hits / RETRIEVAL_CASES.length;
  const mrr = rr / RETRIEVAL_CASES.length;
  return { name: "retrieval", score: { hitAt3, mrr }, pass: hitAt3 >= FLOORS.hitAt3 && mrr >= FLOORS.mrr, failures };
}

function router(): Suite {
  const failures: string[] = [];
  let correct = 0;
  for (const c of ROUTER_CASES) {
    const r = routeModel({ intent: extractIntent(c.question), previousAnswerRejected: false, plannerDisabled: false });
    if (r.model === c.expect) correct++;
    else failures.push(`"${c.question}" went to ${r.model} (${r.reason}), expected ${c.expect}`);
  }
  const accuracy = correct / ROUTER_CASES.length;
  return { name: "router", score: { accuracy }, pass: accuracy >= FLOORS.router, failures };
}

async function cache(): Promise<Suite> {
  const embedder = createEmbedder();
  const threshold = similarityThreshold(embedder);
  const failures: string[] = [];
  let correct = 0;
  let falseHits = 0;
  for (const p of CACHE_PAIRS) {
    const ia = effectiveIntent(extractIntent(p.a), emptyProfile());
    const ib = effectiveIntent(extractIntent(p.b), emptyProfile());
    const [ea, eb] = await embedder.embed([p.a, p.b]);
    const hit = isCacheable(ia) && isCacheable(ib) && intentKey(ia) === intentKey(ib) && cosine(ea!, eb!) >= threshold;
    if (hit === p.same) correct++;
    else failures.push(`${p.same ? "missed" : "false hit"}: "${p.a}" vs "${p.b}" (sim ${cosine(ea!, eb!).toFixed(2)})`);
    if (hit && !p.same) falseHits++;
  }
  const accuracy = correct / CACHE_PAIRS.length;
  return {
    name: "cache",
    score: { accuracy, falseHits, threshold },
    pass: accuracy >= FLOORS.cacheAccuracy && falseHits <= FLOORS.cacheFalseHits,
    failures,
  };
}

/** Rule checks on whole answers: right city, within budget, links only from tools, plans when asked. */
async function answers(live: boolean): Promise<Suite> {
  const deps = await testDeps(live ? new AgentSdkRunner() : new OfflineRunner());
  const catalogUrls = new Set(ACTIVITIES.map((a) => a.bookingUrl));
  const failures: string[] = [];
  let passed = 0;
  for (const [i, c] of ANSWER_CASES.entries()) {
    const events: GenieEvent[] = [];
    await runGenieTurn(deps, { sessionId: `eval-${i}`, message: c.question, ip: `eval-${i}` }, (e) => events.push(e));
    const shown = events.flatMap((e) => (e.type === "cards" || e.type === "itinerary" ? e.activities : []));
    const text = events.flatMap((e) => (e.type === "text" ? [e.delta] : [])).join("");
    const problems: string[] = [];
    if (events.some((e) => e.type === "error")) problems.push("errored");
    if (shown.length === 0) problems.push("showed no activities");
    if (shown.some((a) => a.city !== c.city)) problems.push("showed another city");
    if (c.maxPriceUsd !== undefined && shown.some((a) => a.priceFromUsd > c.maxPriceUsd!)) problems.push("over budget");
    if (shown.some((a) => !catalogUrls.has(a.bookingUrl) && !a.provider.startsWith("mock") && a.provider !== "ticketmaster")) problems.push("unknown link");
    if (/https?:\/\//.test(text)) problems.push("wrote a link in the text");
    if (c.wantsPlan && !events.some((e) => e.type === "itinerary")) problems.push("no day plan");
    if (problems.length) failures.push(`"${c.question}": ${problems.join(", ")}`);
    else passed++;
  }
  const rate = passed / ANSWER_CASES.length;
  return { name: live ? "answers (live agent)" : "answers (offline agent)", score: { passRate: rate }, pass: rate >= FLOORS.answers, failures };
}

const live = process.argv.includes("--live");
const runs = Number(process.env.EVAL_RUNS ?? 1);
const suites: Suite[] = [await retrieval(), router(), await cache()];
for (let r = 0; r < (live ? runs : 1); r++) suites.push(await answers(live));

for (const s of suites) {
  const scores = Object.entries(s.score)
    .map(([k, v]) => `${k}=${Number.isInteger(v) ? v : v.toFixed(3)}`)
    .join("  ");
  console.log(`${s.pass ? "PASS" : "FAIL"}  ${s.name.padEnd(24)} ${scores}`);
  for (const f of s.failures) console.log(`      - ${f}`);
}
await mkdir(new URL("./results/", import.meta.url), { recursive: true });
await writeFile(new URL(`./results/${Date.now()}.json`, import.meta.url), JSON.stringify(suites, null, 2));
process.exit(suites.every((s) => s.pass) ? 0 : 1);
