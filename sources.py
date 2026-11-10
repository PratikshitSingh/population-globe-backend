"""Live upstream data sources, each fetched on demand and cached with a TTL.

Keyless:
- GeoNames daily dump   -> city list, population, time zones (CC BY 4.0)
- Zillow ZORI CSV       -> measured US city rents + history since 2015
- World Bank            -> birth/death rates, internet use, GDP per person (by country)
- USGS / NASA EONET / NOAA SWPC -> earthquakes, natural events, aurora
- OpenSky / CelesTrak   -> aircraft, satellite orbits
- TeleGeography         -> submarine cables (CC BY-NC-SA)
- Wikidata / Wikimedia  -> population, births, pageviews, geotagged photos
- GitHub / Open-Meteo / Overpass -> developers, weather + air quality, amenities

Every URL, interval and limit used here is defined in config.py.

Key-gated (skipped when the env var is unset):
- CENSUS_API_KEY, YOUTUBE_API_KEY, FLICKR_API_KEY, GITHUB_TOKEN (optional, raises limits)
"""

import asyncio
import csv
import io
import time
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

import httpx

import config
from config import DAY, HOUR, MINUTE  # noqa: F401  (re-exported for main.py)
from estimates import estimate_of_per_1k, estimate_rent, norm

GEONAMES_URL = config.URLS["geonames"]
ZORI_URL = config.URLS["zillow"]
WIKIDATA_URL = config.URLS["wikidata"]


