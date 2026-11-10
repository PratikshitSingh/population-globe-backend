# Population Globe — backend

FastAPI service that fetches live public data, caches it, and serves it to the
[frontend](https://github.com/PratikshitSingh/population-globe-frontend).

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn main:app --reload --port 8000
```

## Endpoints

| Endpoint | Returns | Upstream | Cache |
| --- | --- | --- | --- |
| `GET /api/cities` | ~25,000 cities: population, rent, OnlyFans per 1k, time zone; country stats | GeoNames, Zillow ZORI, World Bank | 24 h |
| `GET /api/rent/history` | yearly rent since 2015 for ~2,000 US cities | Zillow ZORI | 24 h |
| `GET /api/live/earthquakes` | M2.5+ quakes, last 24 h | USGS | 1 min |
| `GET /api/live/events` | open wildfires, severe storms, volcanoes | NASA EONET | 15 min |
| `GET /api/live/aurora` | aurora probability grid | NOAA SWPC | 10 min |
| `GET /api/live/aircraft` | airborne aircraft snapshot | OpenSky Network | 15 min |
| `GET /api/live/satellites` | orbit data for the ISS and Starlink | CelesTrak | 6 h per group |
| `GET /api/live/cables` | submarine cables | TeleGeography | 7 days |
| `GET /api/cities/{id}/profile` | Wikidata population, people born there, Wikipedia views, GitHub developers, Commons photos, weather, air quality | Wikidata, Wikimedia, GitHub, Open-Meteo | 15 min – 7 days per part |
| `GET /api/cities/{id}/amenities` | bars, cafés, nightclubs, gyms, coworking near the centre | OpenStreetMap Overpass | 30 days |
| `GET /api/health` | status, cache age, which optional keys are set | — | — |

Caching is two-layer: an in-process TTL cache (which serves stale data if an upstream
is down) and `Cache-Control: s-maxage / stale-while-revalidate` headers so Vercel's CDN
answers most requests without invoking the function.

## Environment variables

| Variable | Purpose |
| --- | --- |
| `ALLOWED_ORIGINS` | Comma-separated frontend origins for CORS. Default `http://localhost:5199`. |
| `API_CONTACT` | URL or email added to the User-Agent sent upstream. Wikimedia blocks clients that don't identify themselves. |
| `GITHUB_TOKEN` | Optional. Raises GitHub's search limit above 10 requests a minute. |
| `CENSUS_API_KEY` | Optional. Adds US median 1-bed rent, income and age to city profiles. |
| `YOUTUBE_API_KEY` | Optional. Adds a count of videos geotagged near the city. |
| `FLICKR_API_KEY` | Optional. Adds a count of photos geotagged near the city. |

## Deploying to Vercel

Import this repo as a project; Vercel should detect `app` in `main.py`. This has not been
tested yet. Things to watch:

- OpenSky is known to refuse some cloud-provider IP ranges; the aircraft layer may need
  OpenSky credentials or a different host.
- CelesTrak returns 403 to a client that re-downloads the same group within about 2 hours.
  The 6-hour cache respects that, but cold starts on several instances could trip it.
- The in-process cache is per instance and is lost on cold start; the CDN headers do most of the work.

## What is real and what is estimated

- **Real:** population (GeoNames), US rent (Zillow), every `/api/live/*` feed, everything in `/profile`.
- **Estimated:** rent outside the US and all OnlyFans figures (`estimates.py`). No public API offers either.

## Data licences

GeoNames CC BY 4.0 · TeleGeography cables CC BY-NC-SA 4.0 (non-commercial) · Open-Meteo free tier is
non-commercial · OpenStreetMap ODbL · Zillow data requires attribution.
