"""
Scoring/ranking logic for Trip Matcher.

total_cost = flight_price + (avg_hotel_price_per_night * nights)
if total_cost > budget: discard
else:
    savings = budget - total_cost
    score = savings                      (primary sort, descending)
    tiebreaker = total_flight_duration   (ascending — shorter wins)

Returns the top 3 destinations as result cards ready for the API response.
"""

from typing import Optional

CURRENCY_SYMBOLS = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}


def _symbol(currency: str) -> str:
    return CURRENCY_SYMBOLS.get(currency, currency + " ")


def _format_duration(total_minutes: Optional[int]) -> str:
    if not total_minutes:
        return "duration unknown"
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours}h {minutes}m" if minutes else f"{hours}h"


def _reason(destination: dict, savings: float, is_shortest: bool, symbol: str) -> str:
    flight_type = destination["flights"]["type"]
    bits = []
    if savings > 0:
        bits.append(f"{symbol}{savings:.0f} under budget")
    if flight_type == "Nonstop":
        bits.append("a direct flight")
    if is_shortest:
        bits.append("the shortest total travel time")
    if not bits:
        bits.append("fits your budget")
    if len(bits) == 1:
        return bits[0][0].upper() + bits[0][1:]
    return f"{bits[0][0].upper()}{bits[0][1:]} with {', '.join(bits[1:])}"


def build_considered(destinations: list, budget: float, nights: int) -> list:
    """
    Every destination with a valid flight + hotel lookup, fitting the budget
    or not, sorted cheapest first. Lets the frontend show the full landscape
    that was searched — not just the top 3 winners — so a "nothing fit"
    search still tells the user how close they were and by how much.
    """
    rows = []
    for dest in destinations:
        if dest.get("error"):
            continue

        flight_price = dest["flights"]["price"]
        hotel_nightly = dest["hotels"]["avg_price_per_night"]
        if flight_price is None or hotel_nightly is None:
            continue

        total_cost = flight_price + (hotel_nightly * nights)
        rows.append({
            "name": dest["name"],
            "country": dest["country"],
            "total_cost": round(total_cost, 2),
            "fits": total_cost <= budget,
            "diff": round(budget - total_cost, 2),
            "flight_type": dest["flights"]["type"],
            "flight_duration": _format_duration(dest["flights"]["total_duration"]),
        })

    rows.sort(key=lambda r: r["total_cost"])
    return rows


def score_destinations(destinations: list, budget: float, nights: int, currency: str = "INR") -> list:
    """
    destinations: list of dicts from serpapi_client.fetch_destination_data
    (each with "flights", "hotels", "error" keys already populated).

    Returns up to 3 result-card dicts, ranked best first.
    """
    scored = []

    for dest in destinations:
        if dest.get("error"):
            continue  # skipped destination — surfaced separately as a warning

        flight_price = dest["flights"]["price"]
        hotel_nightly = dest["hotels"]["avg_price_per_night"]
        if flight_price is None or hotel_nightly is None:
            continue

        total_cost = flight_price + (hotel_nightly * nights)
        if total_cost > budget:
            continue

        savings = budget - total_cost
        scored.append({
            **dest,
            "total_cost": round(total_cost, 2),
            "savings": round(savings, 2),
            "flight_price": flight_price,
            "hotel_nightly": round(hotel_nightly, 2),
            "total_duration": dest["flights"]["total_duration"] or float("inf"),
        })

    # Primary: savings descending. Tiebreak: total_duration ascending.
    scored.sort(key=lambda d: (-d["savings"], d["total_duration"]))

    top3 = scored[:3]
    if not top3:
        return []

    shortest_duration = min(d["total_duration"] for d in top3)
    symbol = _symbol(currency)

    results = []
    for dest in top3:
        is_shortest = dest["total_duration"] == shortest_duration
        results.append({
            "name": dest["name"],
            "country": dest["country"],
            "total_cost": dest["total_cost"],
            "savings": dest["savings"],
            "breakdown": (
                f"Flight: {symbol}{dest['flight_price']:.0f} + "
                f"Hotel: {symbol}{dest['hotel_nightly']:.0f}/night x {nights} nights"
            ),
            "flight_type": dest["flights"]["type"],
            "flight_duration": _format_duration(
                None if dest["total_duration"] == float("inf") else dest["total_duration"]
            ),
            "booking_options": dest["flights"]["booking_options"],
            "reason": _reason(dest, dest["savings"], is_shortest, symbol),
        })

    return results
