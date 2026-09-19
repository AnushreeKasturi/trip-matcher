"""
Trip Matcher backend — FastAPI app.

POST /api/search takes the user's constraints (departure city, budget, trip
length, date window, vibe tag), fires SerpApi Flights + Hotels calls for
every candidate destination under that vibe tag (in batches to respect the
50 requests/hour cap), scores the results, and returns the top 3.
"""

import json
import os
import asyncio
from pathlib import Path
from datetime import date, timedelta
from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from serpapi_client import fetch_destination_data, SERPAPI_KEY
from scoring import score_destinations, build_considered

BASE_DIR = Path(__file__).parent
DESTINATIONS = json.loads((BASE_DIR / "destinations.json").read_text())

BATCH_SIZE = 5  # concurrent destination lookups at a time (stays under the 50/hr cap)

app = FastAPI(title="Trip Matcher")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class SearchRequest(BaseModel):
    departure_id: str = Field(..., description="IATA code, e.g. BLR")
    budget: float = Field(..., gt=0)
    nights: int = Field(..., gt=0, le=30)
    vibe: str = Field(..., description="Beach | Mountains | City | Nature")
    outbound_date: Optional[str] = Field(
        None, description="YYYY-MM-DD; defaults to 30 days from today"
    )
    currency: str = "INR"


class ResultCard(BaseModel):
    name: str
    country: str
    total_cost: float
    savings: float
    breakdown: str
    flight_type: str
    flight_duration: str
    booking_options: list
    reason: str


class ConsideredDestination(BaseModel):
    name: str
    country: str
    total_cost: float
    fits: bool
    diff: float  # positive = under budget by this much, negative = over by this much
    flight_type: str
    flight_duration: str


class SearchResponse(BaseModel):
    results: list[ResultCard]
    skipped: list[str]  # destinations that errored out, for the "no results" UX
    searched_count: int
    search_error: Optional[str] = None  # set when every lookup failed outright
    considered: list[ConsideredDestination] = []  # every successfully-priced destination, cheapest first


@app.get("/api/vibes")
async def get_vibes():
    """Returns available vibe tags for the frontend's select input."""
    return {"vibes": list(DESTINATIONS.keys())}


@app.post("/api/search", response_model=SearchResponse)
async def search(req: SearchRequest):
    if not SERPAPI_KEY:
        raise HTTPException(
            status_code=500,
            detail="SERPAPI_KEY is not configured on the server. See .env.example.",
        )

    if req.vibe not in DESTINATIONS:
        raise HTTPException(status_code=400, detail=f"Unknown vibe '{req.vibe}'")

    candidates = DESTINATIONS[req.vibe]

    outbound = req.outbound_date or (date.today() + timedelta(days=30)).isoformat()
    return_date = (
        date.fromisoformat(outbound) + timedelta(days=req.nights)
    ).isoformat()

    results_raw = []
    async with httpx.AsyncClient() as client:
        for i in range(0, len(candidates), BATCH_SIZE):
            batch = candidates[i : i + BATCH_SIZE]
            batch_results = await asyncio.gather(*[
                fetch_destination_data(
                    client,
                    dest,
                    req.departure_id,
                    outbound,
                    return_date,
                    req.currency,
                )
                for dest in batch
            ])
            results_raw.extend(batch_results)

    skipped = [d["name"] for d in results_raw if d.get("error")]
    top3 = score_destinations(results_raw, req.budget, req.nights, req.currency)
    considered = build_considered(results_raw, req.budget, req.nights)

    # If every single destination errored out (bad API key, rate limit, outage),
    # that's a search failure, not "nothing fit your budget" — tell the truth so
    # the user doesn't waste time adjusting budget/nights over a config problem.
    search_error = None
    if not top3 and candidates and len(skipped) == len(candidates):
        first_error = next((d["error"] for d in results_raw if d.get("error")), None)
        search_error = (
            f"Every destination lookup failed, so no comparison could be made "
            f"({first_error}). This usually means SERPAPI_KEY in .env is missing, "
            f"invalid, or rate-limited — it isn't about your budget or trip length."
        )

    return SearchResponse(
        results=top3,
        skipped=skipped,
        searched_count=len(candidates),
        search_error=search_error,
        considered=considered,
    )


# Serve the frontend as static files so `uvicorn main:app` alone is enough
# to run the whole thing locally.
frontend_dir = BASE_DIR.parent / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
