"""Swath — spray-drone operations planner. Flask backend: fields, sensitive areas, spray windows, day planner, records.
Run: python swath.py   (http://localhost:5000)   See SPEC.md and CLAUDE.md."""
import csv, hashlib, io, json, os, secrets, threading, time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from flask import Flask, Response, abort, jsonify, redirect, render_template, request, send_from_directory, session

import db, geo, planner, rules, weather

APP_VERSION = "0.1.0"
HERE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config["MAX_CONTENT_LENGTH"] = 15 * 1024 * 1024
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
PASSWORD = os.environ.get("SWATH_PASSWORD")
CRUD_TABLES = {"customers", "fields", "sensitive", "pilots", "aircraft", "jobs"}
DEFAULT_OPERATOR = {"name": "", "address": "", "phone": "", "cert_no": "", "exemption_no": ""}

db.init()


# ---------- auth (optional single shared password) ----------
@app.before_request
def _auth():
    if not PASSWORD:
        return None
    p = request.path
    if p in ("/login", "/sw.js", "/healthz") or p.startswith("/static/") or session.get("ok"):
        return None
    if p.startswith("/api/"):
        return jsonify(error="Sign in first"), 401
    return redirect("/login")


@app.route("/login", methods=["GET", "POST"])
def login():
    err = ""
    if request.method == "POST":
        if secrets.compare_digest(request.form.get("password", ""), PASSWORD or ""):
            session["ok"] = True
            session.permanent = True
            return redirect("/")
        err = "Wrong password"
        time.sleep(1)
    return render_template("login.html", err=err, version=APP_VERSION)


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login" if PASSWORD else "/")


# ---------- pages ----------
@app.route("/")
def index():
    r = Response(render_template("index.html", version=APP_VERSION))
    r.headers["Cache-Control"] = "no-store"
    return r


@app.route("/sw.js")
def service_worker():
    r = send_from_directory(os.path.join(HERE, "static"), "sw.js", mimetype="application/javascript")
    r.headers["Service-Worker-Allowed"] = "/"
    r.headers["Cache-Control"] = "no-cache"
    return r


@app.route("/healthz")
def healthz():
    return {"ok": True, "version": APP_VERSION}


# ---------- settings ----------
def settings():
    return {
        "limits": rules.merged_limits(db.get_setting("limits", {})),
        "crew": {**planner.DEFAULT_CREW, **(db.get_setting("crew", {}) or {})},
        "operator": {**DEFAULT_OPERATOR, **(db.get_setting("operator", {}) or {})},
        "tz": db.get_setting("tz") or "America/Chicago",
    }


@app.route("/api/settings", methods=["GET", "PUT"])
def api_settings():
    if request.method == "PUT":
        body = request.get_json(force=True) or {}
        if "limits" in body:
            lim = {k: v for k, v in body["limits"].items() if k in rules.DEFAULT_LIMITS}
            rules.merged_limits(lim)   # validates types (raises on garbage)
            db.set_setting("limits", lim)
        if "crew" in body:
            crew = {k: v for k, v in body["crew"].items() if k in planner.DEFAULT_CREW}
            for k in ("start", "end"):
                if k in crew:
                    planner.hhmm_to_min(crew[k])
            db.set_setting("crew", crew)
        if "operator" in body:
            db.set_setting("operator", {k: str(v)[:300] for k, v in body["operator"].items() if k in DEFAULT_OPERATOR})
        if body.get("tz"):
            ZoneInfo(body["tz"])
            db.set_setting("tz", body["tz"])
    return jsonify(settings())


@app.errorhandler(ValueError)
def _bad_value(e):
    return jsonify(error=str(e)), 400


# ---------- CRUD ----------
def _prep_field(data):
    if "geom" in data:
        g = geo.valid_geometry(data["geom"])
        if not g:
            raise ValueError("Field boundary must be a polygon")
        data["geom"] = g
        data["acres"] = round(geo.area_acres(g), 2)
        data["lat"], data["lon"] = geo.centroid(g)
    return data


