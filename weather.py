"""Hourly forecast per location from Open-Meteo (batched, cached), or a deterministic synthetic forecast when
SWATH_MOCK_WEATHER=1 (tests, offline dev). Units: mph, °F, %, inches. Times are unix seconds (UTC)."""
import math, os, threading, time
import requests

OM_URL = "https://api.open-meteo.com/v1/forecast"
OM_VARS = ["wind_speed_10m", "wind_gusts_10m", "wind_direction_10m", "temperature_2m", "relative_humidity_2m",
           "precipitation_probability", "precipitation", "temperature_80m", "is_day"]   # ≤10 variables = one billed call
KEYS = ["wind", "gust", "dir", "temp", "rh", "pop", "precip", "t80", "is_day"]
FORECAST_DAYS = 3
CACHE_TTL_S = 30 * 60
STALE_OK_S = 6 * 3600          # serve an old copy this long if Open-Meteo is down / rate limited
GRID_DEG = 0.02                # cache key rounding (~2 km), finer than the forecast models
BATCH = 50

_cache = {}                    # key -> forecast dict
_lock = threading.Lock()


def mock_enabled():
    return os.environ.get("SWATH_MOCK_WEATHER") == "1"


def grid_key(lat, lon):
    return (round(round(lat / GRID_DEG) * GRID_DEG, 4), round(round(lon / GRID_DEG) * GRID_DEG, 4))


def _parse(obj):
    h = obj["hourly"]
    out = {"tz": obj.get("timezone") or "UTC", "times": [int(t) for t in h["time"]], "source": "open-meteo", "fetched": time.time()}
    for k, v in zip(KEYS, OM_VARS):
        out[k] = [(x if x is not None else None) for x in h.get(v, [None] * len(h["time"]))]
    return out


def _fetch_batch(keys):
    params = {
        "latitude": ",".join(str(k[0]) for k in keys), "longitude": ",".join(str(k[1]) for k in keys),
        "hourly": ",".join(OM_VARS), "wind_speed_unit": "mph", "temperature_unit": "fahrenheit", "precipitation_unit": "inch",
        "timezone": "auto", "timeformat": "unixtime", "forecast_days": FORECAST_DAYS,
    }
    key = os.environ.get("OPENMETEO_API_KEY")
    url = OM_URL
    if key:
        url, params["apikey"] = "https://customer-api.open-meteo.com/v1/forecast", key
    last = None
    for attempt in range(3):          # a stalled connection is usually fine on the next try
        try:
            r = requests.get(url, params=params, timeout=(5, 8))
            if r.status_code == 429:
                raise RuntimeError("Open-Meteo rate limit (429)")
            r.raise_for_status()
            break
        except (requests.Timeout, requests.ConnectionError) as e:
            last = e
            time.sleep(0.5 * (attempt + 1))
    else:
        raise last
    data = r.json()
    if isinstance(data, dict):
        data = [data]
    return {k: _parse(d) for k, d in zip(keys, data)}


def get_forecasts(locs):
    """locs: iterable of (lat, lon). Returns {(lat, lon): forecast} (keyed by the caller's coordinates).
    Missing entries mean no data could be had (network down and nothing cached)."""
    locs = list(locs)
    if mock_enabled():
        return {loc: mock_forecast(*loc) for loc in locs}
    now = time.time()
    keys = {loc: grid_key(*loc) for loc in locs}
    need = []
    with _lock:
        for k in set(keys.values()):
            c = _cache.get(k)
            if not c or now - c["fetched"] > CACHE_TTL_S or c["times"][-1] < now + 24 * 3600:
                need.append(k)
    for i in range(0, len(need), BATCH):
        chunk = need[i:i + BATCH]
        try:
            got = _fetch_batch(chunk)
        except Exception as e:  # serve stale below
            print(f"[weather] fetch failed for {len(chunk)} locations: {e}")
            continue
        with _lock:
            _cache.update(got)
    out = {}
    with _lock:
        for loc, k in keys.items():
            c = _cache.get(k)
            if c and now - c["fetched"] <= CACHE_TTL_S + STALE_OK_S:
                out[loc] = c
        for k in [k for k, c in _cache.items() if now - c["fetched"] > CACHE_TTL_S + STALE_OK_S]:
            del _cache[k]
    return out


def clear_cache():
    with _lock:
        _cache.clear()


# ---------- derived quantities ----------
def wet_bulb_c(t_c, rh):
    """Stull (2011) wet-bulb temperature, °C. Valid for RH 5–99 %, T −20…50 °C."""
    rh = max(1.0, min(100.0, rh))
    return (t_c * math.atan(0.151977 * math.sqrt(rh + 8.313659)) + math.atan(t_c + rh) - math.atan(rh - 1.676331)
            + 0.00391838 * rh ** 1.5 * math.atan(0.023101 * rh) - 4.686035)


def delta_t_c(t_f, rh):
    t_c = (t_f - 32) / 1.8
    return t_c - wet_bulb_c(t_c, rh)


def height_factor(height_ft, z0_m=0.1):
    """Log wind profile: ratio of wind at height_ft to wind at 10 m over a crop canopy (roughness z0)."""
    h = max(height_ft / 3.2808, z0_m * 2)
    return math.log(h / z0_m) / math.log(10 / z0_m)


# ---------- mock ----------
def mock_forecast(lat, lon, now=None):
    """Repeatable synthetic forecast: calm inverted nights, a breezy mid-afternoon peak, a rain band on day 2.
    Shape varies a little with location so fields differ."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("America/Chicago")
    now = now or time.time()
    day0 = datetime.fromtimestamp(now, tz).replace(hour=0, minute=0, second=0, microsecond=0)
    seed = (math.sin(lat * 12.9898 + lon * 78.233) * 43758.5453) % 1.0
    out = {"tz": "America/Chicago", "source": "mock", "fetched": now, "times": []}
    for k in KEYS:
        out[k] = []
    for i in range(FORECAST_DAYS * 24):
        t = day0 + timedelta(hours=i)
        hr, day = t.hour, i // 24
        peak = 7.5 + 3 * seed + 1.5 * day                   # afternoon peak wind, mph
        diurnal = max(0.0, math.sin(math.pi * (hr - 7) / 12)) if 7 <= hr <= 19 else 0.0
        wind = round(1.5 + (peak - 1.5) * diurnal ** 0.8, 1)
        night = hr < 7 or hr >= 20
        temp = round(55 + 18 * max(0.0, math.sin(math.pi * (hr - 8) / 14)) + 2 * day, 1)
        rain_band = day == 2 and 14 <= hr <= 17
        out["times"].append(int(t.timestamp()))
        out["wind"].append(wind)
        out["gust"].append(round(wind * 1.35 + 1, 1))
        out["dir"].append(round((200 + 40 * seed + 6 * hr + 25 * day) % 360))
        out["temp"].append(temp)
        out["rh"].append(round(85 - 40 * max(0.0, math.sin(math.pi * (hr - 8) / 14)) + (15 if rain_band else 0)))
        out["pop"].append(80 if rain_band else (35 if day == 2 and hr in (12, 13, 18) else 5))
        out["precip"].append(0.08 if rain_band else 0.0)
        out["t80"].append(round(temp + (3.0 if night else -1.0), 1))
        out["is_day"].append(0 if (hr < 7 or hr >= 19) else 1)
    return out
