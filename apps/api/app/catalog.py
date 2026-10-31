from __future__ import annotations

import re
from urllib.parse import quote
from .models import Activity

# name, category, price USD, duration hours, rating, tags, description
_CITIES = [
    ("Paris", "France", 48.8566, 2.3522, [
        ("Eiffel Tower summit visit", "sightseeing", 35, 2, 4.6, ["iconic", "views", "family"], "Lift to the top of the Eiffel Tower for views across Paris."),
        ("Louvre Museum timed entry", "culture", 24, 3, 4.7, ["museum", "art", "rainy-day", "family"], "See the Mona Lisa and Winged Victory in the world's largest art museum."),
        ("Seine river evening cruise", "sightseeing", 18, 1, 4.5, ["night", "romantic", "family", "views"], "One hour boat cruise past Notre-Dame and the lit-up bridges."),
        ("Moulin Rouge cabaret show", "show", 140, 2, 4.4, ["night", "adults", "romantic"], "The classic Montmartre cabaret with the French cancan."),
        ("Montmartre walking food tour", "food", 95, 3, 4.8, ["walking", "local", "food"], "Cheese, crepes and wine with a local guide on the hill of Montmartre."),
        ("Versailles palace and gardens", "culture", 32, 5, 4.6, ["day-trip", "history", "gardens"], "Day trip to the Hall of Mirrors and the royal gardens."),
    ]),
    ("London", "United Kingdom", 51.5072, -0.1276, [
        ("Tower of London and Crown Jewels", "culture", 40, 3, 4.6, ["history", "family", "iconic"], "Nine centuries of history, the Beefeaters and the Crown Jewels."),
        ("West End musical", "show", 60, 3, 4.8, ["night", "theatre", "family"], "An evening musical in London's theatre district."),
        ("London Eye flight", "sightseeing", 42, 1, 4.4, ["views", "family", "iconic"], "Thirty minute rotation over the Thames with views of Westminster."),
        ("British Museum highlights", "culture", 0, 3, 4.8, ["museum", "free", "rainy-day", "family"], "Free museum with the Rosetta Stone and the Parthenon sculptures."),
        ("Borough Market tasting walk", "food", 0, 2, 4.6, ["food", "local", "free", "walking"], "Browse and graze through London's oldest food market."),
        ("Thames RIB speedboat ride", "adventure", 55, 1, 4.7, ["speed", "thrill", "views"], "High speed boat ride from the London Eye to Canary Wharf."),
    ]),
    ("New York", "United States", 40.7128, -74.006, [
        ("Broadway show", "show", 120, 3, 4.8, ["night", "theatre", "iconic"], "A Broadway musical or play in the Theater District."),
        ("Statue of Liberty and Ellis Island ferry", "sightseeing", 25, 4, 4.6, ["iconic", "history", "family"], "Ferry to Liberty Island and the immigration museum on Ellis Island."),
        ("Central Park bike rental", "adventure", 20, 2, 4.6, ["outdoor", "family", "active"], "Cycle the park loop past Bethesda Fountain and the Reservoir."),
        ("Top of the Rock observation deck", "sightseeing", 44, 1, 4.7, ["views", "iconic", "night"], "Open-air deck with a straight view of the Empire State Building."),
        ("Metropolitan Museum of Art", "culture", 30, 3, 4.8, ["museum", "art", "rainy-day"], "Five thousand years of art on Fifth Avenue."),
        ("Brooklyn pizza walking tour", "food", 85, 3, 4.7, ["food", "walking", "local"], "Classic New York slices across three Brooklyn pizzerias."),
    ]),
    ("Tokyo", "Japan", 35.6762, 139.6503, [
        ("Senso-ji temple and Asakusa walk", "culture", 0, 2, 4.6, ["temple", "free", "history", "family"], "Tokyo's oldest temple and the Nakamise shopping street."),
        ("teamLab Planets digital art", "culture", 28, 2, 4.7, ["art", "rainy-day", "family", "unique"], "Walk through water and light in an immersive art museum."),
        ("Shibuya Sky observation deck", "sightseeing", 15, 1, 4.7, ["views", "night", "iconic"], "Rooftop deck above the Shibuya scramble crossing."),
        ("Sumo morning practice visit", "show", 90, 2, 4.5, ["sport", "unique", "local"], "Watch wrestlers train at a sumo stable."),
        ("Tsukiji outer market food tour", "food", 110, 3, 4.8, ["food", "local", "walking"], "Sushi, tamagoyaki and street snacks with a guide."),
        ("Mount Fuji and Hakone day trip", "adventure", 120, 11, 4.5, ["day-trip", "nature", "views"], "Coach trip to Fuji's fifth station and a Lake Ashi cruise."),
    ]),
    ("Barcelona", "Spain", 41.3874, 2.1686, [
        ("Sagrada Familia with tower access", "sightseeing", 40, 2, 4.8, ["iconic", "architecture", "family"], "Gaudi's basilica, with a lift up one of the towers."),
        ("Park Guell entry", "sightseeing", 13, 2, 4.4, ["architecture", "outdoor", "family", "views"], "Mosaic terraces and city views in Gaudi's park."),
        ("Flamenco show in the Gothic Quarter", "show", 35, 1, 4.6, ["night", "music", "local"], "An intimate live flamenco performance."),
        ("Tapas and wine tour", "food", 90, 3, 4.8, ["food", "night", "local"], "Four tapas bars in El Born with a local guide."),
        ("Montserrat hike and monastery", "adventure", 60, 7, 4.6, ["day-trip", "hiking", "nature", "views"], "Mountain monastery day trip with trail time on the ridges."),
        ("Barceloneta stand-up paddle", "adventure", 30, 2, 4.5, ["beach", "water", "active"], "Paddleboard lesson off Barceloneta beach."),
    ]),
    ("Rome", "Italy", 41.9028, 12.4964, [
        ("Colosseum and Roman Forum", "culture", 30, 3, 4.7, ["history", "iconic", "family"], "The arena, the Forum and Palatine Hill on one ticket."),
        ("Vatican Museums and Sistine Chapel", "culture", 35, 3, 4.7, ["art", "museum", "rainy-day"], "Raphael Rooms and Michelangelo's ceiling."),
        ("Trastevere evening food tour", "food", 95, 3, 4.8, ["food", "night", "local", "walking"], "Suppli, cacio e pepe and gelato in Trastevere."),
        ("Vespa tour of Rome", "adventure", 150, 3, 4.8, ["thrill", "views", "unique"], "Ride pillion through the city's hills and piazzas."),
        ("Opera in a Roman church", "show", 40, 2, 4.5, ["night", "music", "romantic"], "Arias by Verdi and Puccini in a candlelit church."),
        ("Trevi Fountain and Pantheon walk", "sightseeing", 0, 2, 4.6, ["free", "walking", "iconic", "family"], "Self-guided loop of Rome's famous squares and fountains."),
    ]),
    ("Lisbon", "Portugal", 38.7223, -9.1393, [
        ("Tram 28 ride through Alfama", "sightseeing", 3, 1, 4.3, ["iconic", "cheap", "family"], "The yellow tram rattling up through Lisbon's oldest quarter."),
        ("Belem Tower and Jeronimos Monastery", "culture", 20, 3, 4.6, ["history", "architecture", "family"], "Age of Discovery monuments on the riverfront."),
        ("Fado dinner show", "show", 55, 2, 4.6, ["night", "music", "food", "romantic"], "Traditional Portuguese fado with dinner in Alfama."),
        ("Pasteis de nata baking class", "food", 45, 2, 4.8, ["food", "family", "hands-on"], "Bake Lisbon's custard tarts with a local pastry chef."),
        ("Sintra palaces day trip", "adventure", 70, 8, 4.7, ["day-trip", "nature", "history"], "Pena Palace and the Moorish castle in the Sintra hills."),
        ("Surf lesson at Carcavelos", "adventure", 35, 2, 4.6, ["beach", "water", "active"], "Beginner surf lesson on the beach west of Lisbon."),
    ]),
    ("Dubai", "United Arab Emirates", 25.2048, 55.2708, [
        ("Burj Khalifa At the Top", "sightseeing", 45, 2, 4.6, ["views", "iconic", "family"], "Observation decks on levels 124 and 125."),
        ("Desert safari with dune bashing", "adventure", 60, 6, 4.7, ["desert", "thrill", "night", "food"], "4x4 dune drive, camel ride and a camp dinner."),
        ("Dubai Fountain show", "show", 0, 1, 4.8, ["free", "night", "family"], "Free evening water and light show at the foot of the Burj."),
        ("Old Dubai souks and abra ride", "culture", 15, 3, 4.5, ["history", "local", "cheap"], "Gold and spice souks with a creek crossing by abra."),
        ("La Perle by Dragone", "show", 75, 2, 4.7, ["night", "family", "acrobatics"], "Water-stage acrobatics show at Al Habtoor City."),
        ("Skydive over the Palm", "adventure", 600, 4, 4.9, ["thrill", "views", "adults"], "Tandem skydive above Palm Jumeirah."),
    ]),
    ("Singapore", "Singapore", 1.3521, 103.8198, [
        ("Gardens by the Bay domes", "sightseeing", 40, 3, 4.8, ["gardens", "family", "rainy-day"], "Cloud Forest and Flower Dome conservatories."),
        ("Supertree Grove light show", "show", 0, 1, 4.7, ["free", "night", "family"], "Garden Rhapsody light and music show at the Supertrees."),
        ("Night Safari", "adventure", 55, 3, 4.6, ["animals", "night", "family"], "Tram ride and trails through a nocturnal zoo."),
        ("Hawker centre food walk", "food", 60, 3, 4.8, ["food", "local", "cheap"], "Chicken rice, laksa and satay at Maxwell and Lau Pa Sat."),
        ("Sentosa luge and skyride", "adventure", 25, 2, 4.6, ["thrill", "family", "outdoor"], "Gravity luge tracks down Sentosa island."),
        ("Marina Bay Sands SkyPark", "sightseeing", 32, 1, 4.5, ["views", "iconic", "night"], "Observation deck 57 floors above Marina Bay."),
    ]),
    ("Bali", "Indonesia", -8.4095, 115.1889, [
        ("Mount Batur sunrise trek", "adventure", 45, 7, 4.7, ["hiking", "nature", "sunrise", "active"], "Pre-dawn climb of an active volcano for sunrise."),
        ("Ubud rice terraces and monkey forest", "sightseeing", 30, 5, 4.5, ["nature", "family", "culture"], "Tegallalang terraces and the Sacred Monkey Forest."),
        ("Kecak fire dance at Uluwatu", "show", 12, 1, 4.7, ["culture", "sunset", "family"], "Clifftop temple dance at sunset."),
        ("White water rafting on the Ayung", "adventure", 35, 4, 4.6, ["water", "thrill", "nature"], "Grade II to III rapids through jungle gorges."),
        ("Balinese cooking class", "food", 35, 5, 4.9, ["food", "hands-on", "family", "local"], "Market visit and cooking class in an Ubud family compound."),
        ("Snorkelling at Nusa Penida", "adventure", 55, 9, 4.5, ["water", "beach", "day-trip", "animals"], "Boat trip to snorkel with manta rays."),
    ]),
]


def _slug(value: str) -> str:
    return re.sub(r"(^-|-$)", "", re.sub(r"[^a-z0-9]+", "-", value.lower()))


def _price_band(price: float) -> str:
    if price == 0:
        return "free"
    if price < 30:
        return "$"
    if price < 100:
        return "$$"
    return "$$$"


ACTIVITIES: list[Activity] = []
for city, country, lat, lng, rows in _CITIES:
    for i, (name, category, price, duration, rating, tags, description) in enumerate(rows):
        ACTIVITIES.append(Activity(
            id=f"{_slug(city)}-{_slug(name)}", name=name, city=city, country=country,
            lat=lat + ((i % 3) - 1) * 0.02, lng=lng + (int(i / 3) - 0.5) * 0.03,
            category=category, tags=tags, priceFromUsd=price, priceBand=_price_band(price),
            durationHours=duration, rating=rating, description=description, provider="google_maps",
            bookingUrl=f"https://www.google.com/maps/search/?api=1&query={quote(name + ', ' + city)}",
        ))

CITY_NAMES = [entry[0] for entry in _CITIES]