def _prep(table, data):
    data = dict(data or {})
    if table == "fields":
        data = _prep_field(data)
    elif table == "sensitive" and "geom" in data:
        g = geo.valid_geometry(data["geom"])
        if not g:
            raise ValueError("Sensitive area must be a polygon")
        data["geom"] = g
    elif table == "jobs":
        if "status" in data and data["status"] not in ("open", "done", "cancelled"):
            raise ValueError("status must be open, done or cancelled")
    return data


@app.route("/api/<table>", methods=["GET", "POST"])
def api_list(table):
    if table not in CRUD_TABLES:
        abort(404)
    if request.method == "POST":
        data = _prep(table, request.get_json(force=True))
        if table == "jobs":
            data.setdefault("status", "open")
            data.setdefault("priority", 1)
            if not data.get("field_id") or not db.get("fields", data["field_id"]):
                raise ValueError("Pick a field")
        if table in ("pilots", "aircraft"):
            data.setdefault("active", 1)
        rid = db.insert(table, data)
        return jsonify(db.get(table, rid)), 201
    return jsonify(db.all_rows(table))


@app.route("/api/fields/bulk", methods=["POST"])
def api_fields_bulk():
    body = request.get_json(force=True) or {}
    ids = []
    for f in body.get("fields", [])[:500]:
        d = _prep_field({"name": f.get("name"), "geom": f.get("geometry") or f.get("geom"), "customer_id": body.get("customer_id") or f.get("customer_id"),
                         "crop": f.get("crop") or body.get("crop")})
        ids.append(db.insert("fields", d))
    return jsonify([db.get("fields", i) for i in ids]), 201


@app.route("/api/<table>/<int:rid>", methods=["GET", "PUT", "DELETE"])
def api_item(table, rid):
    if table not in CRUD_TABLES:
        abort(404)
    row = db.get(table, rid)
    if not row:
        abort(404)
    if request.method == "GET":
        return jsonify(row)
    if request.method == "DELETE":
        if table == "fields":
            if db.all_rows("jobs", "field_id = ? AND status = 'done'", (rid,)):
                return jsonify(error="This field has application records. Records must be kept; archive the field instead of deleting it."), 409
            db.q("DELETE FROM jobs WHERE field_id = ?", (rid,))
        if table == "customers" and db.all_rows("fields", "customer_id = ?", (rid,)):
            return jsonify(error="Customer still has fields"), 409
        if table == "jobs" and row.get("status") == "done":
            return jsonify(error="Completed records can't be deleted (Part 137: keep 12 months). Edit it instead."), 409
        db.delete(table, rid)
        return jsonify(ok=True)
    db.update(table, rid, _prep(table, request.get_json(force=True)))
    return jsonify(db.get(table, rid))


@app.route("/api/bootstrap")
def api_bootstrap():
    return jsonify(settings=settings(), version=APP_VERSION, mock=weather.mock_enabled(),
                   **{t: db.all_rows(t) for t in sorted(CRUD_TABLES)})


@app.route("/api/import", methods=["POST"])
def api_import():
    f = request.files.get("file")
    if not f:
        raise ValueError("No file")
    try:
        feats = geo.parse_boundary_file(f.filename, f.read())
    except geo.ImportError_ as e:
        return jsonify(error=str(e)), 400
    return jsonify(features=feats[:500])


# ---------- spray windows ----------
_exposure_cache = {}
_exp_lock = threading.Lock()


def _h(obj):
    return hashlib.sha1(json.dumps(obj, sort_keys=True).encode()).hexdigest()[:16]


