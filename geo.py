"""Geometry: field area, distances, bearings, drift-toward-sensitive-area sectors, and boundary file import.
All bearings are TRUE degrees clockwise from north. Geometries are GeoJSON dicts (lon, lat order), Polygon or MultiPolygon."""
import io, json, math, zipfile
import xml.etree.ElementTree as ET

R_EARTH_M = 6371008.8
M2_PER_ACRE = 4046.8564224
M_PER_MILE = 1609.344
FT_PER_M = 3.280839895


# ---------- basics ----------
def haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R_EARTH_M * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1, lon1, lat2, lon2):
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(y, x)) % 360


def ang_diff(a, b):
    """Smallest absolute difference between two bearings, 0..180."""
    d = abs(a - b) % 360
    return 360 - d if d > 180 else d


def compass(deg):
    return ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"][int((deg % 360) / 22.5 + 0.5) % 16]


# ---------- polygons ----------
def polygons(geom):
    """List of polygons, each a list of rings (outer first), each ring a list of (lon, lat)."""
    if not geom:
        return []
    t = geom.get("type")
    if t == "Polygon":
        return [[[(float(p[0]), float(p[1])) for p in ring] for ring in geom["coordinates"]]]
    if t == "MultiPolygon":
        return [[[(float(p[0]), float(p[1])) for p in ring] for ring in poly] for poly in geom["coordinates"]]
    if t == "Feature":
        return polygons(geom.get("geometry"))
    return []


def _ring_area_m2(ring):
    """Signed spherical area of a ring (Chamberlain & Duquette, 2007)."""
    if len(ring) < 3:
        return 0.0
    s = 0.0
    n = len(ring)
    for i in range(n):
        lon1, lat1 = ring[i]
        lon2, lat2 = ring[(i + 1) % n]
        s += math.radians(lon2 - lon1) * (2 + math.sin(math.radians(lat1)) + math.sin(math.radians(lat2)))
    return s * R_EARTH_M ** 2 / 2


def area_acres(geom):
    total = 0.0
    for poly in polygons(geom):
        rings = [abs(_ring_area_m2(r)) for r in poly]
        if rings:
            total += rings[0] - sum(rings[1:])
    return total / M2_PER_ACRE


def centroid(geom):
    """Area-weighted centroid (lat, lon) of the outer rings, via a local flat projection; falls back to the vertex mean."""
    polys = polygons(geom)
    pts = [p for poly in polys for p in poly[0]]
    if not pts:
        return None
    lon0 = sum(p[0] for p in pts) / len(pts)
    lat0 = sum(p[1] for p in pts) / len(pts)
    A = cx = cy = 0.0
    for poly in polys:
        ring = [_proj(p, lat0, lon0) for p in poly[0]]
        for i in range(len(ring)):
            x1, y1 = ring[i]
            x2, y2 = ring[(i + 1) % len(ring)]
            c = x1 * y2 - x2 * y1
            A += c
            cx += (x1 + x2) * c
            cy += (y1 + y2) * c
    if abs(A) < 1e-9:
        return (lat0, lon0)
    cx, cy = cx / (3 * A), cy / (3 * A)
    return _unproj((cx, cy), lat0, lon0)


def _proj(p, lat0, lon0):
    """(lon, lat) -> local (x east, y north) metres around (lat0, lon0). Good to well under 1 % over a few miles."""
    return ((p[0] - lon0) * 111320.0 * math.cos(math.radians(lat0)), (p[1] - lat0) * 110574.0)


def _unproj(xy, lat0, lon0):
    return (lat0 + xy[1] / 110574.0, lon0 + xy[0] / (111320.0 * math.cos(math.radians(lat0))))


def _segments(rings_xy):
    for ring in rings_xy:
        n = len(ring)
        for i in range(n):
            a, b = ring[i], ring[(i + 1) % n]
            if a != b:
                yield a, b


def _point_in_rings(pt, rings_xy):
    """Even-odd rule over all rings (handles holes)."""
    x, y = pt
    inside = False
    for ring in rings_xy:
        n = len(ring)
        for i in range(n):
            (x1, y1), (x2, y2) = ring[i], ring[(i + 1) % n]
            if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
                inside = not inside
    return inside


def _ray_hit(p, u, a, b):
    """Distance s >= 0 along ray p + s*u to segment ab, or None."""
    ex, ey = b[0] - a[0], b[1] - a[1]
    den = u[0] * ey - u[1] * ex
    if abs(den) < 1e-12:
        return None
    wx, wy = a[0] - p[0], a[1] - p[1]
    s = (wx * ey - wy * ex) / den
    t = (wx * u[1] - wy * u[0]) / den
    if s >= 0 and -1e-9 <= t <= 1 + 1e-9:
        return s
    return None


def _seg_dist(p, a, b):
    ex, ey = b[0] - a[0], b[1] - a[1]
    L = ex * ex + ey * ey
    t = 0 if L == 0 else max(0, min(1, ((p[0] - a[0]) * ex + (p[1] - a[1]) * ey) / L))
    return math.hypot(p[0] - a[0] - t * ex, p[1] - a[1] - t * ey)


