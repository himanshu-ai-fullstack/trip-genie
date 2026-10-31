import { emptyProfile } from "../agent/intent.js";
import type { ModelId, TripProfile } from "../types.js";
import type { KV } from "./kv.js";

export type Session = {
  id: string;
  profile: TripProfile;
  history: { role: "user" | "assistant"; text: string }[];
  lastModel: ModelId | null;
};

const TTL_SEC = 7 * 24 * 3600;
const MAX_HISTORY = 20;

export class SessionStore {
  constructor(private readonly kv: KV) {}

  async load(id: string): Promise<Session> {
    const raw = await this.kv.get(`genie:session:${id}`);
    if (raw) return JSON.parse(raw) as Session;
    return { id, profile: emptyProfile(), history: [], lastModel: null };
  }

  async save(session: Session): Promise<void> {
    session.history = session.history.slice(-MAX_HISTORY);
    await this.kv.set(`genie:session:${session.id}`, JSON.stringify(session), TTL_SEC);
  }
}
