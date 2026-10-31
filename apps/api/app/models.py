from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, Field

Category = Literal["sightseeing", "adventure", "show", "food", "culture"]
Party = Literal["solo", "couple", "family", "group"]
KillSwitchMode = Literal["on", "degraded", "off"]
ModelId = str


class Activity(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str
    name: str
    city: str
    country: str
    lat: float
    lng: float
    category: Category
    tags: list[str] = Field(default_factory=list)
    price_from_usd: float = Field(alias="priceFromUsd")
    price_band: str = Field(alias="priceBand")
    duration_hours: float = Field(alias="durationHours")
    rating: float
    description: str
    provider: str
    booking_url: str = Field(alias="bookingUrl")
    date: Optional[str] = None
    venue: Optional[str] = None


class Intent(BaseModel):
    cities: list[str] = Field(default_factory=list)
    categories: list[Category] = Field(default_factory=list)
    budget_usd: Optional[float] = None
    month: Optional[str] = None
    days: Optional[int] = None
    party: Optional[Party] = None
    wants_itinerary: bool = False
    constraints: list[str] = Field(default_factory=list)
    follow_up: bool = False


class TripProfile(BaseModel):
    cities: list[str] = Field(default_factory=list)
    budget_usd: Optional[float] = None
    month: Optional[str] = None
    days: Optional[int] = None
    party: Optional[Party] = None
    interests: list[Category] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)


class SessionMessage(BaseModel):
    role: Literal["user", "assistant"]
    text: str


class Session(BaseModel):
    id: str
    profile: TripProfile = Field(default_factory=TripProfile)
    history: list[SessionMessage] = Field(default_factory=list)
    last_model: Optional[str] = None


class ItineraryStop(BaseModel):
    activity_id: str
    start: str
    note: Optional[str] = None


class ItineraryDay(BaseModel):
    day: int
    city: str
    stops: list[ItineraryStop]


class ChatRequest(BaseModel):
    sessionId: Optional[str] = Field(default=None, min_length=8, max_length=64)
    message: str = Field(min_length=1, max_length=4000)


class KillSwitchRequest(BaseModel):
    mode: KillSwitchMode
