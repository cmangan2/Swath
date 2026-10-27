"""API tests with the synthetic forecast and a throwaway SQLite file."""
import json, math, os, sys, tempfile
os.environ["SWATH_MOCK_WEATHER"] = "1"
os.environ["SWATH_DB"] = os.path.join(tempfile.mkdtemp(prefix="swath-test-"), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
import swath
from test_core import square


@pytest.fixture()
def c():
    swath.app.config["TESTING"] = True
    return swath.app.test_client()


def test_end_to_end_flow(c):
    cust = c.post("/api/customers", json={"name": "Jensen Farms", "address": "1 Rural Rt, Ames IA"}).get_json()
    f1 = c.post("/api/fields", json={"name": "North 80", "customer_id": cust["id"], "crop": "Corn", "geom": square(41.60, -93.60, 570)}).get_json()
    assert f1["acres"] == pytest.approx(80.3, rel=0.02) and f1["lat"] > 41.6
    bulk = c.post("/api/fields/bulk", json={"customer_id": cust["id"], "fields": [
        {"name": "East 40", "geometry": square(41.62, -93.52, 400)}, {"name": "South 60", "geometry": square(41.55, -93.58, 500)}]}).get_json()
    assert len(bulk) == 2
    c.post("/api/sensitive", json={"name": "Hives", "kind": "apiary", "buffer_ft": 1500,
                                   "geom": square(41.60, -93.60 + 700 / (111320 * math.cos(math.radians(41.6))), 100)})
    pilot = c.post("/api/pilots", json={"name": "Chris M", "cert_no": "4321", "qual_date": "2026-03-01", "address": "Ames"}).get_json()
    ac = c.post("/api/aircraft", json={"name": "T50 #1", "registration": "N123SW", "serial": "ABC"}).get_json()
    jobs = [c.post("/api/jobs", json={"field_id": f["id"], "product": "Fungicide X", "epa_reg": "100-123", "rate": 1.5, "rate_unit": "pt/ac",
                                      "priority": p}).get_json() for f, p in ((f1, 1), (bulk[0], 3), (bulk[1], 1))]

    w = c.get("/api/windows").get_json()
    assert set(w) == {str(f1["id"]), str(bulk[0]["id"]), str(bulk[1]["id"])}
    hrs = w[str(f1["id"])]["hours"]
    assert len(hrs) == 72
    assert any("Hives" in r[1] for h in hrs for r in h["reasons"]), "sensitive area never triggers"
    assert {h["status"] for h in w[str(bulk[1]["id"])]["hours"]} >= {"go", "nogo"}
    assert c.get("/api/now").get_json()["fields"]

    import datetime
    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    plan = c.post("/api/plan", json={"date": tomorrow, "budget_s": 0.2}).get_json()
    assert plan["stops"], plan
    assert plan["stops"][0]["field_id"] in (f1["id"], bulk[0]["id"], bulk[1]["id"])
    for s in plan["stops"]:
        assert s["start"] >= s["ready"] and s["finish"] > s["start"]

    r = c.post(f"/api/jobs/{jobs[0]['id']}/complete", json={"applied_date": tomorrow, "start_time": "10:00", "end_time": "12:00", "pilot_id": pilot["id"], "aircraft_id": ac["id"]})
    done = r.get_json()
    assert done["status"] == "done" and done["wx_wind"] is not None and done["total_amount"] == pytest.approx(1.5 * f1["acres"], rel=0.01)
    assert c.delete(f"/api/jobs/{jobs[0]['id']}").status_code == 409
    assert c.delete(f"/api/fields/{f1['id']}").status_code == 409

    csv_text = c.get("/api/records.csv").get_data(as_text=True)
    assert "Jensen Farms" in csv_text and "N123SW" in csv_text and "4321" in csv_text
    pdf = c.get("/api/records.pdf")
    assert pdf.status_code == 200 and pdf.data[:4] == b"%PDF"
    assert c.get("/api/backup").get_json()["jobs"]


def test_import_endpoint(c):
    import io
    g = square(41.6, -93.6, 300)
    data = {"file": (io.BytesIO(json.dumps(g).encode()), "x.geojson")}
    r = c.post("/api/import", data=data, content_type="multipart/form-data").get_json()
    assert r["features"][0]["acres"] > 20
    bad = c.post("/api/import", data={"file": (io.BytesIO(b"hello"), "x.txt")}, content_type="multipart/form-data")
    assert bad.status_code == 400


def test_settings_roundtrip(c):
    s = c.put("/api/settings", json={"limits": {"wind_max": 12}, "crew": {"acres_per_hour": 55, "start": "07:30"},
                                     "operator": {"name": "Sky Ag LLC"}}).get_json()
    assert s["limits"]["wind_max"] == 12 and s["crew"]["acres_per_hour"] == 55 and s["operator"]["name"] == "Sky Ag LLC"
    assert c.put("/api/settings", json={"crew": {"start": "nope"}}).status_code == 400
