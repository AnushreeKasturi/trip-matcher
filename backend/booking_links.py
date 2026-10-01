"""
Deep links that drop the user straight into a pre-filled search on a booking
site — same route, same dates — so "this trip fits" turns into "book it" in
one click.

SerpApi's google_flights engine only returns booking_options when called
with a booking_token (an extra API call per flight), so instead we build
search URLs for well-known sites from the parameters we already have.
"""

from datetime import date
from urllib.parse import urlencode


def flight_links(
    departure_id: str,
    arrival_id: str,
    outbound: str,
    return_date: str,
    domestic: bool,
    google_flights_url: str = None,
) -> list:
    out_d, ret_d = date.fromisoformat(outbound), date.fromisoformat(return_date)

    mmt_itinerary = (
        f"{departure_id}-{arrival_id}-{out_d:%d/%m/%Y}_"
        f"{arrival_id}-{departure_id}-{ret_d:%d/%m/%Y}"
    )
    links = [
        {
            "site": "MakeMyTrip",
            "url": "https://www.makemytrip.com/flight/search?" + urlencode({
                "itinerary": mmt_itinerary,
                "tripType": "R",
                "paxType": "A-1_C-0_I-0",
                "intl": "false" if domestic else "true",
                "cabinClass": "E",
            }, safe="/"),  # MakeMyTrip's own URLs keep the date slashes unescaped
        },
        {
            "site": "Skyscanner",
            "url": (
                f"https://www.skyscanner.co.in/transport/flights/"
                f"{departure_id.lower()}/{arrival_id.lower()}/"
                f"{out_d:%y%m%d}/{ret_d:%y%m%d}/"
            ),
        },
    ]
    if google_flights_url:
        links.append({"site": "Google Flights", "url": google_flights_url})
    return links


def hotel_links(hotel_query: str, outbound: str, return_date: str, google_hotels_url: str = None) -> list:
    links = [
        {
            "site": "Booking.com",
            "url": "https://www.booking.com/searchresults.html?" + urlencode({
                "ss": hotel_query,
                "checkin": outbound,
                "checkout": return_date,
                "group_adults": 1,
                "no_rooms": 1,
            }),
        },
    ]
    if google_hotels_url:
        links.append({"site": "Google Hotels", "url": google_hotels_url})
    return links