def _xy_polys(geom, lat0, lon0):
    return [[[_proj(p, lat0, lon0) for p in ring] for ring in poly] for poly in polygons(geom)]


def min_distance_m(geom_a, geom_b):
    """Shortest distance between two (multi)polygons, 0 if they overlap."""
    c = centroid(geom_a)
    if not c:
        return float("inf")
    A, B = _xy_polys(geom_a, *c), _xy_polys(geom_b, *c)
    for pa in A:
        for pb in B:
            if (pa[0] and _point_in_rings(pa[0][0], pb)) or (pb[0] and _point_in_rings(pb[0][0], pa)):
                return 0.0
    best = float("inf")
    sa = [s for pa in A for s in _segments(pa)]
    sb = [s for pb in B for s in _segments(pb)]
    for pa in A:
        for ring in pa:
            for p in ring:
                for a, b in sb:
                    best = min(best, _seg_dist(p, a, b))
    for pb in B:
        for ring in pb:
            for p in ring:
                for a, b in sa:
                    best = min(best, _seg_dist(p, a, b))
    return best


def drift_distance_m(field_geom, area_geom, bearing):
    """How far the field must be slid along `bearing` (the direction the wind blows TOWARD) before it touches the area.
    0 if they already overlap, inf if it never does. Exact for polygons: the first contact is always a vertex of one
    shape meeting an edge of the other."""
    c = centroid(field_geom)
    F, S = _xy_polys(field_geom, *c), _xy_polys(area_geom, *c)
    for pf in F:
        for ps in S:
            if (pf[0] and _point_in_rings(pf[0][0], ps)) or (ps[0] and _point_in_rings(ps[0][0], pf)):
                return 0.0
    th = math.radians(bearing)
    u, nu = (math.sin(th), math.cos(th)), (-math.sin(th), -math.cos(th))
    sf = [s for pf in F for s in _segments(pf)]
    ss = [s for ps in S for s in _segments(ps)]
    best = float("inf")
    for pf in F:
        for ring in pf:
            for p in ring:
                for a, b in ss:
                    s = _ray_hit(p, u, a, b)
                    if s is not None and s < best:
                        best = s
    for ps in S:
        for ring in ps:
            for p in ring:
                for a, b in sf:
                    s = _ray_hit(p, nu, a, b)
                    if s is not None and s < best:
                        best = s
    return best


SECTOR_STEP_DEG = 5


def exposure_sector(field_geom, area_geom, buffer_m):
    """Wind-TOWARD bearings (multiples of SECTOR_STEP_DEG) for which drift from the field reaches the area within buffer_m.
    Returns (bearings, nearest_m). Empty list when the area is out of reach in every direction."""
    near = min_distance_m(field_geom, area_geom)
    if near > buffer_m:
        return [], near
    hits = [b for b in range(0, 360, SECTOR_STEP_DEG) if drift_distance_m(field_geom, area_geom, b) <= buffer_m]
    return hits, near


# ---------- import ----------
NAME_KEYS = ("name", "Name", "NAME", "field", "Field", "FIELD", "field_name", "FIELD_NAME", "FieldName", "FLD_NAME", "label", "title", "id")


class ImportError_(ValueError):
    pass


def _pick_name(props, i):
    for k in NAME_KEYS:
        v = (props or {}).get(k)
        if v not in (None, ""):
            return str(v)[:120]
    return f"Field {i + 1}"


def _clean_geom(geom):
    """Keep only polygonal parts; close rings; drop rings with < 3 distinct points. Returns Polygon/MultiPolygon or None."""
    polys = []
    for poly in polygons(geom):
        rings = []
        for ring in poly:
            pts = [(round(x, 7), round(y, 7)) for x, y in ring]
            if len(pts) > 1 and pts[0] == pts[-1]:
                pts = pts[:-1]
            if len(set(pts)) >= 3:
                rings.append([list(p) for p in pts] + [list(pts[0])])
        if rings:
            polys.append(rings)
    if not polys:
        return None
    for poly in polys:
        for ring in poly:
            for x, y in ring:
                if not (-180 <= x <= 180 and -90 <= y <= 90):
                    raise ImportError_("Coordinates are not longitude/latitude. Export the file in WGS84 (EPSG:4326).")
    return {"type": "Polygon", "coordinates": polys[0]} if len(polys) == 1 else {"type": "MultiPolygon", "coordinates": polys}


def parse_geojson(data):
    obj = json.loads(data) if isinstance(data, (bytes, str)) else data
    feats = []
    if obj.get("type") == "FeatureCollection":
        items = obj.get("features") or []
    elif obj.get("type") == "Feature":
        items = [obj]
    else:
        items = [{"type": "Feature", "properties": {}, "geometry": obj}]
    for i, f in enumerate(items):
        g = f.get("geometry")
        if g and g.get("type") == "GeometryCollection":
            parts = [p for p in g.get("geometries", []) if p.get("type") in ("Polygon", "MultiPolygon")]
            coords = [c for p in parts for c in ([p["coordinates"]] if p["type"] == "Polygon" else p["coordinates"])]
            g = {"type": "MultiPolygon", "coordinates": coords} if coords else None
        geom = _clean_geom(g)
        if geom:
            feats.append({"name": _pick_name(f.get("properties"), i), "geometry": geom})
    return feats


