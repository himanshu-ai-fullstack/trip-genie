import { createSdkMcpServer, tool } from "@anthropic-ai/claude-agent-sdk";
import { z } from "zod";
import { cachedCall } from "../cache/toolCache.js";
import type { Providers } from "../providers/index.js";
import type { ActivityIndex } from "../retrieval/index.js";
import type { KV } from "../store/kv.js";
import { CATEGORIES, type Activity, type GenieEvent, type ItineraryDay, type Party } from "../types.js";
import { checkItinerary } from "./itinerary.js";

export const SERVER_NAME = "genie";

export type ToolContext = {
  index: ActivityIndex;
  providers: Providers;
  kv: KV;
  emit: (event: GenieEvent) => void;
  /** Every activity a tool returned this turn. Cards and plans may only use these, so links are never invented. */
  seen: Map<string, Activity>;
  party: Party | null;
  /** Kill switch in degraded mode: no live provider calls. */
  liveDisabled: boolean;
};

const ok = (data: unknown) => ({ content: [{ type: "text" as const, text: JSON.stringify(data) }] });
const fail = (message: string) => ({ content: [{ type: "text" as const, text: message }], isError: true });

/** What the model sees for each activity: enough to rank, nothing it needs to copy into a link. */
function brief(a: Activity & { date?: string }) {
  return {
    id: a.id,
    name: a.name,
    category: a.category,
    price_from_usd: a.priceFromUsd,
    duration_hours: a.durationHours,
    rating: a.rating,
    tags: a.tags,
    description: a.description,
    ...(a.date ? { date: a.date } : {}),
  };
}

export function createGenieTools(ctx: ToolContext) {
  const remember = (list: Activity[]) => list.forEach((a) => ctx.seen.set(a.id, a));

  const searchActivities = tool(
    "search_activities",
    "Search Genie's activity index for one city. Use this first for any recommendation. Returns activities ranked by fit.",
    {
      city: z.string().describe("City name, for example Lisbon"),
      query: z.string().describe("What the traveler wants, in their words, e.g. 'family friendly outdoor fun'"),
      categories: z.array(z.enum(CATEGORIES as [string, ...string[]])).optional(),
      max_price_usd: z.number().optional().describe("Per-person price ceiling in USD"),
      limit: z.number().int().min(1).max(15).optional(),
    },
    async (args) => {
      ctx.emit({ type: "status", message: `Searching activities in ${args.city}` });
      const results = await ctx.index.search({
        city: args.city,
        text: args.query,
        categories: args.categories as Activity["category"][] | undefined,
        maxPriceUsd: args.max_price_usd ?? null,
        party: ctx.party,
        limit: args.limit ?? 10,
      });
      if (results.length === 0) {
        const cities = await ctx.index.cities();
        return ok({ results: [], note: `No matches. Genie currently covers: ${cities.join(", ")}.` });
      }
      remember(results.map((r) => r.activity));
      return ok({ results: results.map((r) => brief(r.activity)) });
    },
  );

  const findEvents = tool(
    "find_events",
    "Find dated shows, concerts and sports events in a city between two dates.",
    {
      city: z.string(),
      from: z.string().describe("Start date, YYYY-MM-DD"),
      to: z.string().describe("End date, YYYY-MM-DD"),
      keyword: z.string().optional(),
    },
    async (args) => {
      if (ctx.liveDisabled) return fail("Live event search is paused right now. Recommend from search_activities instead.");
      ctx.emit({ type: "status", message: `Looking up shows in ${args.city}` });
      const events = await cachedCall(ctx.kv, ctx.providers.events.name, args, () => ctx.providers.events.findEvents(args));
      remember(events);
      return ok({ events: events.map(brief) });
    },
  );

  const getTourOffers = tool(
    "get_tour_offers",
    "Get bookable tours and experiences with prices for a city.",
    {
      city: z.string(),
      query: z.string(),
      max_price_usd: z.number().optional(),
    },
    async (args) => {
      if (ctx.liveDisabled) return fail("Live tour offers are paused right now. Recommend from search_activities instead.");
      ctx.emit({ type: "status", message: `Checking tour offers in ${args.city}` });
      const q = { city: args.city, query: args.query, maxPriceUsd: args.max_price_usd ?? null };
      const offers = await cachedCall(ctx.kv, ctx.providers.tours.name, q, () => ctx.providers.tours.findTours(q));
      remember(offers);
      return ok({ offers: offers.map(brief) });
    },
  );

  const travelTime = tool(
    "travel_time",
    "Estimated minutes to travel between two activities returned earlier this turn.",
    {
      from_id: z.string(),
      to_id: z.string(),
      mode: z.enum(["walk", "transit"]).optional(),
    },
    async (args) => {
      const from = ctx.seen.get(args.from_id);
      const to = ctx.seen.get(args.to_id);
      if (!from || !to) return fail("Both ids must come from a search earlier in this turn.");
      return ok({ minutes: await ctx.providers.routes.travelMinutes(from, to, args.mode ?? "transit") });
    },
  );

  const showActivities = tool(
    "show_activities",
    "Show the traveler a ranked list of activity cards with booking links. Pass ids in ranked order, best first.",
    {
      activity_ids: z.array(z.string()).min(1).max(12),
    },
    async (args) => {
      const unknown = args.activity_ids.filter((id) => !ctx.seen.has(id));
      if (unknown.length) return fail(`Unknown ids (search for them first): ${unknown.join(", ")}`);
      ctx.emit({ type: "cards", activities: args.activity_ids.map((id) => ctx.seen.get(id)!) });
      return ok({ shown: args.activity_ids.length });
    },
  );

  const showItinerary = tool(
    "show_itinerary",
    "Show a day by day plan. Each stop is an activity id from this turn and a start time. The plan is checked for opening times and travel time; fix any problems it reports and call again.",
    {
      days: z
        .array(
          z.object({
            day: z.number().int().min(1),
            city: z.string(),
            stops: z.array(z.object({ activity_id: z.string(), start: z.string().describe("HH:MM, 24 hour"), note: z.string().optional() })).min(1),
          }),
        )
        .min(1)
        .max(21),
    },
    async (args) => {
      const days: ItineraryDay[] = args.days.map((d) => ({
        day: d.day,
        city: d.city,
        stops: d.stops.map((s) => ({ activityId: s.activity_id, start: s.start, note: s.note })),
      }));
      const issues = await checkItinerary(days, ctx.seen, ctx.providers.routes);
      if (issues.length) return fail(`The plan is not feasible yet:\n- ${issues.join("\n- ")}`);
      const ids = [...new Set(days.flatMap((d) => d.stops.map((s) => s.activityId)))];
      ctx.emit({ type: "itinerary", days, activities: ids.map((id) => ctx.seen.get(id)!) });
      return ok({ shown_days: days.length });
    },
  );

  const tools = [searchActivities, findEvents, getTourOffers, travelTime, showActivities, showItinerary];
  return {
    server: createSdkMcpServer({ name: SERVER_NAME, version: "0.1.0", tools, alwaysLoad: true }),
    allowedTools: tools.map((t) => `mcp__${SERVER_NAME}__${t.name}`),
    seen: ctx.seen,
    routes: ctx.providers.routes,
    handlers: {
      search_activities: searchActivities.handler,
      find_events: findEvents.handler,
      get_tour_offers: getTourOffers.handler,
      travel_time: travelTime.handler,
      show_activities: showActivities.handler,
      show_itinerary: showItinerary.handler,
    },
  };
}
