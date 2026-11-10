"""MODELED ESTIMATES — not measured data.

No public API exposes OnlyFans users by city (OnlyFans publishes no geographic
breakdown at all), and no free API covers rent worldwide. These fallbacks fill
the gaps from rough country baselines and a city-size adjustment. Measured
values from live sources always win over these (see sources.py).
"""

import math
import unicodedata

# cc: (typical 1-bed monthly rent in USD for a ~200k city, OnlyFans accounts per 1,000 residents)
COUNTRY = {
    "US": (1750, 120), "CA": (1450, 100), "GB": (1500, 110), "IE": (1900, 90), "AU": (1600, 100), "NZ": (1250, 85),
    "DE": (1050, 60), "FR": (950, 60), "ES": (950, 55), "IT": (850, 55), "PT": (1000, 50), "NL": (1500, 65),
    "BE": (1000, 55), "CH": (2000, 60), "AT": (1000, 55), "SE": (1000, 65), "NO": (1300, 65), "DK": (1400, 65),
    "FI": (950, 55), "IS": (1700, 60), "LU": (1900, 55), "PL": (800, 40), "CZ": (850, 40), "SK": (700, 35),
    "HU": (600, 35), "RO": (500, 40), "BG": (500, 30), "GR": (600, 40), "HR": (650, 35), "RS": (500, 30),
    "SI": (800, 35), "EE": (700, 40), "LV": (550, 35), "LT": (700, 35), "UA": (350, 25), "BY": (350, 15),
    "RU": (500, 12), "TR": (500, 20), "IL": (1500, 45), "AE": (1900, 15), "SA": (700, 6), "QA": (1700, 8),
    "KW": (900, 6), "EG": (200, 4), "MA": (350, 8), "DZ": (200, 3), "TN": (250, 5), "NG": (400, 3),
    "ZA": (500, 20), "KE": (350, 4), "GH": (400, 3), "ET": (250, 1), "TZ": (250, 1), "MX": (650, 35),
    "BR": (400, 35), "AR": (400, 35), "CL": (550, 35), "CO": (400, 40), "PE": (450, 25), "VE": (350, 20),
    "EC": (400, 20), "UY": (600, 30), "CR": (700, 25), "PA": (900, 25), "DO": (500, 20), "PR": (1100, 70),
    "CU": (100, 1), "GT": (450, 12), "IN": (250, 5), "PK": (120, 2), "BD": (150, 1), "LK": (250, 3),
    "NP": (150, 2), "CN": (550, 0.5), "HK": (2300, 15), "TW": (550, 15), "JP": (700, 10), "KR": (650, 10),
    "SG": (2700, 25), "MY": (400, 10), "TH": (450, 15), "VN": (400, 6), "PH": (350, 20), "ID": (300, 4),
    "KZ": (450, 8), "UZ": (400, 2), "IR": (250, 2), "IQ": (400, 2), "JO": (400, 5), "LB": (600, 8),
}
DEFAULT = (350, 4)

# Rough 1-bed monthly rent (USD) for well-known non-US cities, overriding the formula.
RENT_OVERRIDE = {
    "london|GB": 2800, "manchester|GB": 1500, "edinburgh|GB": 1500, "dublin|IE": 2300, "paris|FR": 1500,
    "berlin|DE": 1400, "munich|DE": 1700, "hamburg|DE": 1250, "frankfurt am main|DE": 1400,
    "amsterdam|NL": 2200, "zurich|CH": 2500, "geneve|CH": 2400, "geneva|CH": 2400, "madrid|ES": 1400,
    "barcelona|ES": 1500, "lisbon|PT": 1500, "milan|IT": 1600, "rome|IT": 1300, "vienna|AT": 1100,
    "stockholm|SE": 1400, "oslo|NO": 1600, "copenhagen|DK": 1900, "helsinki|FI": 1200, "prague|CZ": 1150,
    "warsaw|PL": 1100, "budapest|HU": 750, "moscow|RU": 1000, "istanbul|TR": 800, "dubai|AE": 2300,
    "abu dhabi|AE": 1900, "tel aviv|IL": 1900, "toronto|CA": 1900, "vancouver|CA": 2100,
    "montreal|CA": 1300, "sydney|AU": 2300, "melbourne|AU": 1600, "auckland|NZ": 1400, "tokyo|JP": 1100,
    "osaka|JP": 700, "seoul|KR": 900, "beijing|CN": 1000, "shanghai|CN": 1100, "shenzhen|CN": 850,
    "hong kong|HK": 2300, "singapore|SG": 2700, "bangkok|TH": 650, "kuala lumpur|MY": 500,
    "jakarta|ID": 500, "manila|PH": 450, "ho chi minh city|VN": 500, "mumbai|IN": 600, "delhi|IN": 300,
    "bengaluru|IN": 350, "mexico city|MX": 900, "sao paulo|BR": 550, "rio de janeiro|BR": 500,
    "buenos aires|AR": 500, "santiago|CL": 600, "bogota|CO": 450, "lima|PE": 550, "cape town|ZA": 700,
    "johannesburg|ZA": 500, "lagos|NG": 800, "nairobi|KE": 500, "cairo|EG": 250,
}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s)
    return "".join(ch for ch in s if unicodedata.category(ch) != "Mn").lower()


def _clamp(v: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, v))


def estimate_rent(name: str, cc: str, pop: int) -> int:
    override = RENT_OVERRIDE.get(f"{norm(name)}|{cc}")
    if override is not None:
        return override
    base = COUNTRY.get(cc, DEFAULT)[0]
    # Bigger cities rent higher: about +22% per 10x population.
    return round(base * _clamp(1 + 0.22 * math.log10(pop / 2e5), 0.7, 1.6) / 10) * 10


def estimate_of_per_1k(cc: str, pop: int) -> float:
    base = COUNTRY.get(cc, DEFAULT)[1]
    # Bigger cities skew younger / more online.
    return round(base * _clamp(0.9 + 0.12 * math.log10(pop / 2e5), 0.75, 1.2), 1)