def exposures_for(field, areas):
    """[(area name, wind-toward bearings, nearest m)] for sensitive areas within reach of this field."""
    out = []
    for a in areas:
        if not a.get("geom") or not field.get("geom"):
            continue
        buf_m = (a.get("buffer_ft") or 0) / geo.FT_PER_M
        key = (_h(field["geom"]), _h(a["geom"]), round(buf_m, 1))
        with _exp_lock:
            hit = _exposure_cache.get(key)
        if hit is None:
            hit = geo.exposure_sector(field["geom"], a["geom"], buf_m)
            with _exp_lock:
                if len(_exposure_cache) > 5000:
                    _exposure_cache.clear()
                _exposure_cache[key] = hit
        if hit[0]:
            out.append((a.get("name") or a.get("kind") or "sensitive area", hit[0], hit[1]))
    return out


def field_windows(fields):
    """{field_id: {"tz", "source", "hours": [evaluate_hour...]}} for fields with a location."""
    lim = rules.merged_limits(db.get_setting("limits", {}))
    areas = db.all_rows("sensitive")
    locs = {(f["lat"], f["lon"]) for f in fields if f.get("lat") is not None}
    fcs = weather.get_forecasts(locs)
    out = {}
    tz_seen = None
    for f in fields:
        fc = fcs.get((f.get("lat"), f.get("lon")))
        if not fc:
            continue
        tz_seen = tz_seen or fc["tz"]
        out[f["id"]] = {"tz": fc["tz"], "source": fc["source"], "fetched": fc["fetched"],
                        "hours": rules.evaluate(fc, lim, exposures_for(f, areas))}
    if tz_seen and not db.get_setting("tz"):
        db.set_setting("tz", tz_seen)
    return out


def _fields_by_ids(ids):
    fields = db.all_rows("fields")
    if ids:
        want = {int(i) for i in ids}
        fields = [f for f in fields if f["id"] in want]
    return fields


@app.route("/api/windows")
def api_windows():
    ids = [i for i in request.args.get("field_ids", "").split(",") if i.strip()]
    w = field_windows(_fields_by_ids(ids))
    return jsonify({str(k): v for k, v in w.items()})


@app.route("/api/now")
def api_now():
    now = time.time()
    fields = db.all_rows("fields")
    w = field_windows(fields)
    rows = []
    for f in fields:
        fw = w.get(f["id"])
        if not fw:
            rows.append({"field_id": f["id"], "name": f["name"], "status": None, "reasons": [(rules.NOGO, "No forecast available")]})
            continue
        hrs = fw["hours"]
        i = next((n for n, h in enumerate(hrs) if h["t"] - 1800 <= now < h["t"] + 1800), None)
        if i is None:
            continue
        h = hrs[i]
        nxt = next((x["t"] for x in hrs[i + 1:] if x["status"] == rules.GO), None) if h["status"] != rules.GO else None
        until = None
        if h["status"] == rules.GO:
            until = next((x["t"] for x in hrs[i + 1:] if x["status"] != rules.GO), None)
        rows.append({"field_id": f["id"], "name": f["name"], **h, "next_go": nxt, "go_until": until, "tz": fw["tz"]})
    rows.sort(key=lambda r: (rules.RANK.get(r.get("status"), 3), r["name"] or ""))
    return jsonify(now=now, fields=rows)


# ---------- planner ----------
def usable_slots(hours, day_start_ts, allow_caution):
    ok = {rules.GO, rules.CAUTION} if allow_caution else {rules.GO}
    out = []
    if not hours:
        return [False] * planner.SLOTS_PER_DAY
    t0 = hours[0]["t"]
    for s in range(planner.SLOTS_PER_DAY):
        ts = day_start_ts + (s * planner.SLOT_MIN + planner.SLOT_MIN / 2) * 60
        i = int(round((ts - t0) / 3600.0))          # nearest forecast hour: each hour's status covers ±30 min
        out.append(0 <= i < len(hours) and hours[i]["status"] in ok)
    return out


