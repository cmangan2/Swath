"""Browser smoke tests (Playwright, Chromium). Starts its own server with the synthetic forecast and the demo data.
    pip install -r requirements.txt -r requirements-dev.txt && playwright install chromium
    python tests/e2e.py            # SHOTS=<dir> also saves screenshots
Map tiles come from the internet; without access the map is grey but everything else works."""
import json, math, os, subprocess, sys, tempfile, time, urllib.request
from playwright.sync_api import sync_playwright

PORT = 5098
URL = f"http://localhost:{PORT}/"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SHOTS = os.environ.get("SHOTS")
results = []


def check(name, ok, extra=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  {extra}" if extra else ""))


def shot(pg, name):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        pg.screenshot(path=os.path.join(SHOTS, name + ".png"))


def start_server():
    d = tempfile.mkdtemp(prefix="swath-e2e-")
    env = {**os.environ, "SWATH_MOCK_WEATHER": "1", "SWATH_DB": os.path.join(d, "e2e.db"), "PORT": str(PORT)}
    subprocess.run([sys.executable, os.path.join(ROOT, "seed_demo.py")], env=env, check=True, cwd=ROOT, stdout=subprocess.DEVNULL)
    srv = subprocess.Popen([sys.executable, os.path.join(ROOT, "swath.py")], env=env, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            urllib.request.urlopen(URL + "healthz", timeout=1)
            break
        except Exception:
            time.sleep(0.25)
    return srv


def launch(p):
    exe = os.environ.get("CHROMIUM")
    if not exe:
        for cand in ("/opt/pw-browsers/chromium-1243/chrome-linux64/chrome", "/opt/pw-browsers/chromium/chrome-linux64/chrome"):
            if os.path.exists(cand):
                exe = cand
                break
    return p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()


def new_page(browser, w=1280, h=860, touch=False):
    ctx = browser.new_context(viewport={"width": w, "height": h}, has_touch=touch)
    pg = ctx.new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)[:300]))
    pg.route("**/*", lambda r: r.continue_() if f"localhost:{PORT}" in r.request.url else r.abort())   # no tiles in tests
    pg.goto(URL)
    pg.wait_for_selector("#nowList .card", timeout=10000)
    return ctx, pg, errs


def main():
    srv = start_server()
    try:
        with sync_playwright() as p:
            browser = launch(p)
            ctx, pg, errs = new_page(browser)
            cards = pg.locator("#nowList .card").count()
            check("Now lists every field", cards == 8, f"{cards} cards")
            check("map draws field polygons", pg.locator("path.leaflet-interactive").count() >= 8)
            shot(pg, "1-now")

            # field detail with the 72-hour strip
            pg.locator("#nowList .card").first.click()
            pg.wait_for_selector("#fwStrip .c[data-t]", timeout=5000)
            n = pg.locator("#fwStrip .c[data-t]").count()
            check("field detail shows 72 hourly cells", n == 72, str(n))
            pg.locator("#fwStrip .c.go").first.click()
            check("tapping an hour shows its numbers", "mph" in pg.inner_text("#fwInfo"))
            shot(pg, "2-field")
            pg.click("text=Close")

            # plan tomorrow
            pg.click("#tabs button[data-tab=plan]")
            pg.select_option("#planDate", index=1)
            pg.click("#planBtn")
            pg.wait_for_selector("#planOut .card", timeout=15000)
            stops = pg.locator("#planOut .stop-num").count()
            check("planner schedules jobs", stops >= 2, f"{stops} stops")
            check("timeline drawn", pg.locator(".tl-bar").count() == stops)
            check("route numbered on map", pg.locator(".num-icon").count() == stops)
            shot(pg, "3-plan")

            # import a GeoJSON field
            pg.click("#tabs button[data-tab=fields]")
            lat, lon = 42.09, -93.50
            dlat, dlon = 400 / 110574, 400 / (111320 * math.cos(math.radians(lat)))
            gj = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"name": "Imported 40"},
                  "geometry": {"type": "Polygon", "coordinates": [[[lon, lat], [lon + dlon, lat], [lon + dlon, lat + dlat], [lon, lat + dlat], [lon, lat]]]}}]}
            path = os.path.join(tempfile.mkdtemp(), "f.geojson")
            with open(path, "w") as f:
                json.dump(gj, f)
            pg.set_input_files("#importFile", path)
            pg.wait_for_selector("#mf", timeout=5000)
            pg.click("#mf button[type=submit]")
            pg.wait_for_function("() => [...document.querySelectorAll('#fieldList .title')].some(e => e.textContent === 'Imported 40')", timeout=5000)
            check("GeoJSON import adds the field", True)

            # draw a field on the map
            pg.click("text=+ Draw >> nth=0")
            box = pg.locator("#map").bounding_box()
            cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
            for dx, dy in ((-60, -40), (60, -40), (60, 40), (-60, 40)):
                pg.mouse.click(cx + dx, cy + dy)
                pg.wait_for_timeout(150)
            pg.click("#drawBar button:has-text('Finish')")
            pg.wait_for_selector("#mf [name=name]", timeout=5000)
            pg.fill("#mf [name=name]", "Drawn Field")
            pg.click("#mf button[type=submit]")
            pg.wait_for_function("() => [...document.querySelectorAll('#fieldList .title')].some(e => e.textContent === 'Drawn Field')", timeout=5000)
            check("drawing a polygon creates a field", True)

            # complete a job -> record
            pg.click("#tabs button[data-tab=jobs]")
            open_before = pg.locator("#jobList .card").count()
            pg.locator("#jobList button:has-text('Complete')").first.click()
            pg.wait_for_selector("#mf [name=pilot_id]")
            pg.select_option("#mf [name=pilot_id]", index=1)
            pg.wait_for_timeout(500)
            wind = pg.input_value("#mf [name=wx_wind]")
            check("record form pre-fills weather", wind != "", wind)
            pg.click("#mf button[type=submit]")
            pg.wait_for_timeout(600)
            check("completed job leaves the open list", pg.locator("#jobList .card").count() == open_before - 1)
            pg.click("#tabs button[data-tab=records]")
            pg.wait_for_selector("#recList table", timeout=5000)
            rows = pg.locator("#recList tr").count() - 1
            check("records list shows 3 records", rows == 3, str(rows))
            shot(pg, "4-records")
            with pg.expect_popup() as pop:
                pg.click("text=⬇ CSV")
            check("CSV export opens", pop.value is not None)

            # settings save
            pg.click("#tabs button[data-tab=settings]")
            pg.fill("#limForm [name=wind_max]", "12")
            pg.click("#limForm button.primary")
            pg.wait_for_timeout(500)
            s = json.loads(urllib.request.urlopen(URL + "api/settings").read())
            check("limits save", s["limits"]["wind_max"] == 12)
            shot(pg, "5-settings")
            check("no page errors (desktop)", not errs, "; ".join(errs))
            ctx.close()

            # phone layout
            ctx, pg, errs = new_page(browser, 390, 844, touch=True)
            nav = pg.locator("#tabs").bounding_box()
            check("phone: tab bar sits at the bottom", nav["y"] > 700, str(nav))
            pg.click("#tabs button[data-tab=plan]")
            pg.click("#planBtn")
            pg.wait_for_selector("#planOut .card", timeout=15000)
            wide = pg.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
            check("phone: no horizontal scroll", not wide)
            shot(pg, "6-phone-plan")
            check("no page errors (phone)", not errs, "; ".join(errs))
            ctx.close()
            browser.close()
    finally:
        srv.terminate()
        srv.wait()
    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
