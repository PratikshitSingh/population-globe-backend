"""Population Globe API.

Run locally:   uvicorn main:app --reload --port 8000
Deploy:        Vercel detects `app` in main.py (set the project root to backend/).

Every setting (CORS origins, refresh intervals, cache lifetimes, upstream URLs,
optional API keys) is defined in config.py and can be overridden by environment variable.
"""

import time

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

import config
import sources

app = FastAPI(title="Population Globe API", version="0.2.0")

app.add_middleware(CORSMiddleware, allow_origins=config.ALLOWED_ORIGINS, allow_methods=["GET"], allow_headers=["*"])
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
        "optionalKeys": {k: bool(getattr(config, k)) for k in ("GITHUB_TOKEN", "CENSUS_API_KEY", "YOUTUBE_API_KEY", "FLICKR_API_KEY")},
    }


@app.get("/api/cities")
async def cities(response: Response):
    try:
        data = await sources.get_cities()
    except Exception as e:
        raise _upstream_error("City data", e)
    # The frontend adds a cache-busting `t` whenever /api/status reports newer data, so a
    # given URL always maps to one dataset version and can be cached by browser and CDN.
    _cdn(response, config.DATASET_TTL_SECONDS, browser=config.CITIES_BROWSER_TTL_SECONDS)
    return data


@app.get("/api/status")
async def status(response: Response):
    """When each upstream was last pulled and how often it refreshes. Polled by the frontend."""
    response.headers["Cache-Control"] = (
        f"public, max-age={config.STATUS_BROWSER_TTL_SECONDS}, s-maxage={config.STATUS_CDN_TTL_SECONDS}"
    )
    return {"now": int(time.time()), "sources": sources.status()}


@app.get("/api/rent/history")
async def rent_history(response: Response):
    """Yearly Zillow rent since 2015 for matched US cities, keyed by GeoNames ID."""
    try:
        data = await sources.get_rent_history()
    except Exception as e:
        raise _upstream_error("Rent history", e)
    _cdn(response, config.DATASET_TTL_SECONDS)
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
    _cdn(response, ttl, browser=min(ttl, config.FEED_BROWSER_MAX_TTL_SECONDS))
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
    _cdn(response, config.PROFILE_CDN_TTL_SECONDS)
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
    _cdn(response, config.AMENITIES_TTL_SECONDS)
    return data
