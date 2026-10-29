export type Category = "sightseeing" | "adventure" | "show" | "food" | "culture";
export const CATEGORIES: Category[] = ["sightseeing", "adventure", "show", "food", "culture"];

export type PriceBand = "free" | "$" | "$$" | "$$$";

export type Activity = {
  id: string;
  name: string;
  city: string;
  country: string;
  lat: number;
  lng: number;
  category: Category;
  tags: string[];
  priceFromUsd: number;
  priceBand: PriceBand;
  durationHours: number;
  rating: number;
  description: string;
  provider: string;
  bookingUrl: string;
};

export type Party = "solo" | "couple" | "family" | "group";

/** What a question asks for, reduced to the fields that decide the answer. */
export type Intent = {
  cities: string[];
  categories: Category[];
  budgetUsd: number | null;
  month: string | null;
  days: number | null;
  party: Party | null;
  wantsItinerary: boolean;
  constraints: string[];
  /** True when the question leans on earlier turns ("swap day 2", "more like that"). */
  followUp: boolean;
};

/** Trip facts gathered across a chat session. */
export type TripProfile = {
  cities: string[];
  budgetUsd: number | null;
  month: string | null;
  days: number | null;
  party: Party | null;
  interests: Category[];
  constraints: string[];
};

export type ItineraryStop = { activityId: string; start: string; note?: string };
export type ItineraryDay = { day: number; city: string; stops: ItineraryStop[] };

export type ModelId = "claude-haiku-5-5" | "claude-sonnet-5-5" | "claude-opus-5-5" | "claude-sonnet-5" | "offline";

/** Events streamed to the browser over SSE. */
export type GenieEvent =
  | { type: "status"; message: string }
  | { type: "text"; delta: string }
  | { type: "cards"; activities: Activity[] }
  | { type: "itinerary"; days: ItineraryDay[]; activities: Activity[] }
  | {
      type: "done";
      model: ModelId;
      routeReason: string;
      cached: boolean;
      costUsd: number;
      turns: number;
      stoppedEarly: string | null;
    }
  | { type: "error"; message: string };
