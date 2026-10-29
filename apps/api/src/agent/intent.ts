import { CITY_NAMES } from "../data/activities.js";
import type { Category, Intent, Party, TripProfile } from "../types.js";

// Rule-based intent extraction. It is free, instant and deterministic, which
// matters because its output is both the cache key and the router's input.
// A Haiku-based extractor can replace it behind the same signature.

const CITY_ALIASES: Record<string, string> = {
  nyc: "New York",
  "new york city": "New York",
  manhattan: "New York",
  ubud: "Bali",
  "the uk": "London",
};

const CATEGORY_WORDS: Record<Category, string[]> = {
  sightseeing: ["sightseeing", "sights", "landmark", "landmarks", "views", "viewpoint", "must see", "must-see", "iconic", "tour"],
  adventure: ["adventure", "adventurous", "thrill", "hike", "hiking", "trek", "rafting", "surf", "surfing", "skydive", "outdoor", "active", "bike", "snorkel", "diving"],
  show: ["show", "shows", "theatre", "theater", "musical", "broadway", "concert", "cabaret", "opera", "performance", "nightlife", "flamenco", "fado"],
  food: ["food", "eat", "eating", "restaurant", "restaurants", "tapas", "street food", "cooking", "foodie", "wine", "dinner"],
  culture: ["museum", "museums", "history", "historic", "art", "culture", "cultural", "temple", "palace"],
};

const MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"];

const NUMBER_WORDS: Record<string, number> = {
  one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10, eleven: 11, twelve: 12, fourteen: 14,
};

const CONSTRAINT_WORDS: Record<string, string[]> = {
  kids: ["kid", "kids", "child", "children", "toddler", "baby", "teen", "teens"],
  accessibility: ["wheelchair", "accessible", "mobility", "elderly", "stroller", "pram"],
  diet: ["vegetarian", "vegan", "halal", "kosher", "gluten"],
  fixedDates: ["tickets for", "on the", "already booked", "fixed date", "arriving on"],
  weather: ["rain", "rainy", "indoor", "hot weather"],
};

function hasWord(text: string, word: string): boolean {
  return new RegExp(`(^|[^a-z])${word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}([^a-z]|$)`).test(text);
}

function parseNumber(token: string): number | null {
  if (/^\d+$/.test(token)) return Number(token);
  return NUMBER_WORDS[token] ?? null;
}

export function extractIntent(question: string): Intent {
  const text = question.toLowerCase();

  const cities = new Set<string>();
  for (const city of CITY_NAMES) if (hasWord(text, city.toLowerCase())) cities.add(city);
  for (const [alias, city] of Object.entries(CITY_ALIASES)) if (hasWord(text, alias)) cities.add(city);

  const categories = (Object.keys(CATEGORY_WORDS) as Category[]).filter((cat) =>
    CATEGORY_WORDS[cat].some((w) => hasWord(text, w)),
  );

  let budgetUsd: number | null = null;
  const budgetMatch =
    text.match(/(?:under|below|less than|max(?:imum)?|budget(?: of| is)?|up to|cheaper than)\s*[$€£]?\s*(\d[\d,]*)/) ??
    text.match(/[$€£]\s*(\d[\d,]*)/) ??
    text.match(/(\d[\d,]*)\s*(?:usd|dollars?|euros?|eur|pounds?|gbp)/);
  if (budgetMatch?.[1]) budgetUsd = Number(budgetMatch[1].replace(/,/g, ""));
  if (budgetUsd === null && /\b(cheap|budget|free|low cost|affordable)\b/.test(text)) budgetUsd = 30;

  let days: number | null = null;
  for (const m of text.matchAll(/\b(\d+|[a-z]+)[- ]?(?:day|days|night|nights)\b/g)) {
    days = parseNumber(m[1] ?? "");
    if (days !== null) break;
  }
  if (days === null) {
    const weekMatch = text.match(/\b(a|one|two|\d+)[- ]weeks?\b/);
    if (weekMatch?.[1]) days = 7 * (weekMatch[1] === "a" ? 1 : (parseNumber(weekMatch[1]) ?? 1));
    else if (/\bweekend\b/.test(text)) days = 2;
  }

  const month = MONTHS.find((m) => hasWord(text, m)) ?? null;

  let party: Party | null = null;
  if (/\b(kids?|children|family|toddler|son|daughter)\b/.test(text)) party = "family";
  else if (/\b(wife|husband|partner|girlfriend|boyfriend|honeymoon|couple|anniversary|romantic)\b/.test(text)) party = "couple";
  else if (/\b(friends|group|team|bachelor|bachelorette|stag|hen)\b/.test(text)) party = "group";
  else if (/\b(solo|alone|by myself|on my own)\b/.test(text)) party = "solo";

  const wantsItinerary =
    /\b(itinerary|day plan|day-by-day|day by day|schedule|plan my|plan a|plan our|plan the)\b/.test(text) || days !== null;

  const constraints = Object.entries(CONSTRAINT_WORDS)
    .filter(([, words]) => words.some((w) => text.includes(w)))
    .map(([name]) => name);
  if (budgetUsd !== null) constraints.push("budget");

  const followUp = /\b(swap|replace|instead|that one|those|more like|another one|change day|day \d+|the second|the first|it again)\b/.test(text);

  return { cities: [...cities], categories, budgetUsd, month, days, party, wantsItinerary, constraints, followUp };
}

export function emptyProfile(): TripProfile {
  return { cities: [], budgetUsd: null, month: null, days: null, party: null, interests: [], constraints: [] };
}

/** Fold a new intent into the session's trip profile. Newer facts win. */
export function mergeProfile(profile: TripProfile, intent: Intent): TripProfile {
  return {
    cities: intent.cities.length ? intent.cities : profile.cities,
    budgetUsd: intent.budgetUsd ?? profile.budgetUsd,
    month: intent.month ?? profile.month,
    days: intent.days ?? profile.days,
    party: intent.party ?? profile.party,
    interests: [...new Set([...profile.interests, ...intent.categories])],
    constraints: [...new Set([...profile.constraints, ...intent.constraints])],
  };
}

/** The intent the cache and router should use: this question's facts, filled in from the profile. */
export function effectiveIntent(intent: Intent, profile: TripProfile): Intent {
  return {
    ...intent,
    cities: intent.cities.length ? intent.cities : profile.cities,
    categories: intent.categories.length ? intent.categories : profile.interests,
    budgetUsd: intent.budgetUsd ?? profile.budgetUsd,
    month: intent.month ?? profile.month,
    days: intent.days ?? (intent.wantsItinerary ? profile.days : null),
    party: intent.party ?? profile.party,
    constraints: [...new Set([...intent.constraints, ...profile.constraints])],
  };
}