class TTLCache:
    """In-process cache. One loader runs per key at a time; if a refresh fails
    and an older value exists, the stale value is served instead of an error."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[float, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def get(self, key: str, ttl: float, loader: Callable[[], Awaitable[Any]]) -> Any:
        hit = self._data.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        async with self._locks.setdefault(key, asyncio.Lock()):
            hit = self._data.get(key)
            if hit and time.time() - hit[0] < ttl:
                return hit[1]
            try:
                value = await loader()
            except Exception:
                if hit:
                    return hit[1]
                raise
            self._data[key] = (time.time(), value)
            return value

    def age(self, key: str) -> float | None:
        hit = self._data.get(key)
        return time.time() - hit[0] if hit else None


cache = TTLCache()


def _client(timeout: float = config.HTTP_TIMEOUT_SECONDS) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": config.USER_AGENT, "Accept": "application/json, text/csv, */*;q=0.8"},
    )


async def _get_json(url: str, params: dict | None = None, timeout: float = config.HTTP_TIMEOUT_SECONDS, headers: dict | None = None) -> Any:
    async with _client(timeout) as http:
        r = await http.get(url, params=params, headers=headers)
        r.raise_for_status()
        return r.json()


async def _safe(coro: Awaitable[Any], timeout: float = config.PROFILE_PART_TIMEOUT_SECONDS) -> Any:
    """Await an optional sub-lookup; any failure or timeout becomes None."""
    try:
        return await asyncio.wait_for(coro, timeout)
    except Exception:
        return None


# ───────────────────────── Zillow ─────────────────────────

# GeoNames name|state -> Zillow city name. NYC boroughs use Zillow's citywide figure.
_ZILLOW_ALIAS = {
    "new york city|NY": "new york", "brooklyn|NY": "new york", "queens|NY": "new york",
    "manhattan|NY": "new york", "the bronx|NY": "new york", "staten island|NY": "new york",
    "lexington-fayette|KY": "lexington", "washington, dc|DC": "washington",
}


def _zillow_keys(name: str, state: str) -> list[str]:
    n = norm(name).replace(".", "").replace("'", "").strip()
    if n.startswith("saint "):
        n = "st " + n[6:]
    n = _ZILLOW_ALIAS.get(f"{n}|{state}", n)
    variants = [n, n.removesuffix(" city"), n.removesuffix(" town"), n.removesuffix(" township"), n.removesuffix(" village")]
    return [f"{v}|{state}" for v in dict.fromkeys(variants)]


async def fetch_zillow() -> dict[str, Any]:
    """Latest rent per city plus one value per year (same calendar month) since 2015."""
    async with _client(config.DOWNLOAD_TIMEOUT_SECONDS) as http:
        r = await http.get(ZORI_URL)
        r.raise_for_status()
    reader = csv.reader(io.StringIO(r.text))
    cols = next(reader)
    i_name, i_state, first = cols.index("RegionName"), cols.index("State"), cols.index("CountyName") + 1
    month = cols[-1]  # e.g. 2026-09-30
    mm, last_year = month[5:7], int(month[:4])
    years = list(range(int(cols[first][:4]), last_year + 1))
    year_cols = []
    for y in years:
        idx = next((i for i in range(first, len(cols)) if cols[i].startswith(f"{y}-{mm}")), None)
        year_cols.append(idx)

    rents: dict[str, int] = {}
    series: dict[str, list[int | None]] = {}
    for row in reader:
        # Accept a value from the last few months only, so discontinued series don't leak in.
        floor = max(first, len(row) - config.ZILLOW_MAX_STALE_MONTHS)
        j = len(row) - 1
        while j >= floor and row[j] == "":
            j -= 1
        if j < floor:
            continue
        rent = round(float(row[j]))
        hist = [round(float(row[i])) if i is not None and i < len(row) and row[i] else None for i in year_cols]
        hist[-1] = rent
        for key in _zillow_keys(row[i_name], row[i_state]):
            if key not in rents:  # rows are ordered by size rank; biggest wins
                rents[key] = rent
                series[key] = hist
    return {"month": month, "rents": rents, "years": years, "series": series}


# ───────────────────────── GeoNames ─────────────────────────

async def fetch_geonames() -> list[tuple]:
    """[(geonameid, name, cc, admin1, lat, lon, pop, tz)] sorted by population desc."""
    async with _client(config.DOWNLOAD_TIMEOUT_SECONDS) as http:
        r = await http.get(GEONAMES_URL)
        r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        text = z.read("cities15000.txt").decode("utf-8")
    out = []
    for line in text.splitlines():
        f = line.split("\t")
        pop = int(f[14] or 0)
        # PPLX = section of a populated place (e.g. a borough) — skip to limit double counting.
        if pop < config.MIN_CITY_POPULATION or f[7] == "PPLX":
            continue
        out.append((int(f[0]), f[1], f[8], f[10], round(float(f[4]), 3), round(float(f[5]), 3), pop, f[17]))
    out.sort(key=lambda c: -c[6])
    return out


# ───────────────────────── World Bank (country level) ─────────────────────────

_WB = config.WORLD_BANK_INDICATORS


async def _wb_indicator(code: str) -> dict[str, tuple[float, str]]:
    data = await _get_json(
        config.URLS["worldbank"].format(code=code),
        {"format": "json", "mrnev": 1, "per_page": 400}, timeout=config.WORLD_BANK_TIMEOUT_SECONDS,
    )
    return {d["country"]["id"]: (d["value"], d["date"]) for d in data[1] if d.get("value") is not None}


async def fetch_countries() -> dict[str, dict[str, Any]]:
    results = await asyncio.gather(*(_safe(_wb_indicator(c), config.WORLD_BANK_TIMEOUT_SECONDS + 5) for c in _WB.values()))
    out: dict[str, dict[str, Any]] = {}
    for field, table in zip(_WB, results):
        for cc, (value, year) in (table or {}).items():
            out.setdefault(cc, {})[field] = round(value, 2)
            out[cc][field + "Year"] = year
    return out


# ───────────────────────── Combined dataset ─────────────────────────

async def _build_dataset() -> dict[str, Any]:
    geo_task = asyncio.create_task(fetch_geonames())
    wb_task = asyncio.create_task(fetch_countries())
    try:
        zillow = await fetch_zillow()
    except Exception as e:  # rents degrade to estimates; the city list still loads
        zillow = {"month": None, "rents": {}, "years": [], "series": {}, "error": f"{type(e).__name__}: {e}"}
    geo = await geo_task
    countries = await wb_task

    tz_index: dict[str, int] = {}
    rows, index, history, measured = [], {}, {}, 0
    for gid, name, cc, admin1, lat, lon, pop, tz in geo:
        rent = None
        if cc == "US":
            for key in _zillow_keys(name, admin1):
                if key in zillow["rents"]:
                    rent = zillow["rents"][key]
                    history[gid] = zillow["series"][key]
                    break
        real = rent is not None
        measured += real
        index[gid] = {"name": name, "cc": cc, "admin1": admin1, "lat": lat, "lon": lon, "pop": pop}
        rows.append([
            gid, name, cc, lat, lon, pop,
            rent if real else estimate_rent(name, cc, pop),
            1 if real else 0,
            estimate_of_per_1k(cc, pop),
            tz_index.setdefault(tz, len(tz_index)),
        ])
    used = {r[2] for r in rows}
    return {
        "index": index,
        "history": {"years": zillow["years"], "month": zillow["month"], "rows": history},
        "cities": {
            "meta": {
                "fetchedAt": int(time.time()),
                "cities": len(rows),
                "population": {"source": "GeoNames daily dump (cities15000)", "url": GEONAMES_URL},
                "rent": {
                    "source": "Zillow Observed Rent Index (US cities); modeled estimate elsewhere",
                    "url": ZORI_URL,
                    "month": zillow["month"],
                    "measuredCities": measured,
                    "error": zillow.get("error"),
                },
                "onlyfans": {"source": "Modeled estimate — no public API exists for this"},
                "countries": {"source": "World Bank Indicators API"},
            },
            "tz": list(tz_index),
            # cc -> {birth, death (per 1,000 per year), internet (%), gdp (US$ per person)}
            "countries": {cc: v for cc, v in countries.items() if cc in used},
            # [geonameid, name, cc, lat, lon, pop, rentUsd, rentIsMeasured, onlyfansPer1k, tzIndex]
            "rows": rows,
        },
    }


DATASET_TTL = config.DATASET_TTL_SECONDS


async def _dataset() -> dict[str, Any]:
    return await cache.get("dataset", DATASET_TTL, _build_dataset)


async def get_cities() -> dict[str, Any]:
    return (await _dataset())["cities"]


async def get_rent_history() -> dict[str, Any]:
    return (await _dataset())["history"]


async def get_city(gid: int) -> dict[str, Any] | None:
    return (await _dataset())["index"].get(gid)


# ───────────────────────── Live global feeds ─────────────────────────

async def _earthquakes() -> dict[str, Any]:
    d = await _get_json(config.URLS["earthquakes"].format(feed=config.EARTHQUAKE_FEED))
    return {
        "source": "USGS",
        "rows": [  # [lon, lat, depthKm, magnitude, place, epochMs]
            [f["geometry"]["coordinates"][0], f["geometry"]["coordinates"][1], f["geometry"]["coordinates"][2],
             f["properties"]["mag"], f["properties"]["place"], f["properties"]["time"]]
            for f in d["features"] if f["properties"].get("mag") is not None
        ],
    }


async def _events() -> dict[str, Any]:
    d = await _get_json(
        config.URLS["events"],
        {"status": "open", "days": config.EONET_DAYS, "category": config.EONET_CATEGORIES},
        timeout=config.DOWNLOAD_TIMEOUT_SECONDS,
    )
    rows = []
    for e in d["events"]:
        points = [g for g in e["geometry"] if g.get("type") == "Point"]
        if not points:
            continue
        g = points[-1]
        mag = f"{g['magnitudeValue']:g} {g.get('magnitudeUnit') or ''}".strip() if g.get("magnitudeValue") else None
        # [lon, lat, category, title, isoDate, magnitude]
        rows.append([g["coordinates"][0], g["coordinates"][1], e["categories"][0]["id"], e["title"], g["date"], mag])
    return {"source": "NASA EONET", "rows": rows}


async def _aurora() -> dict[str, Any]:
    d = await _get_json(config.URLS["aurora"])
    rows = [[lon - 360 if lon > 180 else lon, lat, v] for lon, lat, v in d["coordinates"] if v >= config.AURORA_MIN_PROBABILITY]
    return {"source": "NOAA SWPC OVATION", "forecastTime": d.get("Forecast Time"), "rows": rows}  # [lon, lat, 0-100]


async def _aircraft() -> dict[str, Any]:
    d = await _get_json(config.URLS["aircraft"], timeout=config.DOWNLOAD_TIMEOUT_SECONDS)
    rows = [  # [lon, lat, headingDeg, speedMs, callsign, altitudeM]
        [s[5], s[6], round(s[10] or 0), round(s[9] or 0), (s[1] or "").strip(), round(s[13] or s[7] or 0)]
        for s in d.get("states") or []
        if s[5] is not None and s[6] is not None and not s[8]
    ]
    return {"source": "OpenSky Network", "time": d["time"], "rows": rows}


def _tle_triples(text: str) -> list[list[str]]:
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    return [[lines[i].strip(), lines[i + 1], lines[i + 2]] for i in range(0, len(lines) - 2, 3)]


async def _tle_group(group: str) -> list[list[str]]:
    async with _client(config.DOWNLOAD_TIMEOUT_SECONDS) as http:
        r = await http.get(config.URLS["satellites"], params={"GROUP": group, "FORMAT": "tle"})
        r.raise_for_status()
    return _tle_triples(r.text)


async def _satellites() -> dict[str, Any]:
    # CelesTrak answers 403 to a client that re-downloads a group within ~2 hours, so each
    # group is cached on its own (config.TLE_GROUP_TTL_SECONDS) and a failed group is simply left out for now.
    stations, starlink = await asyncio.gather(
        _safe(cache.get("tle:stations", config.TLE_GROUP_TTL_SECONDS, lambda: _tle_group(config.TLE_GROUPS["stations"])), config.DOWNLOAD_TIMEOUT_SECONDS),
        _safe(cache.get("tle:starlink", config.TLE_GROUP_TTL_SECONDS, lambda: _tle_group(config.TLE_GROUPS["starlink"])), config.DOWNLOAD_TIMEOUT_SECONDS),
    )
    if stations is None and starlink is None:
        raise RuntimeError("CelesTrak unavailable")
    iss = next((t for t in stations or [] if t[0].startswith("ISS (ZARYA)")), None)
    return {"source": "CelesTrak", "iss": iss, "starlink": starlink or []}  # [name, tleLine1, tleLine2]


async def _cables() -> dict[str, Any]:
    d = await _get_json(config.URLS["cables"], timeout=config.DOWNLOAD_TIMEOUT_SECONDS)
    for f in d["features"]:
        f["properties"] = {"name": f["properties"].get("name"), "color": f["properties"].get("color")}
    d["source"] = "TeleGeography Submarine Cable Map (CC BY-NC-SA 4.0)"
    return d


_LOADERS: dict[str, Callable[[], Awaitable[Any]]] = {
    "earthquakes": _earthquakes, "events": _events, "aurora": _aurora,
    "aircraft": _aircraft, "satellites": _satellites, "cables": _cables,
}
# name -> (loader, in-process TTL seconds, CDN s-maxage seconds); lifetimes come from config.py
FEEDS: dict[str, tuple[Callable[[], Awaitable[Any]], int, int]] = {
    name: (loader, config.FEED_TTL[name], config.FEED_CDN_TTL[name]) for name, loader in _LOADERS.items()
}


async def get_feed(name: str) -> Any:
    loader, ttl, _ = FEEDS[name]
    return await cache.get(f"feed:{name}", ttl, loader)


_FEED_INFO = {
    "earthquakes": ("Earthquakes", "USGS"),
    "events": ("Wildfires, storms, volcanoes", "NASA EONET"),
    "aurora": ("Aurora forecast", "NOAA SWPC"),
    "aircraft": ("Aircraft", "OpenSky Network"),
    "satellites": ("ISS + Starlink orbits", "CelesTrak"),
    "cables": ("Submarine cables", "TeleGeography"),
}


def status() -> list[dict[str, Any]]:
    """When each upstream was last pulled by this server instance, and how often it is re-pulled."""
    def row(key: str, label: str, upstream: str, ttl: int) -> dict[str, Any]:
        age = cache.age(key)
        return {
            "id": key, "label": label, "upstream": upstream, "refreshSeconds": ttl,
            "ageSeconds": None if age is None else round(age),
            "fetchedAt": None if age is None else round(time.time() - age),
        }
    return [row("dataset", "Cities, population, rent", "GeoNames · Zillow · World Bank", DATASET_TTL)] + [
        row(f"feed:{name}", *_FEED_INFO[name], FEEDS[name][1]) for name in FEEDS
    ]


# ───────────────────────── Per-city profile ─────────────────────────

async def _wikidata(gid: int) -> dict[str, Any]:
    """Wikidata item for a GeoNames ID: latest population, people born there, Wikipedia pageviews."""
    out: dict[str, Any] = {"entity": None, "population": None, "populationAsOf": None,
                           "bornHere": None, "wikipedia": None, "pageviews30d": None}
    async with _client() as http:
        r = await http.get(WIKIDATA_URL, params={
            "action": "query", "list": "search", "srlimit": 1, "format": "json",
            "srsearch": f"haswbstatement:P1566={gid}",
        })
        r.raise_for_status()
        found = r.json().get("query", {}).get("search", [])
        if not found:
            return out
        qid = out["entity"] = found[0]["title"]

        ent_req = http.get(WIKIDATA_URL, params={
            "action": "wbgetentities", "ids": qid, "props": "claims|sitelinks",
            "sitefilter": "enwiki", "format": "json",
        })
        born_req = http.get(WIKIDATA_URL, params={
            "action": "query", "list": "search", "srlimit": 1, "srinfo": "totalhits", "format": "json",
            "srsearch": f"haswbstatement:P19={qid}",
        })
        ent_res, born_res = await asyncio.gather(ent_req, born_req, return_exceptions=True)

        if not isinstance(born_res, Exception) and born_res.status_code == 200:
            out["bornHere"] = born_res.json().get("query", {}).get("searchinfo", {}).get("totalhits")

        if not isinstance(ent_res, Exception) and ent_res.status_code == 200:
            entity = ent_res.json().get("entities", {}).get(qid, {})
            best = None  # (is_preferred, date, population)
            for claim in entity.get("claims", {}).get("P1082", []):
                try:
                    pop = int(float(claim["mainsnak"]["datavalue"]["value"]["amount"]))
                except (KeyError, ValueError):
                    continue
                if claim.get("rank") == "deprecated":
                    continue
                dates = claim.get("qualifiers", {}).get("P585", [])
                date = dates[0].get("datavalue", {}).get("value", {}).get("time", "")[1:11] if dates else ""
                cand = (claim.get("rank") == "preferred", date, pop)
                if best is None or cand[:2] > best[:2]:
                    best = cand
            if best:
                out["population"], out["populationAsOf"] = best[2], best[1] or None

            title = entity.get("sitelinks", {}).get("enwiki", {}).get("title")
            if title:
                out["wikipedia"] = title
                end = datetime.now(timezone.utc) - timedelta(days=1)
                start = end - timedelta(days=config.WIKIPEDIA_PAGEVIEW_DAYS - 1)
                pv = await http.get(
                    config.URLS["pageviews"] + "/"
                    f"{httpx.URL('/' + title.replace(' ', '_')).raw_path.decode()[1:].replace('/', '%2F')}"
                    f"/daily/{start:%Y%m%d}/{end:%Y%m%d}"
                )
                if pv.status_code == 200:
                    out["pageviews30d"] = sum(i["views"] for i in pv.json().get("items", []))
    return out


async def _github_developers(name: str) -> dict[str, Any]:
    """GitHub user accounts whose profile location mentions the city name."""
    headers = {"Accept": "application/vnd.github+json"}
    if config.GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {config.GITHUB_TOKEN}"
    d = await _get_json(config.URLS["github"],
                        {"q": f'location:"{name}" type:user', "per_page": 1}, headers=headers)
    return {"count": d["total_count"]}


async def _commons_photos(lat: float, lon: float) -> dict[str, Any]:
    """Geotagged Wikimedia Commons files near the centre (the API caps both radius and count)."""
    d = await _get_json(config.URLS["commons"], {
        "action": "query", "list": "geosearch", "gscoord": f"{lat}|{lon}", "gsradius": config.COMMONS_RADIUS_METERS,
        "gslimit": config.COMMONS_MAX_RESULTS, "gsnamespace": 6, "format": "json",
    })
    n = len(d.get("query", {}).get("geosearch", []))
    return {"count": n, "capped": n >= config.COMMONS_MAX_RESULTS,
            "radiusKm": round(config.COMMONS_RADIUS_METERS / 1000, 1)}


async def _weather(lat: float, lon: float) -> dict[str, Any]:
    loc = {"latitude": lat, "longitude": lon}
    wx, aq = await asyncio.gather(
        _safe(_get_json(config.URLS["weather"],
                        {**loc, "current": "temperature_2m,weather_code,wind_speed_10m,is_day"})),
        _safe(_get_json(config.URLS["air_quality"],
                        {**loc, "current": "us_aqi,pm2_5"})),
    )
    if not wx and not aq:
        raise RuntimeError("Open-Meteo unavailable")
    cur, air = (wx or {}).get("current", {}), (aq or {}).get("current", {})
    return {
        "tempC": cur.get("temperature_2m"), "weatherCode": cur.get("weather_code"),
        "windKmh": cur.get("wind_speed_10m"), "isDay": cur.get("is_day"),
        "usAqi": air.get("us_aqi"), "pm25": air.get("pm2_5"),
    }


async def _youtube(lat: float, lon: float) -> dict[str, Any]:
    after = (datetime.now(timezone.utc) - timedelta(days=config.YOUTUBE_LOOKBACK_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    d = await _get_json(config.URLS["youtube"], {
        "part": "id", "type": "video", "location": f"{lat},{lon}", "locationRadius": f"{config.YOUTUBE_RADIUS_KM}km",
        "publishedAfter": after, "maxResults": 1, "key": config.YOUTUBE_API_KEY,
    })
    return {"videos": d.get("pageInfo", {}).get("totalResults"), "days": config.YOUTUBE_LOOKBACK_DAYS}


async def _flickr(lat: float, lon: float) -> dict[str, Any]:
    d = await _get_json(config.URLS["flickr"], {
        "method": "flickr.photos.search", "api_key": config.FLICKR_API_KEY, "lat": lat, "lon": lon,
        "radius": config.FLICKR_RADIUS_KM, "min_upload_date": int(time.time()) - config.FLICKR_LOOKBACK_DAYS * DAY, "per_page": 1,
        "format": "json", "nojsoncallback": 1,
    })
    return {"photos": int(d["photos"]["total"]), "days": config.FLICKR_LOOKBACK_DAYS}


_STATE_FIPS = {
    "AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06", "CO": "08", "CT": "09", "DE": "10", "DC": "11",
    "FL": "12", "GA": "13", "HI": "15", "ID": "16", "IL": "17", "IN": "18", "IA": "19", "KS": "20", "KY": "21",
    "LA": "22", "ME": "23", "MD": "24", "MA": "25", "MI": "26", "MN": "27", "MS": "28", "MO": "29", "MT": "30",
    "NE": "31", "NV": "32", "NH": "33", "NJ": "34", "NM": "35", "NY": "36", "NC": "37", "ND": "38", "OH": "39",
    "OK": "40", "OR": "41", "PA": "42", "RI": "44", "SC": "45", "SD": "46", "TN": "47", "TX": "48", "UT": "49",
    "VT": "50", "VA": "51", "WA": "53", "WV": "54", "WI": "55", "WY": "56",
}
_PLACE_SUFFIXES = (" city", " town", " village", " borough", " cdp", " municipality", " city and borough",
                   " consolidated government", " metro government", " unified government", " urban county")


async def _census_table() -> dict[str, dict[str, Any]]:
    """ACS 5-year: median 1-bedroom gross rent, household income and age for every US place."""
    data = await _get_json(config.URLS["census"].format(dataset=config.CENSUS_DATASET), {
        "get": "NAME,B25031_003E,B19013_001E,B01002_001E", "for": "place:*", "in": "state:*",
        "key": config.CENSUS_API_KEY,
    }, timeout=config.DOWNLOAD_TIMEOUT_SECONDS)
    out = {}
    for name, rent, income, age, state, _place in data[1:]:
        place = norm(name.split(",")[0])
        for suffix in _PLACE_SUFFIXES:
            place = place.removesuffix(suffix)
        num = lambda v: float(v) if v not in (None, "") and float(v) > 0 else None  # negatives are "no data" codes
        out.setdefault(f"{place.strip()}|{state}", {"rent1br": num(rent), "income": num(income), "medianAge": num(age), "survey": config.CENSUS_LABEL})
    return out


async def _census(name: str, admin1: str) -> dict[str, Any] | None:
    table = await cache.get("census", config.PROFILE_TTL["census"], _census_table)
    key = norm(name).removesuffix(" city")
    return table.get(f"{'new york' if key == 'new york' else key}|{_STATE_FIPS.get(admin1, '')}")


async def get_profile(gid: int) -> dict[str, Any] | None:
    """Everything we can look up live about one city. Each part is cached on its own
    and is null when its upstream fails, is rate-limited, or needs a key that isn't set."""
    city = await get_city(gid)
    if not city:
        return None
    name, lat, lon = city["name"], city["lat"], city["lon"]
    jobs = {
        "wikidata": cache.get(f"wd:{gid}", config.PROFILE_TTL["wikidata"], lambda: _wikidata(gid)),
        "github": cache.get(f"gh:{norm(name)}", config.PROFILE_TTL["github"], lambda: _github_developers(name)),
        "commons": cache.get(f"commons:{gid}", config.PROFILE_TTL["commons"], lambda: _commons_photos(lat, lon)),
        "weather": cache.get(f"wx:{gid}", config.PROFILE_TTL["weather"], lambda: _weather(lat, lon)),
    }
    if config.YOUTUBE_API_KEY:
        jobs["youtube"] = cache.get(f"yt:{gid}", config.PROFILE_TTL["youtube"], lambda: _youtube(lat, lon))
    if config.FLICKR_API_KEY:
        jobs["flickr"] = cache.get(f"flickr:{gid}", config.PROFILE_TTL["flickr"], lambda: _flickr(lat, lon))
    if config.CENSUS_API_KEY and city["cc"] == "US":
        jobs["census"] = _census(name, city["admin1"])
    results = await asyncio.gather(*(_safe(j) for j in jobs.values()))
    return {"id": gid, **dict(zip(jobs, results))}


