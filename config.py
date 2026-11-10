"""All tunable settings for the backend live here: upstream URLs, refresh intervals,
cache lifetimes, limits and thresholds. Nothing else in the code reads the environment.

Every scalar below can be overridden with an environment variable of the same name
(for example `DATASET_TTL_SECONDS=600`). Per-feed and per-lookup lifetimes use
`FEED_TTL_<NAME>`, `FEED_CDN_TTL_<NAME>` and `PROFILE_TTL_<NAME>`, e.g. `FEED_TTL_AIRCRAFT=300`.
All durations are in seconds.
"""

import os

MINUTE, HOUR, DAY = 60, 3600, 86_400


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


def _str(name: str, default: str) -> str:
    return os.getenv(name, default)


def _list(name: str, default: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


# ───────────── Server ─────────────

# Frontend origins allowed by CORS. "*" allows any.
ALLOWED_ORIGINS = _list("ALLOWED_ORIGINS", "http://localhost:5199")

# URL or email for your deployment, sent in the User-Agent so upstream operators can
# reach you. Wikimedia rejects clients that don't identify themselves.
API_CONTACT = _str("API_CONTACT", "local development build; no public URL yet")
USER_AGENT = f"PopulationGlobeBot/0.1 ({API_CONTACT}) python-httpx"

# Default timeout for one upstream request.
HTTP_TIMEOUT_SECONDS = _int("HTTP_TIMEOUT_SECONDS", 30)
# Timeout for large downloads (GeoNames, Zillow, orbit data, Census).
DOWNLOAD_TIMEOUT_SECONDS = _int("DOWNLOAD_TIMEOUT_SECONDS", 60)

# ───────────── Optional API keys (features switch on when set) ─────────────

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")        # raises GitHub's search limit
CENSUS_API_KEY = os.getenv("CENSUS_API_KEY")    # US 1-bed rent, income, age
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")  # geotagged video counts
FLICKR_API_KEY = os.getenv("FLICKR_API_KEY")    # geotagged photo counts

# ───────────── Upstream endpoints ─────────────

URLS = {
    "geonames": "https://download.geonames.org/export/dump/cities15000.zip",
    "zillow": "https://files.zillowstatic.com/research/public_csvs/zori/City_zori_uc_sfrcondomfr_sm_month.csv",
    "worldbank": "https://api.worldbank.org/v2/country/all/indicator/{code}",
    "earthquakes": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/{feed}.geojson",
    "events": "https://eonet.gsfc.nasa.gov/api/v3/events",
    "aurora": "https://services.swpc.noaa.gov/json/ovation_aurora_latest.json",
    "aircraft": "https://opensky-network.org/api/states/all",
    "satellites": "https://celestrak.org/NORAD/elements/gp.php",
    "cables": "https://www.submarinecablemap.com/api/v3/cable/cable-geo.json",
    "wikidata": "https://www.wikidata.org/w/api.php",
    "pageviews": "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user",
    "commons": "https://commons.wikimedia.org/w/api.php",
    "github": "https://api.github.com/search/users",
    "weather": "https://api.open-meteo.com/v1/forecast",
    "air_quality": "https://air-quality-api.open-meteo.com/v1/air-quality",
    "youtube": "https://www.googleapis.com/youtube/v3/search",
    "flickr": "https://www.flickr.com/services/rest/",
    "census": "https://api.census.gov/data/{dataset}",
}
# Tried in order until one answers.
OVERPASS_URLS = _list("OVERPASS_URLS", "https://overpass-api.de/api/interpreter,https://overpass.private.coffee/api/interpreter")

# ───────────── City dataset (GeoNames + Zillow + World Bank) ─────────────

# How often the dataset is re-pulled. GeoNames republishes daily and Zillow monthly,
# so anything under an hour re-downloads identical files.
DATASET_TTL_SECONDS = _int("DATASET_TTL_SECONDS", HOUR)
# Browsers may reuse /api/cities for this long; the frontend busts it when data changes.
CITIES_BROWSER_TTL_SECONDS = _int("CITIES_BROWSER_TTL_SECONDS", MINUTE)
# Cities below this population are left out.
MIN_CITY_POPULATION = _int("MIN_CITY_POPULATION", 20_000)
# A Zillow rent series counts as current only if it has a value in the last N months.
ZILLOW_MAX_STALE_MONTHS = _int("ZILLOW_MAX_STALE_MONTHS", 3)
WORLD_BANK_TIMEOUT_SECONDS = _int("WORLD_BANK_TIMEOUT_SECONDS", 45)
# Field name in the API response -> World Bank indicator code.
WORLD_BANK_INDICATORS = {
    "birth": "SP.DYN.CBRT.IN",     # births per 1,000 people per year
    "death": "SP.DYN.CDRT.IN",     # deaths per 1,000 people per year
    "internet": "IT.NET.USER.ZS",  # % of population using the internet
    "gdp": "NY.GDP.PCAP.CD",       # GDP per person, current US$
}

# ───────────── Live global feeds (/api/live/<name>) ─────────────

# How long this server keeps each feed before asking the upstream again.
_FEED_TTL_DEFAULTS = {
    "earthquakes": MINUTE,
    "events": 15 * MINUTE,
    "aurora": 10 * MINUTE,
    "aircraft": 15 * MINUTE,    # anonymous OpenSky allows roughly 100 global snapshots a day
    "satellites": 10 * MINUTE,  # the orbit data itself is held for TLE_GROUP_TTL_SECONDS
    "cables": 7 * DAY,
}
# How long a CDN (Vercel) may serve each feed without calling this server.
_FEED_CDN_TTL_DEFAULTS = {**_FEED_TTL_DEFAULTS, "satellites": 30 * MINUTE}

FEED_TTL = {name: _int(f"FEED_TTL_{name.upper()}", ttl) for name, ttl in _FEED_TTL_DEFAULTS.items()}
FEED_CDN_TTL = {name: _int(f"FEED_CDN_TTL_{name.upper()}", ttl) for name, ttl in _FEED_CDN_TTL_DEFAULTS.items()}
# Browsers re-ask for a live feed after at most this long.
FEED_BROWSER_MAX_TTL_SECONDS = _int("FEED_BROWSER_MAX_TTL_SECONDS", 5 * MINUTE)

# USGS summary feed: {significant|4.5|2.5|1.0|all}_{hour|day|week|month}.
EARTHQUAKE_FEED = _str("EARTHQUAKE_FEED", "2.5_day")
# NASA EONET: which open events to show and how far back to look.
EONET_CATEGORIES = _str("EONET_CATEGORIES", "wildfires,severeStorms,volcanoes")
EONET_DAYS = _int("EONET_DAYS", 30)
# Aurora grid cells below this probability (%) are dropped.
AURORA_MIN_PROBABILITY = _int("AURORA_MIN_PROBABILITY", 8)
# CelesTrak answers 403 to a client that re-downloads a group within about 2 hours.
TLE_GROUP_TTL_SECONDS = _int("TLE_GROUP_TTL_SECONDS", 6 * HOUR)
TLE_GROUPS = {"stations": "stations", "starlink": "starlink"}

# ───────────── Per-city profile (/api/cities/<id>/profile) ─────────────

_PROFILE_TTL_DEFAULTS = {
    "wikidata": 7 * DAY,
    "github": 7 * DAY,
    "commons": 7 * DAY,
    "weather": 15 * MINUTE,
    "youtube": DAY,
    "flickr": DAY,
    "census": 30 * DAY,
}
PROFILE_TTL = {name: _int(f"PROFILE_TTL_{name.upper()}", ttl) for name, ttl in _PROFILE_TTL_DEFAULTS.items()}
PROFILE_CDN_TTL_SECONDS = _int("PROFILE_CDN_TTL_SECONDS", 15 * MINUTE)
# One slow lookup is dropped (returned as null) after this long, so the rest still return.
PROFILE_PART_TIMEOUT_SECONDS = _int("PROFILE_PART_TIMEOUT_SECONDS", 12)

WIKIPEDIA_PAGEVIEW_DAYS = _int("WIKIPEDIA_PAGEVIEW_DAYS", 30)
COMMONS_RADIUS_METERS = _int("COMMONS_RADIUS_METERS", 10_000)  # API maximum
COMMONS_MAX_RESULTS = _int("COMMONS_MAX_RESULTS", 500)          # API maximum
YOUTUBE_RADIUS_KM = _int("YOUTUBE_RADIUS_KM", 20)
YOUTUBE_LOOKBACK_DAYS = _int("YOUTUBE_LOOKBACK_DAYS", 7)
FLICKR_RADIUS_KM = _int("FLICKR_RADIUS_KM", 20)
FLICKR_LOOKBACK_DAYS = _int("FLICKR_LOOKBACK_DAYS", 30)
CENSUS_DATASET = _str("CENSUS_DATASET", "2023/acs/acs5")
CENSUS_LABEL = _str("CENSUS_LABEL", "ACS 2023 5-year")

# ───────────── Amenities (/api/cities/<id>/amenities, OpenStreetMap) ─────────────

AMENITIES_TTL_SECONDS = _int("AMENITIES_TTL_SECONDS", 30 * DAY)
OVERPASS_TIMEOUT_SECONDS = _int("OVERPASS_TIMEOUT_SECONDS", 20)
# Search radius = clamp(BASE + PER_SQRT_PERSON * sqrt(population), MIN, MAX), in metres.
AMENITY_RADIUS_MIN_METERS = _int("AMENITY_RADIUS_MIN_METERS", 4_000)
AMENITY_RADIUS_MAX_METERS = _int("AMENITY_RADIUS_MAX_METERS", 15_000)
AMENITY_RADIUS_BASE_METERS = _int("AMENITY_RADIUS_BASE_METERS", 3_000)
AMENITY_RADIUS_PER_SQRT_PERSON = _int("AMENITY_RADIUS_PER_SQRT_PERSON", 4)
# Label -> OpenStreetMap tag filter.
AMENITY_FILTERS = {
    "bars": '["amenity"~"^(bar|pub)$"]',
    "cafes": '["amenity"="cafe"]',
    "nightclubs": '["amenity"="nightclub"]',
    "gyms": '["leisure"="fitness_centre"]',
    "coworking": '["amenity"="coworking_space"]',
}

# ───────────── Status endpoint (/api/status) ─────────────

STATUS_BROWSER_TTL_SECONDS = _int("STATUS_BROWSER_TTL_SECONDS", 10)
STATUS_CDN_TTL_SECONDS = _int("STATUS_CDN_TTL_SECONDS", 15)
