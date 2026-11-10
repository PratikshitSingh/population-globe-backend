"""Population Globe API.

Run locally:   uvicorn main:app --reload --port 8000
Deploy:        Vercel detects `app` in main.py (set the project root to backend/).

Env:
  ALLOWED_ORIGINS   comma-separated frontend origins for CORS
                    (default: http://localhost:5199). Use "*" to allow any.
  API_CONTACT       URL or email put in the User-Agent sent to upstream APIs
                    (Wikimedia blocks clients that don't identify themselves).
  GITHUB_TOKEN      optional; raises the GitHub search rate limit.
  CENSUS_API_KEY    optional; adds US 1-bed rent, income and age to city profiles.
  YOUTUBE_API_KEY   optional; adds geotagged-video counts to city profiles.
  FLICKR_API_KEY    optional; adds geotagged-photo counts to city profiles.
"""

import os

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

import sources

app = FastAPI(title="Population Globe API", version="0.2.0")

origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:5199").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET"], allow_headers=["*"])
app.add_middleware(GZipMiddleware, minimum_size=1024)


def _cdn(response: Response, s_maxage: int, browser: int | None = None) -> None:
    """Vercel's CDN honours s-maxage / stale-while-revalidate, so most requests never
    reach the function; the in-process TTL cache covers the ones that do."""
    browser = min(s_maxage, 3600) if browser is None else browser
    response.headers["Cache-Control"] = (
        f"public, max-age={browser}, s-maxage={s_maxage}, stale-while-revalidate={s_maxage * 4}"
    )


def _upstream_error(what: str, e: Exception) -> HTTPException:
    return HTTPException(502, f"{what} unavailable: {type(e).__name__}: {str(e)[:200]}")


@app.get("/api/health")
async def health():
    age = sources.cache.age("dataset")
    return {
        "ok": True,
        "datasetCacheAgeSeconds": None if age is None else round(age),
        "optionalKeys": {k: bool(os.getenv(k)) for k in ("GITHUB_TOKEN", "CENSUS_API_KEY", "YOUTUBE_API_KEY", "FLICKR_API_KEY")},
    }


@app.get("/api/cities")
async def cities(response: Response):
    try:
        data = await sources.get_cities()
    except Exception as e:
        raise _upstream_error("City data", e)
    _cdn(response, sources.DAY)
    return data


@app.get("/api/rent/history")
async def rent_history(response: Response):
    """Yearly Zillow rent since 2015 for matched US cities, keyed by GeoNames ID."""
    try:
        data = await sources.get_rent_history()
    except Exception as e:
        raise _upstream_error("Rent history", e)
    _cdn(response, sources.DAY)
    return data


@app.get("/api/live/{feed}")
async def live(feed: str, response: Response):
    """Global live layers: earthquakes, events, aurora, aircraft, satellites, cables."""
    if feed not in sources.FEEDS:
        raise HTTPException(404, f"Unknown feed. Available: {', '.join(sources.FEEDS)}")
    try:
        data = await sources.get_feed(feed)
    except Exception as e:
        raise _upstream_error(feed, e)
    ttl = sources.FEEDS[feed][2]
    _cdn(response, ttl, browser=min(ttl, 300))
    return data


@app.get("/api/cities/{geoname_id}/profile")
async def city_profile(geoname_id: int, response: Response):
    """Live lookups for one city: Wikidata, Wikipedia pageviews, GitHub, Commons, weather, and keyed extras."""
    try:
        data = await sources.get_profile(geoname_id)
    except Exception as e:
        raise _upstream_error("City profile", e)
    if data is None:
        raise HTTPException(404, "Unknown city")
    _cdn(response, 15 * sources.MINUTE)
    return data


@app.get("/api/cities/{geoname_id}/amenities")
async def city_amenities(geoname_id: int, response: Response):
    """Bars, cafés, nightclubs, gyms and coworking spaces near the centre (OpenStreetMap)."""
    try:
        data = await sources.get_amenities(geoname_id)
    except Exception as e:
        raise _upstream_error("OpenStreetMap Overpass", e)
    if data is None:
        raise HTTPException(404, "Unknown city")
    _cdn(response, 30 * sources.DAY)
    return data
