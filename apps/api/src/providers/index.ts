import { ACTIVITIES } from "../data/activities.js";
import type { Activity } from "../types.js";

/** A dated event or bookable tour, shaped as an Activity so the UI renders it as a card. */
export type Offer = Activity & { date?: string; venue?: string };

export interface EventsProvider {
  readonly name: string;
  findEvents(q: { city: string; from: string; to: string; keyword?: string }): Promise<Offer[]>;
}

export interface ToursProvider {
  readonly name: string;
  findTours(q: { city: string; query: string; maxPriceUsd?: number | null }): Promise<Offer[]>;
}

export interface RoutesProvider {
  readonly name: string;
  travelMinutes(from: { lat: number; lng: number }, to: { lat: number; lng: number }, mode: "walk" | "transit"): Promise<number>;
}

function hash(s: string): number {
  let h = 7;
  for (const c of s) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return h;
}

function addDays(iso: string, n: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

/** Recorded-style responses built from the seed catalog, so the agent runs with no keys. */
export class MockEventsProvider implements EventsProvider {
  readonly name = "mock-events";

  async findEvents(q: { city: string; from: string; to: string; keyword?: string }): Promise<Offer[]> {
    const shows = ACTIVITIES.filter((a) => a.city.toLowerCase() === q.city.toLowerCase() && a.category === "show");
    const span = Math.max(0, Math.round((Date.parse(q.to) - Date.parse(q.from)) / 86_400_000));
    const kw = q.keyword?.toLowerCase();
    return shows
      .filter((a) => !kw || `${a.name} ${a.tags.join(" ")}`.toLowerCase().includes(kw))
      .map((a) => {
        const date = addDays(q.from, hash(a.id) % (span + 1));
        return { ...a, id: `${a.id}@${date}`, date, venue: a.name, provider: this.name };
      });
  }
}

export class MockToursProvider implements ToursProvider {
  readonly name = "mock-tours";

  async findTours(q: { city: string; query: string; maxPriceUsd?: number | null }): Promise<Offer[]> {
    const words = q.query.toLowerCase().split(/\W+/).filter((w) => w.length > 2);
    return ACTIVITIES.filter(
      (a) =>
        a.city.toLowerCase() === q.city.toLowerCase() &&
        a.priceFromUsd > 0 &&
        (q.maxPriceUsd == null || a.priceFromUsd <= q.maxPriceUsd) &&
        (words.length === 0 || words.some((w) => `${a.name} ${a.description} ${a.tags.join(" ")}`.toLowerCase().includes(w))),
    )
      .slice(0, 6)
      .map((a) => ({ ...a, provider: this.name }));
  }
}

/** Straight-line distance with a detour factor. Good enough to check a day plan is feasible. */
export class HaversineRoutesProvider implements RoutesProvider {
  readonly name = "haversine";

  async travelMinutes(from: { lat: number; lng: number }, to: { lat: number; lng: number }, mode: "walk" | "transit"): Promise<number> {
    const R = 6371;
    const dLat = ((to.lat - from.lat) * Math.PI) / 180;
    const dLng = ((to.lng - from.lng) * Math.PI) / 180;
    const a = Math.sin(dLat / 2) ** 2 + Math.cos((from.lat * Math.PI) / 180) * Math.cos((to.lat * Math.PI) / 180) * Math.sin(dLng / 2) ** 2;
    const km = 2 * R * Math.asin(Math.sqrt(a)) * 1.3;
    const kmh = mode === "walk" ? 4.5 : 18;
    return Math.max(5, Math.round((km / kmh) * 60 + (mode === "transit" ? 8 : 0)));
  }
}

/** Ticketmaster Discovery API, used when TICKETMASTER_API_KEY is set. */
export class TicketmasterEventsProvider implements EventsProvider {
  readonly name = "ticketmaster";

  constructor(private readonly apiKey: string) {}

  async findEvents(q: { city: string; from: string; to: string; keyword?: string }): Promise<Offer[]> {
    const params = new URLSearchParams({
      apikey: this.apiKey,
      city: q.city,
      startDateTime: `${q.from}T00:00:00Z`,
      endDateTime: `${q.to}T23:59:59Z`,
      size: "10",
      sort: "relevance,desc",
    });
    if (q.keyword) params.set("keyword", q.keyword);
    const res = await fetch(`https://app.ticketmaster.com/discovery/v2/events.json?${params}`);
    if (!res.ok) throw new Error(`Ticketmaster ${res.status}`);
    type TmEvent = {
      id: string;
      name: string;
      url: string;
      dates?: { start?: { localDate?: string } };
      priceRanges?: { min?: number }[];
      classifications?: { genre?: { name?: string } }[];
      _embedded?: { venues?: { name?: string; location?: { latitude?: string; longitude?: string }; country?: { name?: string } }[] };
    };
    const body = (await res.json()) as { _embedded?: { events?: TmEvent[] } };
    return (body._embedded?.events ?? []).map((e) => {
      const venue = e._embedded?.venues?.[0];
      const price = e.priceRanges?.[0]?.min ?? 0;
      return {
        id: `tm-${e.id}`,
        name: e.name,
        city: q.city,
        country: venue?.country?.name ?? "",
        lat: Number(venue?.location?.latitude ?? 0),
        lng: Number(venue?.location?.longitude ?? 0),
        category: "show",
        tags: [e.classifications?.[0]?.genre?.name?.toLowerCase() ?? "event"],
        priceFromUsd: price,
        priceBand: price === 0 ? "free" : price < 30 ? "$" : price < 100 ? "$$" : "$$$",
        durationHours: 2,
        rating: 0,
        description: `${e.name} at ${venue?.name ?? "the venue"}`,
        provider: this.name,
        bookingUrl: e.url,
        date: e.dates?.start?.localDate,
        venue: venue?.name,
      } satisfies Offer;
    });
  }
}

export type Providers = { events: EventsProvider; tours: ToursProvider; routes: RoutesProvider };

export function createProviders(): Providers {
  const tm = process.env.TICKETMASTER_API_KEY;
  return {
    events: tm ? new TicketmasterEventsProvider(tm) : new MockEventsProvider(),
    // GetYourGuide/Viator partner APIs and Google Places/Routes are mock-only until keys exist.
    tours: new MockToursProvider(),
    routes: new HaversineRoutesProvider(),
  };
}
