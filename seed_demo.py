"""Fill an empty database with a demo operation near Ames, Iowa: 3 customers, 8 fields, a beehive, 2 pilots, 2 drones,
open jobs and a few finished records.   python seed_demo.py   (uses SWATH_DB / DATABASE_URL like the app)"""
import datetime, math
import swath

CLAT = math.cos(math.radians(42.0))


def rect(lat, lon, w_m, h_m, skew=0.0):
    dlat, dlon = h_m / 110574.0, w_m / (111320.0 * CLAT)
    s = skew * dlon
    ring = [[lon, lat], [lon + dlon, lat], [lon + dlon + s, lat + dlat], [lon + s, lat + dlat], [lon, lat]]
    return {"type": "Polygon", "coordinates": [ring]}


def seed():
    c = swath.app.test_client()
    if c.get("/api/fields").get_json():
        print("Database already has fields; not seeding.")
        return
    post = lambda url, body: c.post(url, json=body).get_json()
    cust = [post("/api/customers", {"name": n, "address": a, "phone": p}) for n, a, p in (
        ("Jensen Farms", "2210 Lincoln Hwy, Nevada IA 50201", "515-555-0141"),
        ("Hoffmann Grain Co.", "57120 170th St, Ames IA 50010", "515-555-0177"),
        ("Larsen Family Farm", "1490 Dayton Ave, Gilbert IA 50105", "515-555-0102"))]
    specs = [  # name, customer index, crop, lat, lon, width m, height m
        ("Jensen North", 0, "Corn", 42.0710, -93.4560, 800, 800), ("Jensen Creek", 0, "Soybeans", 42.0580, -93.4420, 600, 400),
        ("Jensen 40", 0, "Corn", 42.0805, -93.4300, 400, 400), ("Hoffmann Home", 1, "Corn", 42.0300, -93.5600, 800, 600),
        ("Hoffmann East", 1, "Soybeans", 42.0350, -93.5250, 500, 800), ("Hoffmann Bottoms", 1, "Corn", 42.0120, -93.5450, 700, 500),
        ("Larsen South", 2, "Soybeans", 42.1050, -93.6200, 600, 600), ("Larsen Pasture Edge", 2, "Corn", 42.1180, -93.6000, 400, 700)]
    fields = [post("/api/fields", {"name": n, "customer_id": cust[ci]["id"], "crop": crop, "geom": rect(la, lo, w, h, 0.08)})
              for n, ci, crop, la, lo, w, h in specs]
    post("/api/sensitive", {"name": "Miller apiary", "kind": "apiary", "buffer_ft": 1500, "geom": rect(42.0745, -93.4420, 60, 60)})
    post("/api/sensitive", {"name": "Larsen organic garden", "kind": "organic", "buffer_ft": 500, "geom": rect(42.1120, -93.6150, 150, 120)})
    pilots = [post("/api/pilots", {"name": "Chris Mangano", "cert_no": "4567890", "qual_date": "2026-03-14", "address": "Ames IA"}),
              post("/api/pilots", {"name": "Sam Ortiz", "cert_no": "4098812", "qual_date": "2026-04-02", "address": "Nevada IA"})]
    ac = [post("/api/aircraft", {"name": "T50 #1", "make_model": "DJI Agras T50", "registration": "FA3XK72911", "serial": "1581F6Q8D24"}),
          post("/api/aircraft", {"name": "T50 #2", "make_model": "DJI Agras T50", "registration": "FA3XK72954", "serial": "1581F6Q8D31"})]
    prods = [("Fungicide A", "000-111", 10.5, "fl oz/ac"), ("Insecticide B", "000-222", 3.8, "fl oz/ac"), ("Fungicide C", "000-333", 13.7, "fl oz/ac")]
    jobs = []
    for i, f in enumerate(fields):
        p = prods[i % 3]
        jobs.append(post("/api/jobs", {"field_id": f["id"], "product": p[0], "epa_reg": p[1], "rate": p[2], "rate_unit": p[3],
                                       "carrier_gpa": 2, "priority": 3 if i == 4 else (2 if i in (0, 6) else 1), "target": "Tar spot" if "Fung" in p[0] else "Aphids"}))
    today = datetime.date.today()
    for k, j in enumerate(jobs[5:7]):
        d = (today - datetime.timedelta(days=2 + k)).isoformat()
        c.post(f"/api/jobs/{j['id']}/complete", json={"applied_date": d, "start_time": "09:10", "end_time": "11:20", "pilot_id": pilots[k]["id"],
                                                      "aircraft_id": ac[k]["id"], "wx_wind": 6.5, "wx_gust": 9, "wx_dir": 200, "wx_temp": 68, "wx_rh": 62})
    c.put("/api/settings", json={"operator": {"name": "Mango Ag Aerial LLC", "address": "Ames, IA", "cert_no": "XXXX137X", "exemption_no": "00000"},
                                 "crew": {"base_lat": 42.0347, "base_lon": -93.6200, "acres_per_hour": 40}})
    print(f"Seeded {len(fields)} fields, {len(jobs)} jobs.")


if __name__ == "__main__":
    seed()
