import { createDeps } from "./app.js";
import { buildServer } from "./server.js";

const deps = await createDeps();
const app = buildServer(deps, { adminToken: process.env.ADMIN_TOKEN });
const port = Number(process.env.PORT ?? 8787);
await app.listen({ port, host: "0.0.0.0" });
console.log(`Genie API on :${port} (${deps.runner.constructor.name}, ${process.env.DATABASE_URL ? "postgres" : "in-memory"} index)`);

for (const sig of ["SIGINT", "SIGTERM"] as const) {
  process.on(sig, async () => {
    await app.close();
    await deps.close();
    process.exit(0);
  });
}
