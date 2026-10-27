# Swath — project notes for Claude Code

Spray-drone operations planner by Chris "Mango" Mangano (solo dev, also builds Mango Wind Hub). Flask + Leaflet, installable PWA.
Repo: cmangan2/swath, branch `main`. Product spec + planner math: `SPEC.md`.
Chris likes direct numerical answers, scenario tables and frank recommendations. He tests on Android Chrome; iOS matters for users (PWA, no App Store).

## Files
- `swath.py` — Flask app: CRUD API (`/api/<table>`), `/api/windows`, `/api/now`, `/api/plan`, job completion, records CSV/PDF, backup, optional password.
- `geo.py` — geodesic acres, centroid, exact drift-toward-area sectors (`exposure_sector`), boundary import (GeoJSON, KML/KMZ, zipped shapefile; WGS84 only).
- `weather.py` — Open-Meteo hourly forecast, 9 variables (keep ≤ 10: fractional call counting), batched locations, 30 min cache per 0.02° cell, retries with short timeouts (the cloud sandbox proxy sometimes stalls a request). `SWATH_MOCK_WEATHER=1` = deterministic synthetic forecast.
- `rules.py` — GO/CAUTION/NO-GO per hour. `DEFAULT_LIMITS` are operator settings; the label governs (say so in UI copy).
- `planner.py` — orienteering with time windows on 5-minute slots. Greedy insertion + local search (relocate, swap, 2-opt, insert/replace, ruin-and-recreate), lexicographic cost (weighted acres ↑, driving ↓, finish ↓). `tests/test_core.py` checks it against brute force; keep that test passing when touching the search.
- `db.py` — SQLite (`SWATH_DB`) or Postgres (`DATABASE_URL`); SQL written for both, `?` placeholders. New columns: add to `TABLES`, `init()` ALTERs them in.
- `templates/index.html`, `static/app.js`, `static/app.css` — whole frontend (plain JS, state in `S`, every list re-renders from it). Leaflet + Leaflet.draw are vendored in `static/vendor/` (works offline, no CDN).
- `static/sw.js` — served at `/sw.js` with scope `/`; caches the shell and vendor files; API always goes to the network.
- `seed_demo.py` — demo operation near Ames, IA (refuses to seed a non-empty DB).

## Version
`APP_VERSION` in `swath.py`, `CACHE_NAME` and the `?v=` shell URLs in `static/sw.js` must match. Patch bump per pushed change.

## Domain rules (keep consistent)
- Directions are TRUE degrees. Forecast wind direction is where the wind comes FROM; drift goes toward `dir + 180`.
- Sensitive area check: field slid along the wind-toward bearing reaches the area within its buffer → NO-GO within ±`sens_nogo_deg`, CAUTION within ±`sens_caution_deg`. Sectors are cached per (field geom, area geom, buffer).
- Each forecast hour's status covers ±30 min around the hour. Planner slots map to the nearest hour.
- Pause mode: a job may pause through no-go spells, but at most `max_pause_min` in total (default 60), otherwise the crew moves on (a 7 h "pause" was the first bug).
- Completed jobs are records: they can't be deleted (Part 137: keep 12 months), and a field with records can't be deleted.
- Record weather auto-fills from the forecast for the start hour when the pilot doesn't type it.
- Units are US: mph, °F, acres, ft, inches; Delta T in °C (the convention everywhere it's used).

## Tests
`python -m pytest -q` (unit + API, mock weather, temp SQLite) and `python tests/e2e.py` (Playwright; starts its own server on port 5098 with demo data; blocks external requests, so the map is grey). In the cloud sandbox use the venv Python and the preinstalled Chromium (`e2e.py` finds `/opt/pw-browsers/...`). Kill stray servers by PID, not `pkill -f swath.py` (that matches your own shell).

## Roadmap (not built)
Accounts / multi-tenant + billing; several crews in the planner (VRP); drone log import (DJI/XAG/Hylio) and boundary export to controllers; live observations blended into the current hour; QuickBooks; Capacitor Android wrapper for background GPS.
