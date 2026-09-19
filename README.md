# Trip Matcher

Hackathon submission — SerpApi Travel & Local Discovery track.

You give it a budget, trip length, and a vibe (Beach / Mountains / City / Nature).
It searches real flight and hotel prices across a curated shortlist of
destinations for that vibe via SerpApi, filters out anything over budget,
and returns the top 3 ranked by how much money they save you (ties broken
by shorter total flight time).

## How it works

- `backend/destinations.json` holds a static, curated list of 8–10
  destinations per vibe tag. This is deliberate — it bounds the number of
  API calls per search instead of dynamically discovering destinations.
- For each candidate destination, the backend fires one Google Flights call
  and one Google Hotels call (via SerpApi), concurrently, in batches of 5,
  to stay under SerpApi's free-tier rate limit of 50 requests/hour.
- Results are cached in memory for the life of the server process, so
  repeated searches with the same dates don't burn extra API calls.
- `backend/scoring.py` computes `total_cost = flight_price + (avg_hotel_price
  * nights)`, discards anything over budget, and ranks what's left by
  savings (then by flight duration).
- Destinations where either API call fails (timeout, no results, bad route)
  are skipped, not treated as a hard error — the frontend shows which ones
  were skipped rather than failing the whole search.

## Setup

**Requirements:** Python 3.10+, a SerpApi key ([free tier signup](https://serpapi.com/users/sign_up)).

```bash
cd backend
pip install -r requirements.txt
cp ../.env.example ../.env
# edit .env and paste your real SERPAPI_KEY
```

## Run it

```bash
cd backend
uvicorn main:app --reload --port 8000
```

Open **http://localhost:8000** — the backend serves the frontend directly,
so there's nothing else to start.

## Project structure

```
/trip-matcher
  /backend
    main.py             # FastAPI app, routes
    serpapi_client.py    # wraps flights + hotels calls (async, cached)
    scoring.py           # scoring/ranking logic
    destinations.json    # curated candidate list
    requirements.txt
  /frontend
    index.html
    app.js
  .env.example
  README.md
```

## API

`GET /api/vibes` → `{"vibes": ["Beach", "Mountains", "City", "Nature"]}`

`POST /api/search`
```json
{
  "departure_id": "BLR",
  "budget": 800,
  "nights": 6,
  "vibe": "Beach",
  "outbound_date": "2026-11-10"
}
```
→ top 3 ranked results, plus a list of any destinations skipped due to a
failed API call.

## Known limitations / stretch goals

- No auth, no database — single-session, in-memory cache only.
- Destination photos aren't wired up yet (static image mapping was scoped
  as a nice-to-have, not core).
- `booking_options` are passed through from SerpApi's Flights response
  when present, surfaced as a "Book this flight" link on each card.
- Not yet built: budget-slider client-side re-ranking, and a
  departure-continent picker to swap which curated destination list is used.

## Demo script (under 3 minutes)

1. (10s) "Instead of picking a destination first, you tell us your constraints."
2. (20s) Fill form live: $800 budget, 6 nights, Bengaluru departure, Beach vibe.
3. (20s) Show results populating — 3 ranked cards with cost breakdowns.
4. (15s) Click a card, show the "why" reasoning.
5. (15s) Quick flash of code — the parallel Flights + Hotels calls, showing
   it's real SerpApi data, not mocked.
6. (10s) Close: "Cheaper, faster trip discovery without 20 browser tabs open."
