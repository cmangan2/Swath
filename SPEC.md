# Swath — product spec (v0.1)

Swath is planning and record-keeping software for agricultural spray-drone operators (FAA Part 137 / 44807 exemption holders).
Record keepers already exist (AcreConnect, DroneTrax, DroneBundle). Swath's edge is the **planner**: given a list of customer
fields, the hourly forecast at each one and the distances between them, it decides which fields to fly today, in what order and when,
so every acre is sprayed inside a legal, low-drift weather window. Records come along because operators need them anyway.

## Users
- **Owner / operator** (1–5 drones, 1–3 crews): plans the day, keeps the records the FAA and the state can ask for.
- **Pilot in the field**: one-glance go / no-go for the field they're standing in, logs the job when done (phone, often gloves, bright sun).

## v0.1 scope
1. **Fields** — draw on a satellite map, or import KML/KMZ, GeoJSON or a zipped shapefile (what Pix4Dfields, DJI SmartFarm, FieldView and
   John Deere Ops Center export). Acreage computed geodesically. Each field belongs to a customer (name + address: Part 137 record item).
2. **Sensitive areas** — polygons for apiaries, organic crops, homes, schools, water, susceptible crops, each with a buffer distance.
   A field is no-go while the wind blows from it toward a sensitive area within the buffer.
3. **Spray windows** — every field gets an hourly status for 3 days: **GO / CAUTION / NO-GO**, with the reasons:
   - wind speed min/max and gust max (optionally adjusted from the 10 m forecast to release height with a log wind profile),
   - temperature max, relative humidity min, Delta T (wet-bulb depression) caution/no-go band,
   - temperature inversion (80 m air warmer than 2 m air with light wind),
   - rain: hour probability, rain in the hour, rain inside the rain-fast period after application,
   - daylight only (optional),
   - wind toward a sensitive area.
   All limits are operator settings; **the product label always governs** (UI says so).
4. **Day planner** — pick a date and the open jobs; crew settings (base, start/end, acres per hour, setup minutes per field, road speed,
   detour factor, whether a job may pause for a no-go hour). Output: ordered route with arrive / start / finish times, travel and waiting,
   jobs that don't fit and why, drawn on the map. Objective: maximize priority-weighted acres, then minimize driving, then finish early.
5. **Jobs / records** — a job is a work order (field, product, EPA reg. no., rate, carrier volume, priority) that becomes an application
   record when completed (date, start/end, acres, total product, pilot + certificate no. + qualification date, aircraft N-number/serial,
   weather at application captured automatically from the forecast and editable).
   Export CSV and PDF for any date range. Part 137 §137.71 asks for: customer name and address, service date, material name and quantity,
   pilot name, address, certificate number and qualification date — all present.
6. **Go / no-go now** — list of fields with the current hour's status, sorted GO first.
7. Installable web app (PWA), works on Android and iOS without an App Store account. Optional single shared password.

## Out of scope for v0.1 (roadmap)
- Multi-tenant accounts and billing; multiple crews in the planner (v0.2: vehicle-routing with several crews).
- Importing flight logs straight from DJI/XAG/Hylio; exporting planned boundaries back to the drone controller (GeoJSON/shapefile export is easy next).
- Invoicing / QuickBooks.
- Live observations (METAR / Mesonet / a handheld anemometer reading) blended into the forecast for the current hour.
- Native wrappers (Capacitor) for background GPS on Android.

## Planner formulation
Time is discretized into 5-minute slots over the planning day. For job *j* at field *f*: duration
`d_j = acres_j / rate × 60 + setup_min` (rounded up to slots). `go_f[t]` is true when the slot is GO (or CAUTION, if the operator allows it).
- **Continuous mode**: a job must run in one unbroken run of usable slots. `next_start_j[t]` = first `s ≥ t` with `go_f[s..s+d_j-1]` all true.
- **Pause mode**: the crew stays on the field and sprays only in usable slots, with at most `max_pause_min` of idle time inside the job; finish = end of the first run of `d_j` usable slots whose gaps fit that cap.
Both are precomputed per job in O(T), so simulating a route is O(n).
Travel: great-circle distance × detour factor ÷ road speed.
Search (orienteering problem with time windows): greedy insertion by value per added minute, then local search
(relocate, swap, 2-opt, insert unscheduled, ruin-and-recreate) under a fixed time budget and seed, compared lexicographically on
(weighted acres ↑, travel minutes ↓, finish time ↓). Tests check it against brute force on small instances.

## Defaults (US units)
| Setting | Default |
|---|---|
| Wind | 2–10 mph, gusts ≤ 15 mph, measured at 10 m (forecast standard) |
| Temperature | ≤ 90 °F (caution ≥ 85) |
| Relative humidity | ≥ 25 % (caution < 40) |
| Delta T | caution outside 2–8 °C, no-go > 10 °C |
| Inversion | no-go when T80m − T2m ≥ 1.5 °F and wind < 6 mph; caution ≥ 0.5 °F |
| Rain | no-go if ≥ 0.02 in in the hour or within 2 h after; probability no-go ≥ 60 %, caution ≥ 30 % |
| Sensitive areas | no-go when wind blows toward one within its buffer, ±45°; caution ±70° |
| Crew | 6:00–19:00, 40 ac/h, 20 min setup, 45 mph roads, detour ×1.3, pauses inside a job ≤ 60 min |

## Data source
Open-Meteo hourly forecast (`best_match` model), 9 variables, `timezone=auto`, unix time, 3 days. Locations are batched into one
request and rounded to 0.02° for caching (30 min TTL). `SWATH_MOCK_WEATHER=1` swaps in a deterministic synthetic forecast for tests.