def _strip_ns(root):
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def _kml_ring(el):
    coords = el.find(".//coordinates")
    if coords is None or not coords.text:
        return None
    pts = []
    for tok in coords.text.split():
        parts = tok.split(",")
        if len(parts) >= 2:
            pts.append([float(parts[0]), float(parts[1])])
    return pts


def parse_kml(data):
    if b"<!ENTITY" in data[:4096]:
        raise ImportError_("KML files with entity declarations are not accepted.")
    root = _strip_ns(ET.fromstring(data))
    feats = []
    for i, pm in enumerate(root.iter("Placemark")):
        name_el = pm.find("name")
        polys = []
        for pg in pm.iter("Polygon"):
            outer = pg.find("outerBoundaryIs")
            ring = _kml_ring(outer) if outer is not None else None
            if not ring:
                continue
            holes = [r for r in (_kml_ring(ib) for ib in pg.findall("innerBoundaryIs")) if r]
            polys.append([ring] + holes)
        if not polys:
            continue
        geom = _clean_geom({"type": "MultiPolygon", "coordinates": polys})
        if geom:
            name = name_el.text.strip() if name_el is not None and name_el.text else f"Field {i + 1}"
            feats.append({"name": name[:120], "geometry": geom})
    return feats


def parse_shapefile_zip(data):
    import shapefile  # pyshp
    z = zipfile.ZipFile(io.BytesIO(data))
    names = z.namelist()
    shps = [n for n in names if n.lower().endswith(".shp") and not n.startswith("__MACOSX")]
    if not shps:
        raise ImportError_("The zip has no .shp file.")
    feats = []
    for shp in shps:
        base = shp[:-4]
        def part(ext):
            for n in names:
                if n.lower() == (base + ext).lower():
                    return io.BytesIO(z.read(n))
            return None
        prj = part(".prj")
        if prj is not None:
            wkt = prj.read().decode("latin-1").strip().upper()
            if wkt.startswith("PROJCS") or wkt.startswith("PROJCRS"):
                raise ImportError_(f"{shp} is in a projected coordinate system. Export it in WGS84 (EPSG:4326, longitude/latitude).")
        r = shapefile.Reader(shp=part(".shp"), shx=part(".shx"), dbf=part(".dbf"))
        fields = [f[0] for f in r.fields[1:]]
        for i, sr in enumerate(r.iterShapeRecords()):
            try:
                gi = sr.shape.__geo_interface__
            except Exception:
                continue
            geom = _clean_geom(gi)
            if geom:
                props = dict(zip(fields, sr.record)) if fields else {}
                feats.append({"name": _pick_name(props, len(feats)), "geometry": geom})
    return feats


def parse_boundary_file(filename, data):
    """Returns [{name, geometry, acres}] from GeoJSON, KML, KMZ or a zipped shapefile."""
    fn = (filename or "").lower()
    try:
        if fn.endswith(".kmz"):
            z = zipfile.ZipFile(io.BytesIO(data))
            kmls = [n for n in z.namelist() if n.lower().endswith(".kml")]
            if not kmls:
                raise ImportError_("The KMZ has no .kml inside.")
            feats = [f for n in kmls for f in parse_kml(z.read(n))]
        elif fn.endswith(".kml"):
            feats = parse_kml(data)
        elif fn.endswith(".zip"):
            z = zipfile.ZipFile(io.BytesIO(data))
            if any(n.lower().endswith(".shp") for n in z.namelist()):
                feats = parse_shapefile_zip(data)
            elif any(n.lower().endswith(".kml") for n in z.namelist()):
                feats = [f for n in z.namelist() if n.lower().endswith(".kml") for f in parse_kml(z.read(n))]
            else:
                raise ImportError_("The zip has no shapefile or KML.")
        elif fn.endswith((".geojson", ".json")):
            feats = parse_geojson(data)
        else:
            head = data[:200].lstrip()
            if head.startswith(b"{"):
                feats = parse_geojson(data)
            elif head.startswith(b"<"):
                feats = parse_kml(data)
            else:
                raise ImportError_("Unsupported file. Use GeoJSON, KML, KMZ or a zipped shapefile.")
    except ImportError_:
        raise
    except (ET.ParseError, json.JSONDecodeError, zipfile.BadZipFile, UnicodeDecodeError) as e:
        raise ImportError_(f"Could not read the file: {e}")
    for f in feats:
        f["acres"] = round(area_acres(f["geometry"]), 2)
    if not feats:
        raise ImportError_("No polygons found in the file.")
    return feats


def valid_geometry(geom):
    try:
        g = _clean_geom(geom)
    except ImportError_:
        return None
    return g