@app.route("/api/plan", methods=["POST"])
def api_plan():
    body = request.get_json(force=True) or {}
    st = settings()
    tz = ZoneInfo(st["tz"])
    day = date.fromisoformat(body.get("date") or datetime.now(tz).date().isoformat())
    day_start = datetime(day.year, day.month, day.day, tzinfo=tz).timestamp()
    crew = {**st["crew"], **{k: v for k, v in (body.get("crew") or {}).items() if k in planner.DEFAULT_CREW}}
    jobs = db.all_rows("jobs", "status = 'open'")
    if body.get("job_ids"):
        want = {int(i) for i in body["job_ids"]}
        jobs = [j for j in jobs if j["id"] in want]
    fields = {f["id"]: f for f in db.all_rows("fields")}
    jobs = [j for j in jobs if j["field_id"] in fields]
    w = field_windows([fields[j["field_id"]] for j in jobs])
    pj, usable = [], {}
    now = time.time()
    for j in jobs:
        f = fields[j["field_id"]]
        if f["id"] not in w:
            continue
        pj.append({"id": j["id"], "name": f["name"] + (f" — {j['product']}" if j.get("product") else ""), "lat": f["lat"], "lon": f["lon"],
                   "acres": f["acres"] or 0, "priority": j.get("priority") or 1})
        u = usable_slots(w[f["id"]]["hours"], day_start, crew.get("allow_caution"))
        for s in range(planner.SLOTS_PER_DAY):      # never plan in the past
            if day_start + s * planner.SLOT_MIN * 60 < now:
                u[s] = False
        usable[j["id"]] = u
    if day_start + 24 * 3600 > now and day_start < now:   # today: crew can't start before now
        now_min = (now - day_start) / 60
        if planner.hhmm_to_min(crew["start"]) < now_min:
            crew["start"] = planner.min_to_hhmm(min(24 * 60 - 1, now_min + 4))
    res = planner.plan_day(pj, usable, crew, budget_s=float(body.get("budget_s", 0.8)))
    for s in res["stops"]:
        s["field_id"] = next(j["field_id"] for j in jobs if j["id"] == s["job_id"])
    res.update(date=day.isoformat(), tz=st["tz"], crew=crew,
               skipped=[j["id"] for j in jobs if j["field_id"] not in w])
    return jsonify(res)


# ---------- records ----------
def weather_snapshot(field, applied_date, start_time, tz):
    """Forecast numbers for the hour of application (what the record captures when nothing is typed in)."""
    try:
        d = date.fromisoformat(applied_date)
        hh = planner.hhmm_to_min(start_time or "12:00")
    except (TypeError, ValueError):
        return None
    ts = datetime(d.year, d.month, d.day, tzinfo=ZoneInfo(tz)).timestamp() + hh * 60
    fc = weather.get_forecasts([(field["lat"], field["lon"])]).get((field["lat"], field["lon"]))
    if not fc:
        return None
    i = rules.hour_index(fc, ts)
    if i is None:
        return None
    return {"wx_wind": fc["wind"][i], "wx_gust": fc["gust"][i], "wx_dir": fc["dir"][i], "wx_temp": fc["temp"][i], "wx_rh": fc["rh"][i]}


@app.route("/api/jobs/<int:jid>/wx")
def api_job_wx(jid):
    j = db.get("jobs", jid) or abort(404)
    f = db.get("fields", j["field_id"]) or abort(404)
    snap = weather_snapshot(f, request.args.get("date"), request.args.get("time"), settings()["tz"])
    return jsonify(snap or {})


