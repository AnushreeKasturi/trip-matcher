"""
Thin async wrapper around SerpApi's google_flights and google_hotels engines.

Both engines are plain HTTP GET requests against https://serpapi.com/search,
so we use httpx.AsyncClient directly rather than the official (synchronous)
`google-search-results` package. This lets us fire several destinations'
worth of calls concurrently without spinning up threads.

Free tier: 250 searches/month, 50 requests/hour, shared across both engines.
We stay well under the hourly cap by batching concurrent requests (see
main.py's BATCH_SIZE) rather than firing all destinations at once.
"""

import os
import json
import time
import asyncio
import statistics
from pathlib import Path

import httpx

SERPAPI_BASE_URL = "https://serpapi.com/search"
SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")

# Cache of raw SerpApi responses, persisted to disk so it survives server
# restarts — the free tier is only 250 searches/month, so a search that has
# been run once should never cost quota again while its prices are still
# fresh. Keyed by the params that actually affect the result.
MIN_HOTELS_FOR_BENCHMARK = 4  # fewer actual hotels than this -> count rentals too
MIN_REVIEWS_TO_TRUST = 20  # hotel picks with fewer reviews rank below those with more
# With prefer="cheapest", take a nonstop over a cheaper connecting flight if
# it costs at most this much more — nobody wants a Goa layover on BLR->BOM
# to save a few hundred rupees.
NONSTOP_TOLERANCE = 0.15

CACHE_FILE = Path(__file__).parent / ".cache" / "serpapi_cache.json"
CACHE_TTL_SECONDS = float(os.environ.get("CACHE_TTL_HOURS", "72")) * 3600


def _load_cache() -> dict:
    try:
        entries = json.loads(CACHE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    now = time.time()
    return {k: v for k, v in entries.items() if now - v["saved_at"] < CACHE_TTL_SECONDS}


def _save_cache() -> None:
    CACHE_FILE.parent.mkdir(exist_ok=True)
    tmp = CACHE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(_cache))
    tmp.replace(CACHE_FILE)  # atomic, so a crash mid-write can't corrupt the cache


_cache: dict = _load_cache()


class SerpApiError(Exception):
    """Raised when SerpApi returns an error payload or an HTTP error."""
    pass


def _cache_key(params: dict) -> str:
    relevant = {k: v for k, v in params.items() if k != "api_key"}
    return json.dumps(relevant, sort_keys=True)


async def _get(client: httpx.AsyncClient, params: dict) -> dict:
    if not SERPAPI_KEY:
        raise SerpApiError(
            "SERPAPI_KEY is not set. Copy .env.example to .env and add your key."
        )

    params = {**params, "api_key": SERPAPI_KEY}
    key = _cache_key(params)
    entry = _cache.get(key)
    if entry and time.time() - entry["saved_at"] < CACHE_TTL_SECONDS:
        return entry["data"]

    try:
        resp = await client.get(SERPAPI_BASE_URL, params=params, timeout=20.0)
        resp.raise_for_status()
    except httpx.TimeoutException:
        raise SerpApiError(f"SerpApi request timed out ({params.get('engine')}).")
    except httpx.HTTPStatusError as e:
        raise SerpApiError(f"SerpApi HTTP error {e.response.status_code}: {e.response.text[:200]}")

    data = resp.json()
    if data.get("error"):
        raise SerpApiError(data["error"])

    _cache[key] = {"saved_at": time.time(), "data": data}
    _save_cache()
    return data


async def get_flights(
    client: httpx.AsyncClient,
    departure_id: str,
    arrival_id: str,
    outbound_date: str,
    return_date: str,
    currency: str = "INR",
    prefer: str = "cheapest",
) -> dict:
    """
    Calls engine=google_flights. prefer="nonstop" picks the cheapest nonstop
    option, falling back to the cheapest overall when the route has none.
    prefer="cheapest" still takes a nonstop if it's within NONSTOP_TOLERANCE
    of the cheapest fare.
    Either way it's the same API call, so switching costs no quota.
    Returns a normalized dict:
        {"price": float | None, "total_duration": int | None, "type": str,
         "airline": str | None, "airline_logo": str | None,
         "google_flights_url": str | None, "price_insights": dict | None,
         "nonstop_unavailable": bool}
    Raises SerpApiError on failure — caller decides whether to skip the
    destination or surface the error.
    """
    params = {
        "engine": "google_flights",
        "departure_id": departure_id,
        "arrival_id": arrival_id,
        "outbound_date": outbound_date,
        "return_date": return_date,
        "currency": currency,
        "hl": "en",
    }
    data = await _get(client, params)

    # Google splits results into best_flights and other_flights, and the
    # cheapest one is sometimes in other_flights — look at both.
    candidates = [
        f for f in (data.get("best_flights") or []) + (data.get("other_flights") or [])
        if f.get("price") is not None
    ]
    if not candidates:
        raise SerpApiError(f"No flights found for {departure_id} -> {arrival_id}")

    nonstop_unavailable = False
    if prefer == "nonstop":
        nonstop = [f for f in candidates if len(f.get("flights", [])) <= 1]
        if nonstop:
            candidates = nonstop
        else:
            nonstop_unavailable = True

    # Cheapest first; at equal price prefer fewer legs, then shorter trips.
    by_price = lambda f: (f["price"], len(f.get("flights", [])), f.get("total_duration") or float("inf"))
    cheapest = min(candidates, key=by_price)
    if prefer == "cheapest" and len(cheapest.get("flights", [])) > 1:
        nonstop_close = [
            f for f in candidates
            if len(f.get("flights", [])) <= 1 and f["price"] <= cheapest["price"] * (1 + NONSTOP_TOLERANCE)
        ]
        if nonstop_close:
            cheapest = min(nonstop_close, key=by_price)
    flights_legs = cheapest.get("flights", [])
    layovers = cheapest.get("layovers", [])

    airlines = []
    for leg in flights_legs:
        if leg.get("airline") and leg["airline"] not in airlines:
            airlines.append(leg["airline"])

    return {
        "price": cheapest.get("price"),
        "total_duration": cheapest.get("total_duration"),  # minutes
        "type": "Nonstop" if len(flights_legs) <= 1 else f"{len(layovers)} stop(s)",
        "airline": " + ".join(airlines) or None,
        "airline_logo": cheapest.get("airline_logo")
        or (flights_legs[0].get("airline_logo") if flights_legs else None),
        "google_flights_url": data.get("search_metadata", {}).get("google_flights_url"),
        "price_insights": _price_insights(data.get("price_insights")),
        "nonstop_unavailable": nonstop_unavailable,
    }


