"""Storage: Postgres when DATABASE_URL is set, else a local SQLite file (dev/tests). SQL is written to run on both;
placeholders are written as ? and converted for psycopg."""
import json, os, sqlite3, threading, time

DATABASE_URL = os.environ.get("DATABASE_URL")
SQLITE_PATH = os.environ.get("SWATH_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "swath.db"))
_local = threading.local()

# table -> {column: type}. id + created are added automatically.
TABLES = {
    "customers": {"name": "TEXT", "address": "TEXT", "phone": "TEXT", "email": "TEXT", "notes": "TEXT"},
    "fields": {"name": "TEXT", "customer_id": "INTEGER", "crop": "TEXT", "notes": "TEXT", "geom": "TEXT",
               "acres": "REAL", "lat": "REAL", "lon": "REAL"},
    "sensitive": {"name": "TEXT", "kind": "TEXT", "geom": "TEXT", "buffer_ft": "REAL", "notes": "TEXT"},
    "pilots": {"name": "TEXT", "address": "TEXT", "cert_no": "TEXT", "qual_date": "TEXT", "active": "INTEGER"},
    "aircraft": {"name": "TEXT", "make_model": "TEXT", "registration": "TEXT", "serial": "TEXT", "active": "INTEGER"},
    "jobs": {"field_id": "INTEGER", "status": "TEXT", "priority": "INTEGER", "product": "TEXT", "epa_reg": "TEXT",
             "rate": "REAL", "rate_unit": "TEXT", "carrier_gpa": "REAL", "target": "TEXT", "notes": "TEXT", "due_date": "TEXT",
             "applied_date": "TEXT", "start_time": "TEXT", "end_time": "TEXT", "acres_treated": "REAL", "total_amount": "REAL",
             "amount_unit": "TEXT", "pilot_id": "INTEGER", "aircraft_id": "INTEGER",
             "wx_wind": "REAL", "wx_gust": "REAL", "wx_dir": "REAL", "wx_temp": "REAL", "wx_rh": "REAL", "completed_at": "REAL"},
}


def is_pg():
    return bool(DATABASE_URL)


def _conn():
    c = getattr(_local, "conn", None)
    if c is not None:
        return c
    if is_pg():
        import psycopg
        c = psycopg.connect(DATABASE_URL, autocommit=True)
    else:
        c = sqlite3.connect(os.environ.get("SWATH_DB", SQLITE_PATH), check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
    _local.conn = c
    return c


def reset_connection():
    c = getattr(_local, "conn", None)
    if c is not None:
        try:
            c.close()
        except Exception:
            pass
    _local.conn = None


def q(sql, args=(), fetch=None):
    """fetch: None, 'one', 'all'. Returns dict rows."""
    for attempt in (0, 1):
        c = _conn()
        try:
            if is_pg():
                from psycopg.rows import dict_row
                with c.cursor(row_factory=dict_row) as cur:
                    cur.execute(sql.replace("?", "%s"), args)
                    if fetch == "one":
                        return cur.fetchone()
                    if fetch == "all":
                        return cur.fetchall()
                    return None
            cur = c.execute(sql, args)
            if fetch == "one":
                r = cur.fetchone()
                c.commit()
                return dict(r) if r else None
            if fetch == "all":
                rows = [dict(r) for r in cur.fetchall()]
                c.commit()
                return rows
            c.commit()
            return None
        except Exception as e:
            if attempt == 0 and is_pg() and "closed" in str(e).lower():
                reset_connection()
                continue
            raise


def init():
    idcol = "id SERIAL PRIMARY KEY" if is_pg() else "id INTEGER PRIMARY KEY AUTOINCREMENT"
    q("CREATE TABLE IF NOT EXISTS settings (k TEXT PRIMARY KEY, v TEXT)")
    for t, cols in TABLES.items():
        body = ", ".join(f"{c} {ty}" for c, ty in cols.items())
        q(f"CREATE TABLE IF NOT EXISTS {t} ({idcol}, {body}, created REAL)")
        existing = _columns(t)
        for c, ty in cols.items():        # forward-only migrations: add new columns
            if c not in existing:
                q(f"ALTER TABLE {t} ADD COLUMN {c} {ty}")


def _columns(table):
    if is_pg():
        rows = q("SELECT column_name FROM information_schema.columns WHERE table_name = ?", (table,), "all")
        return {r["column_name"] for r in rows}
    return {r["name"] for r in q(f"PRAGMA table_info({table})", fetch="all")}


def _clean(table, data):
    cols = TABLES[table]
    out = {}
    for k, v in (data or {}).items():
        if k not in cols:
            continue
        if v == "":
            v = None
        if v is not None:
            if cols[k] == "INTEGER":
                v = int(v) if not isinstance(v, bool) else int(v)
            elif cols[k] == "REAL":
                v = float(v)
            elif k == "geom" and not isinstance(v, str):
                v = json.dumps(v)
            else:
                v = str(v)
        out[k] = v
    return out


def insert(table, data):
    d = _clean(table, data)
    d_cols = list(d) + ["created"]
    vals = list(d.values()) + [time.time()]
    sql = f"INSERT INTO {table} ({', '.join(d_cols)}) VALUES ({', '.join('?' for _ in d_cols)}) RETURNING id"
    return q(sql, vals, "one")["id"]


def update(table, rid, data):
    d = _clean(table, data)
    if not d:
        return
    q(f"UPDATE {table} SET {', '.join(f'{k} = ?' for k in d)} WHERE id = ?", list(d.values()) + [int(rid)])


def delete(table, rid):
    q(f"DELETE FROM {table} WHERE id = ?", (int(rid),))


def get(table, rid):
    return _decode(q(f"SELECT * FROM {table} WHERE id = ?", (int(rid),), "one"))


def all_rows(table, where="", args=(), order="id"):
    return [_decode(r) for r in q(f"SELECT * FROM {table} {('WHERE ' + where) if where else ''} ORDER BY {order}", args, "all")]


def _decode(r):
    if r and r.get("geom") and isinstance(r["geom"], str):
        try:
            r["geom"] = json.loads(r["geom"])
        except ValueError:
            r["geom"] = None
    return r


def get_setting(k, default=None):
    r = q("SELECT v FROM settings WHERE k = ?", (k,), "one")
    if not r:
        return default
    try:
        return json.loads(r["v"])
    except ValueError:
        return default


def set_setting(k, v):
    s = json.dumps(v)
    if is_pg():
        q("INSERT INTO settings (k, v) VALUES (?, ?) ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v", (k, s))
    else:
        q("INSERT OR REPLACE INTO settings (k, v) VALUES (?, ?)", (k, s))
