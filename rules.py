"""Spray-window rules: hourly GO / CAUTION / NO-GO for a field from its forecast, the operator's limits and nearby
sensitive areas. Every limit is an operator setting; the product label always governs."""
from geo import ang_diff, compass, M_PER_MILE
from weather import delta_t_c, height_factor

GO, CAUTION, NOGO = "go", "caution", "nogo"
RANK = {GO: 0, CAUTION: 1, NOGO: 2}

DEFAULT_LIMITS = {
    "wind_min": 2.0, "wind_max": 10.0, "wind_caution_margin": 1.5, "gust_max": 15.0,
    "wind_height": "10m", "release_height_ft": 10.0,          # "10m" = forecast as-is, "release" = log-profile estimate
    "temp_max": 90.0, "temp_caution": 85.0,
    "rh_min": 25.0, "rh_caution": 40.0,
    "dt_low": 2.0, "dt_high": 8.0, "dt_nogo": 10.0,           # Delta T, °C
    "inv_nogo": 1.5, "inv_caution": 0.5, "inv_wind": 6.0,      # T80m − T2m °F, applies below inv_wind mph
    "pop_nogo": 60.0, "pop_caution": 30.0,
    "rain_in": 0.02, "rainfast_h": 2,
    "daylight_only": True,
    "sens_nogo_deg": 45.0, "sens_caution_deg": 70.0,
}


def merged_limits(user):
    lim = dict(DEFAULT_LIMITS)
    for k, v in (user or {}).items():
        if k in lim and v is not None and v != "":
            lim[k] = type(DEFAULT_LIMITS[k])(v) if not isinstance(DEFAULT_LIMITS[k], bool) else bool(v)
    return lim


def _worse(a, b):
    return a if RANK[a] >= RANK[b] else b


def evaluate_hour(fc, i, lim, exposures=()):
    """Status for forecast hour i. exposures: [(area_name, bearings, nearest_m), ...] from geo.exposure_sector.
    Returns dict(status, reasons=[(level, text)], and the hour's numbers)."""
    g = lambda k: fc[k][i] if i < len(fc[k]) else None
    wind10, gust10, wdir, temp, rh = g("wind"), g("gust"), g("dir"), g("temp"), g("rh")
    pop, precip, t80, is_day = g("pop"), g("precip"), g("t80"), g("is_day")
    reasons = []
    status = GO

    def flag(level, text):
        nonlocal status
        status = _worse(status, level)
        reasons.append((level, text))

    if wind10 is None:
        return {"status": NOGO, "reasons": [(NOGO, "No forecast for this hour")], "t": fc["times"][i]}
    f = height_factor(lim["release_height_ft"]) if lim["wind_height"] == "release" else 1.0
    wind, gust = wind10 * f, (gust10 or wind10) * f
    where = f" at {lim['release_height_ft']:.0f} ft" if f != 1.0 else ""
    mph = lambda v: f"{v:.1f}" if v < 10 else f"{v:.0f}"

    if wind < lim["wind_min"]:
        flag(NOGO, f"Wind {mph(wind)} mph{where}: below {lim['wind_min']:g} (direction unreliable, inversion risk)")
    elif wind > lim["wind_max"]:
        flag(NOGO, f"Wind {mph(wind)} mph{where}: above {lim['wind_max']:g}")
    elif wind > lim["wind_max"] - lim["wind_caution_margin"]:
        flag(CAUTION, f"Wind {mph(wind)} mph{where}: near the {lim['wind_max']:g} limit")
    if gust > lim["gust_max"]:
        flag(NOGO, f"Gusts {mph(gust)} mph{where}: above {lim['gust_max']:g}")

    if temp is not None:
        if temp > lim["temp_max"]:
            flag(NOGO, f"{temp:.0f}°F: above {lim['temp_max']:g}°F")
        elif temp > lim["temp_caution"]:
            flag(CAUTION, f"{temp:.0f}°F: hot, more evaporation and volatility")
    if rh is not None:
        if rh < lim["rh_min"]:
            flag(NOGO, f"Humidity {rh:.0f}%: below {lim['rh_min']:g}%")
        elif rh < lim["rh_caution"]:
            flag(CAUTION, f"Humidity {rh:.0f}%: low")
    dt = None
    if temp is not None and rh is not None:
        dt = delta_t_c(temp, rh)
        if dt > lim["dt_nogo"]:
            flag(NOGO, f"Delta T {dt:.1f}°C: droplets evaporate fast")
        elif dt > lim["dt_high"]:
            flag(CAUTION, f"Delta T {dt:.1f}°C: above {lim['dt_high']:g}")
        elif dt < lim["dt_low"]:
            flag(CAUTION, f"Delta T {dt:.1f}°C: below {lim['dt_low']:g} (fine droplets linger)")

    inv = None
    if temp is not None and t80 is not None:
        inv = t80 - temp
        if wind10 < lim["inv_wind"]:
            if inv >= lim["inv_nogo"]:
                flag(NOGO, f"Temperature inversion likely (80 m air {inv:+.1f}°F vs ground)")
            elif inv >= lim["inv_caution"]:
                flag(CAUTION, f"Weak inversion possible (80 m air {inv:+.1f}°F)")

    if precip is not None and precip >= lim["rain_in"]:
        flag(NOGO, f"Rain {precip:.2f} in this hour")
    else:
        ahead = [fc["precip"][j] or 0 for j in range(i + 1, min(len(fc["precip"]), i + 1 + int(lim["rainfast_h"])))]
        if ahead and max(ahead) >= lim["rain_in"]:
            k = next(n for n, v in enumerate(ahead, 1) if v >= lim["rain_in"])
            flag(NOGO, f"Rain expected within {k} h (rain-fast {lim['rainfast_h']:g} h)")
    if pop is not None:
        if pop >= lim["pop_nogo"]:
            flag(NOGO, f"Rain chance {pop:.0f}%")
        elif pop >= lim["pop_caution"]:
            flag(CAUTION, f"Rain chance {pop:.0f}%")

    if lim["daylight_only"] and is_day == 0:
        flag(NOGO, "Night")

    if wdir is not None and exposures and wind >= 0.5:
        toward = (wdir + 180) % 360
        for name, bearings, near in exposures:
            if not bearings:
                continue
            off = min(ang_diff(toward, b) for b in bearings)
            dist = f"{near / M_PER_MILE:.2f} mi" if near >= 0.1 * M_PER_MILE else f"{near * 3.2808:.0f} ft"
            if off <= lim["sens_nogo_deg"]:
                flag(NOGO, f"Wind blowing toward {name} ({dist})")
            elif off <= lim["sens_caution_deg"]:
                flag(CAUTION, f"Wind partly toward {name} ({dist})")

    return {"status": status, "reasons": reasons, "t": fc["times"][i], "wind": round(wind, 1), "gust": round(gust, 1),
            "dir": wdir, "dir_txt": compass(wdir) if wdir is not None else None, "temp": temp, "rh": rh,
            "pop": pop, "precip": precip, "dt": round(dt, 1) if dt is not None else None,
            "inv": round(inv, 1) if inv is not None else None}


def evaluate(fc, lim, exposures=()):
    return [evaluate_hour(fc, i, lim, exposures) for i in range(len(fc["times"]))]


def hour_index(fc, ts):
    """Index of the forecast hour whose ±30 min span contains ts, or None."""
    for i, t in enumerate(fc["times"]):
        if t - 1800 <= ts < t + 1800:
            return i
    return None
