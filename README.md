# Swath

Planning and record-keeping for agricultural spray-drone operators.

- **Spray windows** for every field, hour by hour for 3 days: GO / CAUTION / NO-GO with the reasons (wind, gusts, temperature,
  humidity, Delta T, temperature inversion, rain and rain-fast period, daylight, wind blowing toward a sensitive area).
- **Day planner**: which open jobs to fly, in what order and when, so all spraying lands in usable weather. Driving time between
  fields, crew hours, acres per hour, setup time and job priority are taken into account.
- **Fields**: draw on a satellite map or import KML/KMZ, GeoJSON or zipped shapefiles. Acreage is computed for you.
- **Sensitive areas** (apiaries, organic crops, homes…) with buffers: a field goes NO-GO while the wind blows toward one.
- **Records** with every FAA Part 137 §137.71 item, weather captured automatically, CSV / PDF export, full JSON backup.
- Installable web app: add it to the Home Screen on Android or iPhone. No App Store needed.

Forecast data: [Open-Meteo](https://open-meteo.com). Swath is decision support. The product label and on-site conditions always govern.

## Run it
```
pip install -r requirements.txt
python seed_demo.py      # optional: demo operation near Ames, Iowa
python swath.py          # http://localhost:5000  (open from your phone via the machine's LAN IP)
```
Offline / no forecast access: `SWATH_MOCK_WEATHER=1 python swath.py` uses a synthetic forecast.

## Tests
```
pip install -r requirements-dev.txt && playwright install chromium
python -m pytest -q      # unit + API tests
python tests/e2e.py      # browser tests (SHOTS=<dir> saves screenshots)
```

## Deploy (Render)
`render.yaml` is included. Set:
- `DATABASE_URL`: Postgres (e.g. Supabase **pooler** connection string). Without it the app uses a local SQLite file, which a Render deploy wipes.
- `SECRET_KEY`: any long random string (keeps sign-ins across restarts).
- `SWATH_PASSWORD`: optional shared password for the whole app.
- `OPENMETEO_API_KEY`: optional paid Open-Meteo key (commercial use needs one).

See `SPEC.md` for the product spec and planner formulation.
