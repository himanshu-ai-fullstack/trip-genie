import { describe, expect, it } from "vitest";
import { checkItinerary } from "../src/agent/itinerary.js";
import { ACTIVITIES } from "../src/data/activities.js";
import { HaversineRoutesProvider } from "../src/providers/index.js";

const byId = new Map(ACTIVITIES.map((a) => [a.id, a]));
const routes = new HaversineRoutesProvider();

describe("checkItinerary", () => {
  it("accepts a feasible day", async () => {
    const issues = await checkItinerary(
      [{ day: 1, city: "Rome", stops: [{ activityId: "rome-colosseum-and-roman-forum", start: "09:00" }, { activityId: "rome-trevi-fountain-and-pantheon-walk", start: "13:30" }] }],
      byId,
      routes,
    );
    expect(issues).toEqual([]);
  });

  it("rejects overlaps, unknown ids and bad times", async () => {
    const issues = await checkItinerary(
      [
        {
          day: 1,
          city: "Rome",
          stops: [
            { activityId: "rome-colosseum-and-roman-forum", start: "09:00" },
            { activityId: "rome-vatican-museums-and-sistine-chapel", start: "10:00" },
            { activityId: "made-up", start: "15:00" },
            { activityId: "rome-opera-in-a-roman-church", start: "9pm" },
          ],
        },
      ],
      byId,
      routes,
    );
    expect(issues).toHaveLength(3);
  });
});