@app.route("/api/jobs/<int:jid>/complete", methods=["POST"])
def api_job_complete(jid):
    j = db.get("jobs", jid) or abort(404)
    f = db.get("fields", j["field_id"]) or abort(404)
    body = request.get_json(force=True) or {}
    tz = settings()["tz"]
    data = {k: body.get(k) for k in ("applied_date", "start_time", "end_time", "acres_treated", "total_amount", "amount_unit",
                                    "pilot_id", "aircraft_id", "wx_wind", "wx_gust", "wx_dir", "wx_temp", "wx_rh", "notes",
                                    "product", "epa_reg", "rate", "rate_unit", "carrier_gpa", "target") if k in body}
    data.setdefault("applied_date", datetime.now(ZoneInfo(tz)).date().isoformat())
    if not data.get("acres_treated"):
        data["acres_treated"] = f.get("acres")
    if not data.get("pilot_id"):
        raise ValueError("Pick the pilot (Part 137 record)")
    if not data.get("product"):
        data.pop("product", None)
        if not j.get("product"):
            raise ValueError("Enter the product applied")
    if all(data.get(k) in (None, "") for k in ("wx_wind", "wx_temp")):
        snap = weather_snapshot(f, data["applied_date"], data.get("start_time"), tz)
        if snap:
            data.update(snap)
    if data.get("total_amount") in (None, "") and (data.get("rate") or j.get("rate")):
        data["total_amount"] = round(float(data.get("rate") or j["rate"]) * float(data["acres_treated"]), 2)
        data.setdefault("amount_unit", (data.get("rate_unit") or j.get("rate_unit") or "").split("/")[0].strip() or None)
    data.update(status="done", completed_at=time.time())
    db.update("jobs", jid, data)
    return jsonify(db.get("jobs", jid))


RECORD_COLS = [
    ("Date", "applied_date"), ("Start", "start_time"), ("End", "end_time"), ("Customer", "customer"), ("Customer address", "customer_address"),
    ("Field", "field"), ("Crop", "crop"), ("Acres treated", "acres_treated"), ("Product", "product"), ("EPA reg. no.", "epa_reg"),
    ("Rate", "rate"), ("Rate unit", "rate_unit"), ("Total applied", "total_amount"), ("Unit", "amount_unit"), ("Carrier gal/ac", "carrier_gpa"),
    ("Target", "target"), ("Pilot", "pilot"), ("Pilot address", "pilot_address"), ("Pilot cert. no.", "pilot_cert"),
    ("Pilot qualification date", "pilot_qual"), ("Aircraft", "aircraft"), ("Registration", "registration"), ("Serial", "serial"),
    ("Wind mph", "wx_wind"), ("Gust mph", "wx_gust"), ("Wind from", "wx_dir_txt"), ("Temp °F", "wx_temp"), ("RH %", "wx_rh"), ("Notes", "notes"),
]


def records(frm=None, to=None):
    rows = db.all_rows("jobs", "status = 'done'", order="applied_date, start_time, id")
    fields = {f["id"]: f for f in db.all_rows("fields")}
    cust = {c["id"]: c for c in db.all_rows("customers")}
    pilots = {p["id"]: p for p in db.all_rows("pilots")}
    ac = {a["id"]: a for a in db.all_rows("aircraft")}
    out = []
    for j in rows:
        if frm and (j.get("applied_date") or "") < frm:
            continue
        if to and (j.get("applied_date") or "") > to:
            continue
        f = fields.get(j["field_id"], {})
        c = cust.get(f.get("customer_id"), {})
        p = pilots.get(j.get("pilot_id"), {})
        a = ac.get(j.get("aircraft_id"), {})
        out.append({**j, "field": f.get("name"), "crop": f.get("crop"), "customer": c.get("name"), "customer_address": c.get("address"),
                    "pilot": p.get("name"), "pilot_address": p.get("address"), "pilot_cert": p.get("cert_no"), "pilot_qual": p.get("qual_date"),
                    "aircraft": a.get("name") or a.get("make_model"), "registration": a.get("registration"), "serial": a.get("serial"),
                    "wx_dir_txt": geo.compass(j["wx_dir"]) if j.get("wx_dir") is not None else None})
    return out


@app.route("/api/records")
def api_records():
    return jsonify(records(request.args.get("from"), request.args.get("to")))


def _fmt(v):
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.2f}".rstrip("0").rstrip(".")
    return str(v)


@app.route("/api/records.csv")
def api_records_csv():
    rows = records(request.args.get("from"), request.args.get("to"))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([c[0] for c in RECORD_COLS])
    for r in rows:
        w.writerow([_csv_safe(_fmt(r.get(k))) for _, k in RECORD_COLS])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=swath-records-{date.today().isoformat()}.csv"})


