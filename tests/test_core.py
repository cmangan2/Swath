"""Unit tests: geometry, import, rules, planner. Run: python -m pytest -q"""
import io, itertools, json, math, os, sys, zipfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
import geo, rules, planner, weather


def square(lat, lon, side_m):
    """Axis-aligned square with its SW corner at (lat, lon)."""
    dlat = side_m / 110574.0
    dlon = side_m / (111320.0 * math.cos(math.radians(lat)))
    ring = [[lon, lat], [lon + dlon, lat], [lon + dlon, lat + dlat], [lon, lat + dlat], [lon, lat]]
    return {"type": "Polygon", "coordinates": [ring]}


# ---------- geometry ----------
def test_area_square_mile():
    g = square(41.6, -93.6, geo.M_PER_MILE)
    assert geo.area_acres(g) == pytest.approx(640, rel=0.005)


def test_area_with_hole():
    outer = square(41.6, -93.6, 1000)["coordinates"][0]
    hole = square(41.6 + 300 / 110574, -93.6 + 300 / (111320 * math.cos(math.radians(41.6))), 200)["coordinates"][0]
    g = {"type": "Polygon", "coordinates": [outer, hole[::-1]]}
    assert geo.area_acres(g) == pytest.approx((1000 ** 2 - 200 ** 2) / geo.M2_PER_ACRE, rel=0.01)


def test_centroid_and_distance():
    g = square(41.6, -93.6, 1000)
    lat, lon = geo.centroid(g)
    assert geo.haversine_m(lat, lon, 41.6 + 500 / 110574, -93.6 + 500 / (111320 * math.cos(math.radians(41.6)))) < 2


def test_drift_sector_points_east():
    field = square(41.6, -93.6, 400)
    # area 300 m east of the field's east edge
    east = square(41.6, -93.6 + 700 / (111320 * math.cos(math.radians(41.6))), 400)
    hits, near = geo.exposure_sector(field, east, 500)
    assert near == pytest.approx(300, abs=3)
    assert 90 in hits and 270 not in hits and 0 not in hits
    assert geo.drift_distance_m(field, east, 90) == pytest.approx(300, abs=3)
    hits_far, _ = geo.exposure_sector(field, east, 200)
    assert hits_far == []


def test_import_geojson_kml_shapefile():
    g = square(41.6, -93.6, 500)
    fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"FIELD_NAME": "North 40"}, "geometry": g},
                                                    {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [0, 0]}}]}
    feats = geo.parse_boundary_file("x.geojson", json.dumps(fc).encode())
    assert [f["name"] for f in feats] == ["North 40"] and feats[0]["acres"] == pytest.approx(61.78, rel=0.01)

    coords = " ".join(f"{x},{y},0" for x, y in g["coordinates"][0])
    kml = f"""<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark><name>Home</name>
      <Polygon><outerBoundaryIs><LinearRing><coordinates>{coords}</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark></Document></kml>"""
    feats = geo.parse_boundary_file("f.kml", kml.encode())
    assert feats[0]["name"] == "Home" and feats[0]["acres"] == pytest.approx(61.78, rel=0.01)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("doc.kml", kml)
    assert geo.parse_boundary_file("f.kmz", buf.getvalue())[0]["name"] == "Home"

    import shapefile
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    w = shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=shapefile.POLYGON)
    w.field("NAME", "C")
    w.poly([g["coordinates"][0][::-1]])   # shapefile outer rings are clockwise
    w.record("Shp Field")
    w.close()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("f.shp", shp.getvalue()); z.writestr("f.shx", shx.getvalue()); z.writestr("f.dbf", dbf.getvalue())
        z.writestr("f.prj", 'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984"]]')
    feats = geo.parse_boundary_file("f.zip", buf.getvalue())
    assert feats[0]["name"] == "Shp Field" and feats[0]["acres"] == pytest.approx(61.78, rel=0.01)


def test_import_rejects_projected():
    with pytest.raises(geo.ImportError_):
        geo.parse_geojson(json.dumps({"type": "Polygon", "coordinates": [[[500000, 4600000], [500100, 4600000], [500100, 4600100], [500000, 4600000]]]}))


# ---------- rules ----------
def fc_one(**kw):
    base = {"times": [0, 3600, 7200, 10800], "wind": [6] * 4, "gust": [9] * 4, "dir": [270] * 4, "temp": [75] * 4, "rh": [60] * 4,
            "pop": [5] * 4, "precip": [0] * 4, "t80": [74] * 4, "is_day": [1] * 4}
    for k, v in kw.items():
        base[k] = v
    return base


