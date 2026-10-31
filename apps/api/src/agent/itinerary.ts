import type { RoutesProvider } from "../providers/index.js";
import type { Activity, ItineraryDay } from "../types.js";

const DAY_START = 8 * 60;
const DAY_END = 23 * 60 + 30;

export function toMinutes(hhmm: string): number | null {
  const m = /^(\d{1,2}):(\d{2})$/.exec(hhmm);
  if (!m) return null;
  const h = Number(m[1]);
  const min = Number(m[2]);
  if (h > 23 || min > 59) return null;
  return h * 60 + min;
}

/**
 * Checks a day plan is physically possible: every stop is a known activity,
 * stops start after the previous one ends plus travel time, and the day
 * stays between 08:00 and 23:30. Returns a list of problems, empty if feasible.
 */
export async function checkItinerary(
  days: ItineraryDay[],
  activities: Map<string, Activity>,
  routes: RoutesProvider,
): Promise<string[]> {
  const issues: string[] = [];
  for (const day of days) {
    let prev: { activity: Activity; end: number } | null = null;
    for (const stop of day.stops) {
      const activity = activities.get(stop.activityId);
      if (!activity) {
        issues.push(`Day ${day.day}: ${stop.activityId} is not an activity returned by a search this turn.`);
        continue;
      }
      const start = toMinutes(stop.start);
      if (start === null) {
        issues.push(`Day ${day.day}: "${stop.start}" is not a HH:MM time.`);
        continue;
      }
      if (start < DAY_START) issues.push(`Day ${day.day}: ${activity.name} starts before 08:00.`);
      const end = start + Math.round(activity.durationHours * 60);
      if (end > DAY_END && activity.durationHours < 10) issues.push(`Day ${day.day}: ${activity.name} ends after 23:30.`);
      if (prev) {
        const travel = await routes.travelMinutes(prev.activity, activity, "transit");
        if (start < prev.end + travel) {
          issues.push(
            `Day ${day.day}: ${activity.name} starts at ${stop.start} but ${prev.activity.name} ends at ${fmt(prev.end)} and travel takes about ${travel} min.`,
          );
        }
      }
      prev = { activity, end };
    }
  }
  return issues;
}

function fmt(min: number): string {
  return `${String(Math.floor(min / 60)).padStart(2, "0")}:${String(min % 60).padStart(2, "0")}`;
}