def _price_insights(raw: dict) -> dict:
    """
    Google Flights' own verdict on whether this route is cheap right now
    ("low" / "typical" / "high") plus the usual price range. It comes back in
    the same response we already pay for, so it costs no extra API calls.
    """
    if not raw or not raw.get("price_level"):
        return None
    typical = raw.get("typical_price_range") or []
    return {
        "level": raw["price_level"],
        "typical_low": typical[0] if len(typical) == 2 else None,
        "typical_high": typical[1] if len(typical) == 2 else None,
    }


async def get_hotels(
    client: httpx.AsyncClient,
    query: str,
    check_in_date: str,
    check_out_date: str,
    currency: str = "INR",
) -> dict:
    """
    Calls engine=google_hotels. Returns:
        {"avg_price_per_night": float,   # 25th-percentile nightly rate among the hotels listed
         "picks": list,                  # up to 3 well-rated hotels at or under that rate
         "google_hotels_url": str | None}
    """
    params = {
        "engine": "google_hotels",
        "q": query,
        "check_in_date": check_in_date,
        "check_out_date": check_out_date,
        "currency": currency,
        "hl": "en",
    }
    data = await _get(client, params)

    # Google returns properties by relevance, not price, and often mixes
    # 5-star hotels with whole 2-3BHK vacation rentals. Neither is what a
    # budget traveller books, so: hotels only (unless a place has too few to
    # go on), and budget with the 25th-percentile rate — a decent cheaper
    # hotel — rather than a median dragged up by the luxury ones.
    properties = [p for p in data.get("properties", []) if _nightly_rate(p)]
    hotels = [p for p in properties if p.get("type") == "hotel"]
    if len(hotels) >= MIN_HOTELS_FOR_BENCHMARK:
        properties = hotels

    if not properties:
        raise SerpApiError(f"No hotel rates found for '{query}'")

    nightly = _lower_quartile([_nightly_rate(p) for p in properties])
    return {
        "avg_price_per_night": nightly,
        "picks": _hotel_picks(properties, nightly),
        "google_hotels_url": data.get("search_metadata", {}).get("google_hotels_url"),
    }


def _nightly_rate(prop: dict):
    # Google sometimes lists a property at 0 — not a real rate
    return prop.get("rate_per_night", {}).get("extracted_lowest") or None


def _lower_quartile(prices: list) -> float:
    prices = sorted(prices)
    if len(prices) < 4:
        return prices[0]
    return statistics.quantiles(prices, n=4)[0]


def _hotel_picks(properties: list, nightly: float) -> list:
    """
    Up to 3 concrete hotels the user could actually book, priced at or below
    the nightly rate we budgeted with (so the total shown stays honest).
    Hotels with enough reviews to trust come first, then by rating — so a
    5.0 from 5 reviews doesn't outrank a 4.4 from 2,000.
    """
    picks = []
    for p in properties:
        price = _nightly_rate(p)
        if not price or price > nightly:
            continue
        images = p.get("images") or []
        picks.append({
            "name": p.get("name"),
            "price_per_night": price,
            "rating": p.get("overall_rating"),
            "reviews": p.get("reviews"),
            "hotel_class": p.get("hotel_class"),
            "image": images[0].get("thumbnail") if images else None,
            "image_large": images[0].get("original_image") if images else None,
            "link": p.get("link"),
        })
    picks.sort(key=lambda h: ((h["reviews"] or 0) >= MIN_REVIEWS_TO_TRUST, h["rating"] or 0), reverse=True)
    return picks[:3]


async def fetch_destination_data(
    client: httpx.AsyncClient,
    destination: dict,
    departure_id: str,
    outbound_date: str,
    return_date: str,
    currency: str = "INR",
    flight_pref: str = "cheapest",
) -> dict:
    """
    Fires the Flights and Hotels calls for one destination concurrently.
    Returns a dict with destination info merged in, plus an "error" key
    (None on success) so the caller can skip failed destinations without
    the whole search failing.
    """
    try:
        flights, hotels = await asyncio.gather(
            get_flights(
                client, departure_id, destination["arrival_id"], outbound_date, return_date,
                currency, flight_pref,
            ),
            get_hotels(
                client, destination["hotel_query"], outbound_date, return_date, currency
            ),
        )
        return {**destination, "flights": flights, "hotels": hotels, "error": None}
    except SerpApiError as e:
        return {**destination, "flights": None, "hotels": None, "error": str(e)}
