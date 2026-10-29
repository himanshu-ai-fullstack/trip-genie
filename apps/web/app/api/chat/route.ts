// Same-origin proxy to the Genie API, passing the SSE stream straight through.
// Aborting the browser request aborts the upstream request, which stops the agent.
export const dynamic = "force-dynamic";

const API = process.env.GENIE_API_URL ?? "http://localhost:8787";

export async function POST(req: Request): Promise<Response> {
  const upstream = await fetch(`${API}/api/chat`, {
    method: "POST",
    headers: { "content-type": "application/json", "x-forwarded-for": req.headers.get("x-forwarded-for") ?? "" },
    body: await req.text(),
    signal: req.signal,
  });
  return new Response(upstream.body, {
    status: upstream.status,
    headers: {
      "content-type": upstream.headers.get("content-type") ?? "text/event-stream",
      "cache-control": "no-cache, no-transform",
    },
  });
}