def test_rules_go_and_reasons():
    lim = rules.merged_limits({})
    assert rules.evaluate_hour(fc_one(), 0, lim)["status"] == rules.GO
    assert rules.evaluate_hour(fc_one(wind=[12] * 4), 0, lim)["status"] == rules.NOGO
    assert rules.evaluate_hour(fc_one(wind=[9.5] * 4), 0, lim)["status"] == rules.CAUTION
    assert rules.evaluate_hour(fc_one(wind=[1] * 4), 0, lim)["status"] == rules.NOGO
    inv = rules.evaluate_hour(fc_one(wind=[4] * 4, t80=[78] * 4), 0, lim)
    assert inv["status"] == rules.NOGO and any("inversion" in r[1] for r in inv["reasons"])
    rain = rules.evaluate_hour(fc_one(precip=[0, 0.1, 0, 0]), 0, lim)
    assert rain["status"] == rules.NOGO and "within 1 h" in rain["reasons"][0][1]
    assert rules.evaluate_hour(fc_one(is_day=[0] * 4), 0, lim)["status"] == rules.NOGO
    assert rules.evaluate_hour(fc_one(is_day=[0] * 4), 0, rules.merged_limits({"daylight_only": False}))["status"] == rules.GO


def test_rules_release_height_lowers_wind():
    lim = rules.merged_limits({"wind_height": "release", "release_height_ft": 10})
    r = rules.evaluate_hour(fc_one(wind=[12] * 4, gust=[14] * 4), 0, lim)
    assert r["wind"] < 10 and r["status"] != rules.NOGO


def test_rules_sensitive_area():
    lim = rules.merged_limits({})
    exp = [("Smith apiary", [85, 90, 95], 300)]
    # wind FROM 270 blows toward 90 -> no-go
    r = rules.evaluate_hour(fc_one(dir=[270] * 4), 0, lim, exp)
    assert r["status"] == rules.NOGO and "Smith apiary" in r["reasons"][0][1]
    assert rules.evaluate_hour(fc_one(dir=[90] * 4), 0, lim, exp)["status"] == rules.GO


def test_delta_t():
    assert weather.delta_t_c(68, 100) == pytest.approx(0, abs=0.5)
    assert 6 < weather.delta_t_c(86, 35) < 12


def test_mock_forecast_shape():
    fc = weather.mock_forecast(41.6, -93.6)
    assert len(fc["times"]) == 72 and all(len(fc[k]) == 72 for k in weather.KEYS)


# ---------- planner ----------
def _usable(hours):
    u = [False] * planner.SLOTS_PER_DAY
    for a, b in hours:
        for s in range(a * 12, b * 12):
            u[s] = True
    return u


def _brute(p):
    best = p.cost([])
    for r in range(1, len(p.ids) + 1):
        for perm in itertools.permutations(p.ids, r):
            c = p.cost(list(perm))
            if c is not None and c < best:
                best = c
    return best


def test_planner_matches_brute_force():
    import random
    rng = random.Random(7)
    for trial in range(12):
        jobs = []
        usable = {}
        for i in range(5):
            jobs.append({"id": i + 1, "name": f"F{i}", "lat": 41.5 + rng.random() * 0.4, "lon": -93.8 + rng.random() * 0.4,
                         "acres": rng.choice([20, 40, 60, 100, 160]), "priority": rng.choice([1, 1, 2, 3])})
            a = rng.randint(6, 13)
            usable[i + 1] = _usable([(a, min(19, a + rng.randint(2, 6)))])
        crew = {"base_lat": 41.7, "base_lon": -93.6, "allow_pause": trial % 2 == 0}
        p = planner.Plan(jobs, usable, crew)
        got = p.cost(p.solve(budget_s=0.3, seed=trial))
        assert got[0] == pytest.approx(_brute(p)[0]), f"trial {trial}: value {got} vs brute {_brute(p)}"


def test_planner_respects_windows_and_pause():
    jobs = [{"id": 1, "name": "A", "lat": 41.6, "lon": -93.6, "acres": 80, "priority": 1}]   # 2 h at 40 ac/h
    usable = {1: _usable([(8, 9), (11, 12)])}
    res = planner.plan_day(jobs, usable, {"allow_pause": True, "setup_min": 0, "max_pause_min": 120})
    st = res["stops"][0]
    assert st["start_txt"] == "08:00" and st["finish_txt"] == "12:00" and st["paused"] == 120
    res = planner.plan_day(jobs, usable, {"allow_pause": True, "setup_min": 0, "max_pause_min": 60})
    assert res["stops"] == [] and "broken up" in res["unscheduled"][0]["reason"]
    res = planner.plan_day(jobs, usable, {"allow_pause": False, "setup_min": 0})
    assert res["stops"] == [] and "unbroken" in res["unscheduled"][0]["reason"]


def test_planner_prefers_priority():
    jobs = [{"id": 1, "name": "Low", "lat": 41.6, "lon": -93.6, "acres": 200, "priority": 1},
            {"id": 2, "name": "Urgent", "lat": 41.6, "lon": -93.6, "acres": 200, "priority": 3}]
    usable = {1: _usable([(8, 13)]), 2: _usable([(8, 13)])}    # room for only one 5 h job
    res = planner.plan_day(jobs, usable, {"setup_min": 0})
    assert [s["name"] for s in res["stops"]] == ["Urgent"]
