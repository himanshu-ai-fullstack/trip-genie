import { randomUUID } from "node:crypto";
import Fastify, { type FastifyInstance } from "fastify";
import { z } from "zod";
import { runGenieTurn, type GenieDeps } from "./agent/genie.js";
import type { KillSwitchMode } from "./safety/killSwitch.js";
import type { GenieEvent } from "./types.js";

const ChatBody = z.object({
  sessionId: z.string().min(8).max(64).optional(),
  message: z.string().min(1).max(4000),
});

const KillSwitchBody = z.object({ mode: z.enum(["on", "degraded", "off"]) });

export function buildServer(deps: GenieDeps, opts: { adminToken?: string } = {}): FastifyInstance {
  const app = Fastify({ logger: false, trustProxy: true });

  app.get("/api/health", async () => ({ ok: true, killSwitch: await deps.killSwitch.mode() }));

  /** One traveler turn, streamed as Server-Sent Events. */
  app.post("/api/chat", async (req, reply) => {
    const parsed = ChatBody.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ error: "Send { message, sessionId? }." });
    const sessionId = parsed.data.sessionId ?? randomUUID();

    reply.hijack();
    const res = reply.raw;
    res.writeHead(200, {
      "content-type": "text/event-stream; charset=utf-8",
      "cache-control": "no-cache, no-transform",
      connection: "keep-alive",
      "x-accel-buffering": "no",
      "x-session-id": sessionId,
    });

    // The traveler's Stop button closes the request; that aborts the agent run.
    const abort = new AbortController();
    res.on("close", () => abort.abort());

    const send = (event: GenieEvent | { type: "session"; sessionId: string }) => {
      if (!res.writableEnded) res.write(`event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`);
    };
    send({ type: "session", sessionId });
    try {
      await runGenieTurn(deps, { sessionId, message: parsed.data.message, ip: req.ip, signal: abort.signal }, send);
    } catch (err) {
      deps.log?.({ event: "unhandled", error: err instanceof Error ? err.message : String(err) });
      send({ type: "error", message: "Genie hit a problem answering that. Please try again." });
    } finally {
      res.end();
    }
  });

  /** Global kill switch. Needs the admin token. */
  app.post("/api/admin/kill-switch", async (req, reply) => {
    if (!opts.adminToken || req.headers.authorization !== `Bearer ${opts.adminToken}`) {
      return reply.code(401).send({ error: "unauthorized" });
    }
    const parsed = KillSwitchBody.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ error: "mode must be on, degraded or off" });
    await deps.killSwitch.set(parsed.data.mode as KillSwitchMode);
    deps.log?.({ event: "kill_switch", mode: parsed.data.mode });
    return { mode: parsed.data.mode };
  });

  return app;
}
