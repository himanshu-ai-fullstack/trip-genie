// Mirrors the SSE events emitted by apps/api/app/main.py.
export type Activity = {
  id: string;
  name: string;
  city: string;
  category: string;
  tags: string[];
  priceFromUsd: number;
  durationHours: number;
  rating: number;
  description: string;
  provider: string;
  bookingUrl: string;
  date?: string;
};

export type ItineraryDay = { day: number; city: string; stops: { activityId: string; start: string; note?: string }[] };

export type GenieEvent =
  | { type: "session"; sessionId: string }
  | { type: "status"; message: string }
  | { type: "text"; delta: string }
  | { type: "cards"; activities: Activity[] }
  | { type: "itinerary"; days: ItineraryDay[]; activities: Activity[] }
  | { type: "done"; model: string; routeReason: string; cached: boolean; costUsd: number; turns: number; stoppedEarly: string | null; runId?: string; task?: string; confidence?: number }
  | { type: "error"; message: string };

export type Turn = {
  id: string;
  question: string;
  text: string;
  status: string | null;
  cards: Activity[];
  itinerary: { days: ItineraryDay[]; activities: Activity[] } | null;
  meta: Extract<GenieEvent, { type: "done" }> | null;
  error: string | null;
  streaming: boolean;
};
