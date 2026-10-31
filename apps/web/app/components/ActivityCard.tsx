import type { Activity } from "../types";

const CATEGORY_LABEL: Record<string, string> = {
  sightseeing: "Sightseeing",
  adventure: "Adventure",
  show: "Show",
  food: "Food",
  culture: "Culture",
};

function price(usd: number): string {
  return usd === 0 ? "Free" : `From $${usd}`;
}

function duration(hours: number): string {
  return hours < 1 ? `${Math.round(hours * 60)} min` : `${hours} h`;
}

export function ActivityCard({ activity, rank }: { activity: Activity; rank?: number }) {
  return (
    <article className="card">
      <div className="card-top">
        {rank !== undefined && <span className="rank">{rank}</span>}
        <span className={`chip chip-${activity.category}`}>{CATEGORY_LABEL[activity.category] ?? activity.category}</span>
        {activity.rating > 0 && <span className="rating">★ {activity.rating.toFixed(1)}</span>}
      </div>
      <h3>{activity.name}</h3>
      <p className="desc">{activity.description}</p>
      <div className="facts">
        <span>{price(activity.priceFromUsd)}</span>
        <span>{duration(activity.durationHours)}</span>
        {activity.date && <span>{activity.date}</span>}
      </div>
      <a className="book" href={activity.bookingUrl} target="_blank" rel="noopener noreferrer">
        See and book
      </a>
    </article>
  );
}
