import type { Activity, ItineraryDay } from "../types";

export function Itinerary({ days, activities }: { days: ItineraryDay[]; activities: Activity[] }) {
  const byId = new Map(activities.map((a) => [a.id, a]));
  return (
    <section className="itinerary">
      {days.map((d) => (
        <div key={d.day} className="day">
          <h3>
            Day {d.day} <span className="day-city">{d.city}</span>
          </h3>
          <ol>
            {d.stops.map((s) => {
              const a = byId.get(s.activityId);
              if (!a) return null;
              return (
                <li key={s.activityId + s.start}>
                  <span className="time">{s.start}</span>
                  <div>
                    <a href={a.bookingUrl} target="_blank" rel="noopener noreferrer">
                      {a.name}
                    </a>
                    <div className="stop-meta">
                      {a.priceFromUsd === 0 ? "Free" : `From $${a.priceFromUsd}`} · {a.durationHours} h
                      {s.note ? ` · ${s.note}` : ""}
                    </div>
                  </div>
                </li>
              );
            })}
          </ol>
        </div>
      ))}
    </section>
  );
}
