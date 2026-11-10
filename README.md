# Population Globe — backend

FastAPI service that fetches live public data, caches it, and serves it to the
[frontend](https://github.com/PratikshitSingh/population-globe-frontend).

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn main:app --reload --port 8000
```

## Endpoints

Cache times below are the defaults from `config.py`.

| Endpoint | Returns | Upstream | Cache |
| --- | --- | --- | --- |
| `GET /api/cities` | ~25,000 cities: population, rent, OnlyFans per 1k, time zone; country stats | GeoNames, Zillow ZORI, World Bank | 1 h |
| `GET /api/rent/history` | yearly rent since 2015 for ~2,000 US cities | Zillow ZORI | 1 h |
| `GET /api/live/earthquakes` | M2.5+ quakes, last 24 h | USGS | 1 min |
| `GET /api/live/events` | open wildfires, severe storms, volcanoes | NASA EONET | 15 min |
| `GET /api/live/aurora` | aurora probability grid | NOAA SWPC | 10 min |
| `GET /api/live/aircraft` | airborne aircraft snapshot | OpenSky Network | 15 min |
| `GET /api/live/satellites` | orbit data for the ISS and Starlink | CelesTrak | 6 h per group |
| `GET /api/live/cables` | submarine cables | TeleGeography | 7 days |
| `GET /api/cities/{id}/profile` | Wikidata population, people born there, Wikipedia views, GitHub developers, Commons photos, weather, air quality | Wikidata, Wikimedia, GitHub, Open-Meteo | 15 min – 7 days per part |
| `GET /api/cities/{id}/amenities` | bars, cafés, nightclubs, gyms, coworking near the centre | OpenStreetMap Overpass | 30 days |
| `GET /api/status` | when each source was last pulled and its refresh interval | — | 15 s |
| `GET /api/health` | status, cache age, which optional keys are set | — | — |

Caching is two-layer: an in-process TTL cache (which serves stale data if an upstream
is down) and `Cache-Control: s-maxage / stale-while-revalidate` headers so Vercel's CDN
answers most requests without invoking the function.

## Configuration

Every setting lives in [`config.py`](config.py): upstream URLs, how often each source is
re-pulled, browser and CDN cache lifetimes, timeouts, thresholds and the optional API keys.
No other file reads the environment or hardcodes an interval.

Each scalar can be overridden by an environment variable of the same name, so Vercel
settings win over the file without a code change. The ones you are most likely to touch:

| Variable | Default | Purpose |
| --- | --- | --- |
| `ALLOWED_ORIGINS` | `http://localhost:5199` | Comma-separated frontend origins for CORS. |
| `API_CONTACT` | placeholder | URL or email added to the User-Agent sent upstream. Wikimedia blocks clients that don't identify themselves. |
| `DATASET_TTL_SECONDS` | `3600` | How often cities, rent and country stats are re-pulled. |
| `FEED_TTL_<NAME>` | see `config.py` | Refresh interval for one live feed, e.g. `FEED_TTL_AIRCRAFT=300`. Names: `EARTHQUAKES`, `EVENTS`, `AURORA`, `AIRCRAFT`, `SATELLITES`, `CABLES`. |
| `FEED_CDN_TTL_<NAME>` | see `config.py` | How long the CDN may serve that feed. |
| `PROFILE_TTL_<NAME>` | see `config.py` | Refresh interval for one city-profile lookup, e.g. `PROFILE_TTL_WEATHER=300`. |
| `GITHUB_TOKEN` | unset | Optional. Raises GitHub's search limit above 10 requests a minute. |
| `CENSUS_API_KEY` | unset | Optional. Adds US median 1-bed rent, income and age to city profiles. |
| `YOUTUBE_API_KEY` | unset | Optional. Adds a count of videos geotagged near the city. |
| `FLICKR_API_KEY` | unset | Optional. Adds a count of photos geotagged near the city. |

`GET /api/status` reports the refresh interval actually in effect for each source.

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