# ───────────────────────── Amenities (OpenStreetMap) ─────────────────────────

_AMENITIES = config.AMENITY_FILTERS
_OVERPASS = config.OVERPASS_URLS


async def _amenities(lat: float, lon: float, radius: int) -> dict[str, Any]:
    query = f"[out:json][timeout:{config.OVERPASS_TIMEOUT_SECONDS}];" + "".join(
        f"nwr{sel}(around:{radius},{lat},{lon});out count;" for sel in _AMENITIES.values()
    )
    last: Exception | None = None
    for url in _OVERPASS:
        try:
            async with _client(config.OVERPASS_TIMEOUT_SECONDS) as http:
                r = await http.post(url, data={"data": query})
                r.raise_for_status()
            counts = [int(e["tags"]["total"]) for e in r.json()["elements"] if e.get("type") == "count"]
            if len(counts) == len(_AMENITIES):
                return {"radiusKm": round(radius / 1000, 1), **dict(zip(_AMENITIES, counts))}
            last = RuntimeError("unexpected Overpass response")
        except Exception as e:
            last = e
    raise last or RuntimeError("Overpass unavailable")


async def get_amenities(gid: int) -> dict[str, Any] | None:
    city = await get_city(gid)
    if not city:
        return None
    # Search radius grows with city size (see AMENITY_RADIUS_* in config.py).
    grown = config.AMENITY_RADIUS_BASE_METERS + config.AMENITY_RADIUS_PER_SQRT_PERSON * city["pop"] ** 0.5
    radius = int(min(config.AMENITY_RADIUS_MAX_METERS, max(config.AMENITY_RADIUS_MIN_METERS, grown)))
    return await cache.get(f"osm:{gid}", config.AMENITIES_TTL_SECONDS, lambda: _amenities(city["lat"], city["lon"], radius))
