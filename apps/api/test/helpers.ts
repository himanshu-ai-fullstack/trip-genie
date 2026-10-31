import type { GenieDeps } from "../src/agent/genie.js";
import { OfflineRunner } from "../src/agent/offlineRunner.js";
import type { AgentRunner } from "../src/agent/runner.js";
import { MemoryAnswerCache } from "../src/cache/answerCache.js";
import { ACTIVITIES } from "../src/data/activities.js";
import { HaversineRoutesProvider, MockEventsProvider, MockToursProvider } from "../src/providers/index.js";
import { HashedEmbedder } from "../src/retrieval/embed.js";
import { MemoryActivityIndex } from "../src/retrieval/index.js";
import { CircuitBreaker } from "../src/safety/circuitBreaker.js";
import { KillSwitch } from "../src/safety/killSwitch.js";
import { MemoryKV } from "../src/store/kv.js";
import { SessionStore } from "../src/store/sessions.js";
import type { GenieEvent } from "../src/types.js";

export async function testDeps(runner: AgentRunner = new OfflineRunner()): Promise<GenieDeps> {
  const embedder = new HashedEmbedder();
  const kv = new MemoryKV();
  return {
    index: await MemoryActivityIndex.create(ACTIVITIES, embedder),
    cache: new MemoryAnswerCache(),
    kv,
    embedder,
    providers: { events: new MockEventsProvider(), tours: new MockToursProvider(), routes: new HaversineRoutesProvider() },
    sessions: new SessionStore(kv),
    killSwitch: new KillSwitch(kv, "on"),
    breaker: new CircuitBreaker(),
    runner,
  };
}

export function collector() {
  const events: GenieEvent[] = [];
  return {
    events,
    emit: (e: GenieEvent) => events.push(e),
    done: () => events.find((e): e is Extract<GenieEvent, { type: "done" }> => e.type === "done"),
    text: () => events.flatMap((e) => (e.type === "text" ? [e.delta] : [])).join(""),
  };
}
