"use client";

import { useEffect, useRef, useState } from "react";
import { ActivityCard } from "./components/ActivityCard";
import { Itinerary } from "./components/Itinerary";
import { readEvents } from "./sse";
import type { Turn } from "./types";

const SUGGESTIONS = [
  "Fun things to do in Lisbon with kids under $60",
  "Best shows and nightlife in London this weekend",
  "Adventure activities in Bali for a group of friends",
  "Plan a 7 day trip to Paris and Rome for a couple",
];

const SESSION_KEY = "trip-genie-session";

function loadSession(): string | undefined {
  try {
    return localStorage.getItem(SESSION_KEY) ?? undefined;
  } catch {
    return undefined;
  }
}

function saveSession(id: string) {
  try {
    localStorage.setItem(SESSION_KEY, id);
  } catch {
    // Private mode or blocked storage: the chat still works, it just won't survive a reload.
  }
}

export default function Home() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [sessionId, setSessionId] = useState<string | undefined>();
  const abortRef = useRef<AbortController | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);
  const busy = turns.some((t) => t.streaming);

  useEffect(() => {
    setSessionId(loadSession());
  }, []);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  const update = (id: string, patch: (t: Turn) => Partial<Turn>) =>
    setTurns((all) => all.map((t) => (t.id === id ? { ...t, ...patch(t) } : t)));

  async function ask(question: string) {
    if (!question.trim() || busy) return;
    const id = crypto.randomUUID();
    setInput("");
    setTurns((all) => [
      ...all,
      { id, question, text: "", status: "Thinking", cards: [], itinerary: null, meta: null, error: null, streaming: true },
    ]);

    const abort = new AbortController();
    abortRef.current = abort;
    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ message: question, sessionId }),
        signal: abort.signal,
      });
      if (!res.ok || !res.body) throw new Error(`Request failed (${res.status})`);
      for await (const ev of readEvents(res.body)) {
        switch (ev.type) {
          case "session":
            setSessionId(ev.sessionId);
            saveSession(ev.sessionId);
            break;
          case "status":
            update(id, () => ({ status: ev.message }));
            break;
          case "text":
            update(id, (t) => ({ text: t.text + ev.delta, status: null }));
            break;
          case "cards":
            update(id, (t) => ({ cards: [...t.cards, ...ev.activities], status: null }));
            break;
          case "itinerary":
            update(id, () => ({ itinerary: { days: ev.days, activities: ev.activities }, status: null }));
            break;
          case "done":
            update(id, () => ({ meta: ev }));
            break;
          case "error":
            update(id, () => ({ error: ev.message }));
            break;
        }
      }
    } catch (err) {
      if (!abort.signal.aborted) update(id, () => ({ error: err instanceof Error ? err.message : "Something went wrong" }));
    } finally {
      update(id, (t) => ({ streaming: false, status: null, text: abort.signal.aborted && !t.text ? "Stopped." : t.text }));
      abortRef.current = null;
    }
  }

  function newTrip() {
    abortRef.current?.abort();
    setTurns([]);
    setSessionId(undefined);
    try {
      localStorage.removeItem(SESSION_KEY);
    } catch {}
  }

  return (
    <main>
      <header>
        <div className="brand">
          <span className="lamp" aria-hidden>
            ✦
          </span>
          Trip Genie
        </div>
        <button className="ghost" onClick={newTrip}>
          New trip
        </button>
      </header>

      <div className="thread">
        {turns.length === 0 && (
          <section className="welcome">
            <h1>Where are you headed?</h1>
            <p>Tell me the city, your budget and who's coming. I'll find sightseeing, adventures, shows and food, with links to book.</p>
            <div className="suggestions">
              {SUGGESTIONS.map((s) => (
                <button key={s} onClick={() => ask(s)}>
                  {s}
                </button>
              ))}
            </div>
          </section>
        )}

        {turns.map((t) => (
          <div key={t.id} className="turn">
            <div className="bubble user">{t.question}</div>
            <div className="bubble genie">
              {t.status && <div className="status">{t.status}…</div>}
              {t.text && <p className="answer">{t.text}</p>}
              {t.cards.length > 0 && (
                <div className="cards">
                  {t.cards.map((a, i) => (
                    <ActivityCard key={a.id} activity={a} rank={i + 1} />
                  ))}
                </div>
              )}
              {t.itinerary && <Itinerary days={t.itinerary.days} activities={t.itinerary.activities} />}
              {t.error && <div className="error">{t.error}</div>}
              {t.meta && (
                <div className="meta">
                  {t.meta.cached ? "from cache" : t.meta.model} · {t.meta.routeReason}
                  {t.meta.costUsd > 0 ? ` · $${t.meta.costUsd.toFixed(4)}` : ""}
                  {t.meta.stoppedEarly ? ` · stopped: ${t.meta.stoppedEarly}` : ""}
                </div>
              )}
            </div>
          </div>
        ))}
        <div ref={endRef} />
      </div>

      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          ask(input);
        }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask about any city: things to do, shows, a 3 day plan…"
          maxLength={2000}
          aria-label="Your message"
        />
        {busy ? (
          <button type="button" className="stop" onClick={() => abortRef.current?.abort()}>
            Stop
          </button>
        ) : (
          <button type="submit" disabled={!input.trim()}>
            Ask
          </button>
        )}
      </form>
    </main>
  );
}