def _csv_safe(s):
    """Stop spreadsheet formula injection from user-typed text."""
    return "'" + s if s[:1] in ("=", "+", "-", "@") and not _is_number(s) else s


def _is_number(s):
    try:
        float(s)
        return True
    except ValueError:
        return False


@app.route("/api/records.pdf")
def api_records_pdf():
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape

    frm, to = request.args.get("from"), request.args.get("to")
    rows = records(frm, to)
    op = settings()["operator"]
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(letter), leftMargin=0.4 * inch, rightMargin=0.4 * inch, topMargin=0.4 * inch, bottomMargin=0.4 * inch,
                            title="Application records")
    ss = getSampleStyleSheet()
    small = ss["BodyText"].clone("small", fontSize=6.5, leading=8)
    head = [Paragraph(escape(op.get("name") or "Application records"), ss["Title"])]
    sub = " · ".join(x for x in [op.get("address"), op.get("phone"), op.get("cert_no") and f"Part 137 cert. {op['cert_no']}",
                                 op.get("exemption_no") and f"Exemption {op['exemption_no']}"] if x)
    head.append(Paragraph(escape(sub) + "<br/>" + escape(f"Application records {frm or 'all'} to {to or 'today'} · {len(rows)} records"), ss["Normal"]))
    head.append(Spacer(1, 8))
    cols = [("Date / time", lambda r: f"{r.get('applied_date') or ''} {r.get('start_time') or ''}–{r.get('end_time') or ''}"),
            ("Customer", lambda r: f"{r.get('customer') or ''}\n{r.get('customer_address') or ''}"),
            ("Field / crop / acres", lambda r: f"{r.get('field') or ''}\n{r.get('crop') or ''} · {_fmt(r.get('acres_treated'))} ac"),
            ("Product / EPA reg.", lambda r: f"{r.get('product') or ''}\n{r.get('epa_reg') or ''}"),
            ("Rate / total", lambda r: f"{_fmt(r.get('rate'))} {r.get('rate_unit') or ''}\n{_fmt(r.get('total_amount'))} {r.get('amount_unit') or ''}"),
            ("Pilot / cert. / qual.", lambda r: f"{r.get('pilot') or ''}\n{r.get('pilot_cert') or ''} · {r.get('pilot_qual') or ''}\n{r.get('pilot_address') or ''}"),
            ("Aircraft", lambda r: f"{r.get('aircraft') or ''}\n{r.get('registration') or ''} {r.get('serial') or ''}"),
            ("Weather", lambda r: f"{_fmt(r.get('wx_wind'))} mph {r.get('wx_dir_txt') or ''} g{_fmt(r.get('wx_gust'))}\n{_fmt(r.get('wx_temp'))}°F {_fmt(r.get('wx_rh'))}% RH")]
    data = [[Paragraph(f"<b>{c[0]}</b>", small) for c in cols]]
    for r in rows:
        data.append([Paragraph(escape(fn(r)).replace("\n", "<br/>"), small) for _, fn in cols])
    t = Table(data, repeatRows=1, colWidths=[1.0 * inch, 1.5 * inch, 1.3 * inch, 1.3 * inch, 1.1 * inch, 1.6 * inch, 1.1 * inch, 1.2 * inch])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8efe6")),
                           ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    doc.build(head + [t])
    return Response(buf.getvalue(), mimetype="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename=swath-records-{date.today().isoformat()}.pdf"})


@app.route("/api/backup")
def api_backup():
    body = {t: db.all_rows(t) for t in sorted(CRUD_TABLES)}
    body.update(settings=settings(), version=APP_VERSION, exported=time.time())
    return Response(json.dumps(body, indent=1), mimetype="application/json",
                    headers={"Content-Disposition": f"attachment; filename=swath-backup-{date.today().isoformat()}.json"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=os.environ.get("FLASK_DEBUG") == "1")
