# Trip Matcher

Hackathon submission — SerpApi India Hackathon, Travel & Local Discovery track.

**Most travel sites make you pick a destination first, then tell you what it
costs. Trip Matcher works the other way round: tell it your budget, how many
nights you have, and the kind of trip you're in the mood for — it tells you
where you can actually afford to go, and gets you to the booking page in one
click.**

You pick a vibe (Beach / Mountains / City / Nature), choose India,
International or both, and set a budget. It pulls live flight and hotel prices
for a curated shortlist of destinations via SerpApi, drops anything over
budget, and returns the top 3 ranked by how much money they leave you (ties
broken by shorter flight time) — each with an airline, real hotel picks, a
"is this a good price?" verdict, and pre-filled booking links.

## What you get for each trip

- **Total cost vs your budget** — flight + hotel nights, with a bar showing
  how much of the budget it uses.
- **The flight** — airline, nonstop or not, duration, outbound/return legs.
  Choose **Cheapest flight** or **Nonstop only**; both come from the same
  API response, so switching costs no quota. "Cheapest" still picks a
  nonstop when it's within 15% of the cheapest connecting fare.
- **Price insight** — Google Flights' verdict on whether this route is
  cheap, typical or expensive right now, plus its usual price range.
- **Three real hotels** at or under the nightly rate we budgeted with, with
  photos, ratings and links to the hotel.
- **One-click booking** — MakeMyTrip, Skyscanner and Google Flights for the
  flight; Booking.com and Google Hotels for the stay — all pre-filled with
  the same route and dates.
- **Compare all** — every destination that was priced, how far over or under
  budget it came in, so "nothing fits" still tells you how close you were.

## How it uses SerpApi

Each destination costs exactly two SerpApi calls, and the app squeezes as
much out of each response as it can:

| Engine | What we use from the response |
|---|---|
| `google_flights` | Cheapest round trip (or a nonstop within 15% of it) across `best_flights` + `other_flights`, airline name and logo, stops, duration, `price_insights` (low/typical/high + typical range), and the Google Flights link |
| `google_hotels` | 25th-percentile nightly rate among the hotels listed, skipping vacation rentals (used for the total), well-reviewed hotels at or under that rate, their photos/ratings/links, and the Google Hotels link |

SerpApi's `booking_options` need an extra call per flight (via
`booking_token`), so booking links are built from the search parameters
instead — see `backend/booking_links.py`.

## How it works

- `backend/destinations.json` holds a curated list of 9–16 destinations per
  vibe, in India and abroad. This is deliberate — it bounds the number of
  API calls per search instead of dynamically discovering destinations.
  The **India / International / Both** filter picks which ones are searched,
  and your own departure airport is always skipped.
- Each destination also has a hand-picked cover photo (1280px, from
  Wikimedia Commons, credited on the card). If it fails to load, the card
  falls back to the hotels' full-size photos, then their thumbnails, then a
  gradient for the vibe.
- For each destination, the backend fires the Google Flights and Google
  Hotels calls concurrently, in batches of 5 destinations at a time.
- **Raw SerpApi responses are cached on disk** (`backend/.cache/`) for
  `CACHE_TTL_HOURS` (default 72), so a search that's been run once costs no
  quota again — even after a server restart. Useful on the free tier's
  250 searches/month.
- `backend/scoring.py` computes `total_cost = flight_price + (hotel_nightly_rate
  * nights)`, discards anything over budget, and ranks what's left by
  savings (then by flight duration).
- Destinations where either API call fails (timeout, no results, bad route)
  are skipped, not treated as a hard error — the frontend shows which ones
  were skipped. If *every* lookup fails, it says so plainly (usually a bad
  key or exhausted quota) rather than pretending nothing fit the budget.

## Setup

**Requirements:** Python 3.9+, a SerpApi key ([free tier signup](https://serpapi.com/users/sign_up)).

```bash
cd backend
pip install -r requirements.txt
cp ../.env.example ../.env
# edit .env and paste your real SERPAPI_KEY
```

## Run it

```bash
cd backend
python3 -m uvicorn main:app --reload --port 8000
```

Open **http://localhost:8000** — the backend serves the frontend directly,
so there's nothing else to start.

## Quota cost

A search costs 2 calls per destination searched, e.g. City + India is
7 destinations = 14 calls; City + Both is 16 = 32 calls. Repeating a
search (same departure, dates, nights, vibe and region) within the cache
window costs nothing.

## Project structure

```
/trip-matcher
  /backend
    main.py              # FastAPI app, routes, region filter
    serpapi_client.py    # flights + hotels calls (async, disk-cached)
    scoring.py           # scoring/ranking logic
    booking_links.py     # pre-filled MakeMyTrip / Skyscanner / Booking.com links
    destinations.json    # curated candidate list
    requirements.txt
  /frontend
    index.html           # the whole UI (no build step)
  .env.example
  README.md
```

## API

`GET /api/vibes` → `{"vibes": ["Beach", "Mountains", "City", "Nature"]}`

`POST /api/search`
```json
{
  "departure_id": "BLR",
  "budget": 60000,
  "nights": 4,
  "vibe": "City",
  "region": "india",
  "flight_pref": "nonstop",
  "outbound_date": "2026-11-10",
  "currency": "INR"
}
```
`region` is `india`, `international` or `both` (default). `flight_pref` is
`cheapest` (default) or `nonstop` — nonstop falls back to the cheapest
option with stops when a route has no nonstop flights. `outbound_date` is
optional (defaults to 30 days out).

→ up to 3 ranked results (with flight, hotel picks, price insights and
booking links), every destination that was priced, and any that were
skipped.

## Known limitations

- Hotel cost is the 25th-percentile rate of the hotels Google lists (rentals
  excluded). Where Google lists mostly upscale hotels (e.g. New Delhi) it
  still comes out higher than a budget traveller would pay.
- Requests use SerpApi's default `gl=us` market, so fares may differ
  slightly from what Google India or MakeMyTrip shows.
- Budget is per person; hotel price is per room.
- MakeMyTrip links treat a trip as domestic when the destination is in
  India, i.e. they assume you're flying from India.
- No auth, no database.

## Demo script (under 3 minutes)

Run the demo search once beforehand so it's served from the cache.

1. (10s) "Instead of picking a destination first, you tell us your constraints."
2. (20s) Fill the form live: BLR, ₹60,000, 4 nights, City, India.
3. (20s) Results — 3 ranked cards with cost, budget bar and price insight.
4. (20s) Show the hotel picks, then click "Book on MakeMyTrip" — it opens
   with the route and dates already filled in.
5. (15s) Open "Compare all" — how close the over-budget cities came.
6. (15s) Quick flash of `serpapi_client.py` — the parallel Flights + Hotels
   calls, showing it's real SerpApi data, not mocked.
7. (10s) Close: "Budget-first trip discovery, from idea to booking page, without 20 tabs open."
