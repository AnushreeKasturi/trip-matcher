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
import asyncio
import httpx

SERPAPI_BASE_URL = "https://serpapi.com/search"
SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")

# Simple in-memory cache for the lifetime of the process. Keyed by a tuple of
# the params that actually affect the result, so repeated searches with the
# same dates/destination within a session don't burn extra API calls.
_cache: dict = {}


class SerpApiError(Exception):
    """Raised when SerpApi returns an error payload or an HTTP error."""
    pass


def _cache_key(engine: str, params: dict) -> tuple:
    relevant = {k: v for k, v in params.items() if k != "api_key"}
    return (engine, tuple(sorted(relevant.items())))


async def _get(client: httpx.AsyncClient, params: dict) -> dict:
    if not SERPAPI_KEY:
        raise SerpApiError(
            "SERPAPI_KEY is not set. Copy .env.example to .env and add your key."
        )

    params = {**params, "api_key": SERPAPI_KEY}
    key = _cache_key(params["engine"], params)
    if key in _cache:
        return _cache[key]

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

    _cache[key] = data
    return data


async def get_flights(
    client: httpx.AsyncClient,
    departure_id: str,
    arrival_id: str,
    outbound_date: str,
    return_date: str,
    currency: str = "INR",
) -> dict:
    """
    Calls engine=google_flights. Returns a normalized dict:
        {"price": float | None, "total_duration": int | None,
         "type": str, "booking_options": list}
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

    # SerpApi returns best_flights and/or other_flights; prefer best_flights.
    candidates = data.get("best_flights") or data.get("other_flights") or []
    if not candidates:
        raise SerpApiError(f"No flights found for {departure_id} -> {arrival_id}")

    cheapest = min(candidates, key=lambda f: f.get("price", float("inf")))
    flights_legs = cheapest.get("flights", [])
    layovers = cheapest.get("layovers", [])

    return {
        "price": cheapest.get("price"),
        "total_duration": cheapest.get("total_duration"),  # minutes
        "type": "Nonstop" if len(flights_legs) <= 1 else f"{len(layovers)} stop(s)",
        "booking_options": data.get("booking_options", []),
    }


async def get_hotels(
    client: httpx.AsyncClient,
    query: str,
    check_in_date: str,
    check_out_date: str,
    currency: str = "INR",
) -> dict:
    """
    Calls engine=google_hotels. Returns {"avg_price_per_night": float | None}
    computed as the median rate_per_night across the first ~10 properties.
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

    properties = data.get("properties", [])[:10]
    nightly_prices = []
    for p in properties:
        rate = p.get("rate_per_night", {})
        # SerpApi gives this as either a formatted string or an "extracted_lowest" int
        val = rate.get("extracted_lowest")
        if val is not None:
            nightly_prices.append(val)

    if not nightly_prices:
        raise SerpApiError(f"No hotel rates found for '{query}'")

    nightly_prices.sort()
    n = len(nightly_prices)
    median = (
        nightly_prices[n // 2]
        if n % 2 == 1
        else (nightly_prices[n // 2 - 1] + nightly_prices[n // 2]) / 2
    )
    return {"avg_price_per_night": median}


async def fetch_destination_data(
    client: httpx.AsyncClient,
    destination: dict,
    departure_id: str,
    outbound_date: str,
    return_date: str,
    currency: str = "INR",
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
                client, departure_id, destination["arrival_id"], outbound_date, return_date, currency
            ),
            get_hotels(
                client, destination["hotel_query"], outbound_date, return_date, currency
            ),
        )
        return {**destination, "flights": flights, "hotels": hotels, "error": None}
    except SerpApiError as e:
        return {**destination, "flights": None, "hotels": None, "error": str(e)}
