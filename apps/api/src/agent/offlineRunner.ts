import type { Activity } from "../types.js";
import { extractIntent } from "./intent.js";
import type { AgentRunInput, AgentRunner, AgentRunResult } from "./runner.js";

/**
 * A scripted stand-in for the model. It calls the same tool handlers the
 * agent would, so the UI, streaming, cache and evals run end to end with no
 * API key. Set GENIE_OFFLINE=1 to use it.
 */
export class OfflineRunner implements AgentRunner {
  async run(input: AgentRunInput, onText: (delta: string) => void): Promise<AgentRunResult> {
    const say = (s: string) => {
      for (const chunk of s.match(/.{1,24}(\s|$)/g) ?? [s]) onText(chunk);
      text += s;
    };
    let text = "";
    let turns = 0;
    const h = input.tools.handlers;
    const profile = /<trip_profile>([\s\S]*?)<\/trip_profile>/.exec(input.prompt)?.[1] ?? "";
    const cities = (/destination: (.*)/.exec(profile)?.[1] ?? "unknown").split(", ").filter((c) => c !== "unknown");
    const budget = Number(/budget per activity \(USD\): (\d+)/.exec(profile)?.[1] ?? NaN);
    const daysInProfile = Number(/trip length \(days\): (\d+)/.exec(profile)?.[1] ?? NaN);
    const intent = extractIntent(input.message);

    if (cities.length === 0) {
      say("Happy to help! Where are you heading, and roughly what's your budget per activity?");
      return { text, costUsd: 0, turns: 1, stoppedEarly: null, answeredBy: "offline" };
    }

    const results: Activity[] = [];
    for (const city of cities) {
      if (++turns > input.maxTurns) return { text, costUsd: 0, turns, stoppedEarly: "step limit", answeredBy: "offline" };
      const res = await h.search_activities(
        { city, query: input.message, categories: intent.categories.length ? intent.categories : undefined, max_price_usd: Number.isFinite(budget) ? budget : undefined, limit: undefined },
        undefined,
      );
      const parsed = JSON.parse((res.content[0] as { text: string }).text) as { results: { id: string }[] };
      for (const r of parsed.results) results.push({ id: r.id } as Activity);
    }

    if (results.length === 0) {
      say(`I couldn't find matches in ${cities.join(" and ")} for that. Try a higher budget or different interests.`);
      return { text, costUsd: 0, turns, stoppedEarly: null, answeredBy: "offline" };
    }

    const days = intent.days ?? (intent.wantsItinerary && Number.isFinite(daysInProfile) ? daysInProfile : null);
    if (intent.wantsItinerary && days) {
      const plan = await this.planDays(results.map((r) => input.tools.seen.get(r.id)!), cities, days, input);
      turns++;
      const res = await h.show_itinerary({ days: plan }, undefined);
      if (res.isError) {
        say("I put together a rough plan but the timings didn't fit. Ask me for the activity list instead.");
      } else {
        say(`Here's a ${plan.length}-day plan for ${cities.join(" and ")}. Each day mixes a big sight with something local, with time to travel between stops.`);
      }
      return { text, costUsd: 0, turns, stoppedEarly: null, answeredBy: "offline" };
    }

    turns++;
    await h.show_activities({ activity_ids: results.slice(0, 6).map((r) => r.id) }, undefined);
    say(
      `Here are my top picks in ${cities.join(" and ")}${Number.isFinite(budget) ? ` under $${budget}` : ""}. ` +
        "Tap a card to see it and book. Want me to turn these into a day by day plan?",
    );
    return { text, costUsd: 0, turns, stoppedEarly: null, answeredBy: "offline" };
  }

  /** Greedy plan: split days across cities, up to three stops a day, each starting after the last one ends plus travel. */
  private async planDays(activities: Activity[], cities: string[], days: number, input: AgentRunInput) {
    const plan: { day: number; city: string; stops: { activity_id: string; start: string }[] }[] = [];
    const pools = new Map(cities.map((c) => [c, activities.filter((a) => a.city === c)]));
    for (let d = 0; d < days; d++) {
      const city = cities[Math.min(cities.length - 1, Math.floor((d * cities.length) / days))]!;
      const pool = pools.get(city) ?? [];
      const stops: { activity_id: string; start: string }[] = [];
      let clock = 9 * 60;
      let prev: Activity | null = null;
      while (stops.length < 3 && pool.length) {
        const next = pool.shift()!;
        if (prev) clock += (await input.tools.routes.travelMinutes(prev, next, "transit")) + 30;
        clock = Math.ceil(clock / 15) * 15;
        if (stops.length > 0 && clock + next.durationHours * 60 > 22 * 60) {
          pool.unshift(next);
          break;
        }
        stops.push({ activity_id: next.id, start: hhmm(clock) });
        clock += Math.round(next.durationHours * 60);
        prev = next;
        if (next.durationHours >= 7) break;
      }
      if (stops.length) plan.push({ day: plan.length + 1, city, stops });
    }
    return plan;
  }
}

function hhmm(min: number): string {
  return `${String(Math.floor(min / 60)).padStart(2, "0")}:${String(min % 60).padStart(2, "0")}`;
}
