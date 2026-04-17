# -*- coding: utf-8 -*-
"""
Sky-Net (Lite) Proximity Alert
================================
Uses Shapely to compute which active closures fall within a configurable
buffer radius of known military facilities.

Outputs:
  data/proximity_alerts.json  — machine-readable alert list
  data/proximity_alerts.html  — human-readable HTML report

Usage:
    python scripts/proximity_alert.py [--radius-nm 50]

Run after grab_notams.py.  Integrate into daily workflow:
    python scripts/grab_notams.py && python scripts/proximity_alert.py
"""

import io, json, math, os, sys, argparse
from datetime import datetime, timezone

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
else:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

try:
    from shapely.geometry import Point, MultiPoint, LineString, Polygon, box
    from shapely.ops import unary_union
except ImportError:
    print("ERROR: pip install shapely")
    sys.exit(1)

BASE_DIR        = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR        = os.path.join(BASE_DIR, "data")
CZML_PATH       = os.path.join(DATA_DIR, "closures.czml")
FACILITIES_PATH = os.path.join(DATA_DIR, "military_facilities.geojson")
BOX_SCORE_PATH  = os.path.join(DATA_DIR, "box_score.json")
GEO_CATALOG_PATH= os.path.join(DATA_DIR, "geo_catalog.json")
JSON_OUT        = os.path.join(DATA_DIR, "proximity_alerts.json")
HTML_OUT        = os.path.join(DATA_DIR, "proximity_alerts.html")

# 1 degree latitude ≈ 60 nm; use this for degree-space buffer approximation
NM_PER_DEG = 60.0


# ── Helpers ────────────────────────────────────────────────────────────────

def nm_to_deg(nm):
    """Convert nautical miles to approximate decimal degrees (latitude-based)."""
    return nm / NM_PER_DEG


def get_czml_prop(entity, key):
    props = entity.get("properties", {})
    val = props.get(key)
    if isinstance(val, dict):
        return val.get("string") or val.get("boolean") or val.get("number")
    return val


def czml_to_shapely(entity):
    """
    Convert a CZML entity's geometry to a Shapely geometry.
    Returns None if no geometry can be extracted.
    """
    # Polyline
    pl = entity.get("polyline", {})
    if pl:
        pos = pl.get("positions", {}).get("cartographicDegrees", [])
        if len(pos) >= 6:
            pts = [(pos[i], pos[i+1]) for i in range(0, len(pos), 3)]
            try:
                return LineString(pts).buffer(nm_to_deg(5))  # 5nm buffer around line
            except Exception:
                pass

    # Polygon
    pg = entity.get("polygon", {})
    if pg:
        pos = pg.get("positions", {}).get("cartographicDegrees", [])
        if len(pos) >= 9:
            pts = [(pos[i], pos[i+1]) for i in range(0, len(pos), 3)]
            try:
                return Polygon(pts)
            except Exception:
                pass

    # Point
    pt = entity.get("position", {})
    if pt:
        coords = pt.get("cartographicDegrees", [])
        if len(coords) >= 2:
            return Point(coords[0], coords[1])

    return None


def load_czml_entities(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        data = json.load(f)
    return [e for e in data if isinstance(e, dict) and e.get("id") != "document"]


def load_facilities(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    facilities = []
    for feat in data.get("features", []):
        props = feat.get("properties", {})
        coords = feat.get("geometry", {}).get("coordinates")
        if coords and len(coords) >= 2:
            facilities.append({
                "name":      props.get("name", "Unknown"),
                "component": props.get("component", ""),
                "type":      props.get("type", ""),
                "state":     props.get("state", ""),
                "notes":     props.get("notes", ""),
                "lon":       coords[0],
                "lat":       coords[1],
                "point":     Point(coords[0], coords[1])
            })
    return facilities


# ── Orbital debris cross-reference ────────────────────────────────────────

def load_box_score():
    """Load box_score.json → dict keyed by SPADOC_CD."""
    if not os.path.exists(BOX_SCORE_PATH):
        return {}
    with open(BOX_SCORE_PATH, encoding='utf-8') as f:
        rows = json.load(f)
    return {r.get('SPADOC_CD', '').strip(): r for r in rows if r.get('SPADOC_CD')}


def load_geo_catalog():
    """Load geo_catalog.json → list of satellite dicts."""
    if not os.path.exists(GEO_CATALOG_PATH):
        return []
    with open(GEO_CATALOG_PATH, encoding='utf-8') as f:
        return json.load(f)


def get_debris_for_country(spadoc_cd, geo_catalog):
    """
    Return list of debris objects from geo_catalog matching a SPADOC_CD country code.
    Matches on COUNTRY field (Space-Track uses SPADOC_CD as country code in geo_report).
    """
    return [
        obj for obj in geo_catalog
        if obj.get('COUNTRY', '').strip().upper() == spadoc_cd.upper()
        and ('DEB' in obj.get('SATNAME', '').upper() or
             obj.get('DISPLAY_TYPE') == 'DEBRIS')
    ]


def get_spadoc_from_source(source_text, box_score):
    """
    Try to match a closure source/description to a SPADOC_CD country code.
    Checks if any country name from box_score appears in the source text.
    Returns list of matching (SPADOC_CD, country_name) tuples.
    """
    source_upper = source_text.upper()
    matches = []
    for spadoc, row in box_score.items():
        country = row.get('COUNTRY', '').upper()
        if country and len(country) > 3 and country in source_upper:
            matches.append((spadoc, row.get('COUNTRY', '')))
    return matches


# ── Main ───────────────────────────────────────────────────────────────────

def run(radius_nm=50):
    run_time = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"Sky-Net (Lite) Proximity Alert — radius: {radius_nm} nm — {run_time}")

    if not os.path.exists(CZML_PATH):
        print(f"ERROR: {CZML_PATH} not found. Run grab_notams.py first.")
        sys.exit(1)
    if not os.path.exists(FACILITIES_PATH):
        print(f"ERROR: {FACILITIES_PATH} not found.")
        sys.exit(1)

    entities    = load_czml_entities(CZML_PATH)
    facilities  = load_facilities(FACILITIES_PATH)
    box_score   = load_box_score()
    geo_catalog = load_geo_catalog()
    print(f"Loaded {len(entities)} closures, {len(facilities)} facilities")
    if box_score:
        print(f"Loaded {len(box_score)} countries from box_score.json")
    if geo_catalog:
        print(f"Loaded {len(geo_catalog)} GEO objects from geo_catalog.json")

    radius_deg = nm_to_deg(radius_nm)
    alerts = []

    for entity in entities:
        # Only process active entities with geometry
        active = get_czml_prop(entity, "active")
        if active is False or active == "false":
            continue

        geom = czml_to_shapely(entity)
        if geom is None:
            continue

        # Build buffered geometry for proximity check
        try:
            geom_buf = geom if geom.geom_type == 'Polygon' else geom.buffer(0)
        except Exception:
            continue

        name   = entity.get("name", entity.get("id", "?"))
        source = get_czml_prop(entity, "source") or "?"
        tags   = get_czml_prop(entity, "tags") or ""
        start  = get_czml_prop(entity, "timestamp") or "?"

        for fac in facilities:
            fac_buf = fac["point"].buffer(radius_deg)
            try:
                if geom_buf.intersects(fac_buf):
                    # Calculate actual distance in nm
                    dist_deg = geom.distance(fac["point"])
                    dist_nm  = dist_deg * NM_PER_DEG

                    alert = {
                        "closure_id":   entity.get("id", "?"),
                        "closure_name": name,
                        "source":       source,
                        "tags":         tags,
                        "start":        start,
                        "facility":     fac["name"],
                        "component":    fac["component"],
                        "fac_type":     fac["type"],
                        "state":        fac["state"],
                        "dist_nm":      round(dist_nm, 1),
                        "fac_lon":      fac["lon"],
                        "fac_lat":      fac["lat"],
                        "orbital_debris": []
                    }

                    # ── Orbital debris cross-reference ──────────────────
                    # If closure is tagged SPACE_DEBRIS, find debris objects
                    # from the same nation in the GEO catalog
                    if geo_catalog and 'SPACE_DEBRIS' in tags.upper():
                        # Try to identify nation from closure description
                        desc = entity.get("description", "")
                        country_matches = get_spadoc_from_source(
                            name + " " + source + " " + desc[:500], box_score
                        )
                        for spadoc, country_name in country_matches[:3]:
                            debris_objs = get_debris_for_country(spadoc, geo_catalog)
                            for deb in debris_objs[:10]:  # cap at 10 per country
                                alert["orbital_debris"].append({
                                    "norad_id":   deb.get("NORAD_CAT_ID", ""),
                                    "satname":    deb.get("SATNAME", ""),
                                    "country":    deb.get("COUNTRY", ""),
                                    "spadoc":     spadoc,
                                    "launch":     deb.get("LAUNCH", ""),
                                    "longitude":  deb.get("LONGITUDE_EST"),
                                    "inclination":deb.get("INCLINATION", ""),
                                    "apogee":     deb.get("APOGEE", ""),
                                    "perigee":    deb.get("PERIGEE", ""),
                                })

                    alerts.append(alert)
            except Exception:
                continue

    # Sort by distance
    alerts.sort(key=lambda a: a["dist_nm"])

    print(f"\nFound {len(alerts)} proximity alerts within {radius_nm} nm")

    # Write JSON
    with open(JSON_OUT, "w", encoding="utf-8") as f:
        json.dump({"generated": run_time, "radius_nm": radius_nm,
                   "alerts": alerts}, f, indent=2)
    print(f"JSON: {JSON_OUT}")

    # Write HTML
    write_html(alerts, radius_nm, run_time)
    print(f"HTML: {HTML_OUT}")

    # Print top 20
    print(f"\nTop alerts (closest first):")
    for a in alerts[:20]:
        print(f"  {a['dist_nm']:5.1f} nm | {a['facility'][:30]:<30} | {a['closure_name'][:40]}")

    return alerts


def write_html(alerts, radius_nm, run_time):
    def dist_color(nm):
        if nm < 10:  return "#ff4444"
        if nm < 25:  return "#ff8800"
        if nm < 50:  return "#ffcc00"
        return "#44ff88"

    rows = ""
    debris_section_rows = ""
    for a in alerts:
        color = dist_color(a["dist_nm"])
        tags_html = ""
        if a["tags"]:
            tags_html = " ".join(
                f'<span style="background:#1a3a5c;border:1px solid #0af;border-radius:3px;'
                f'padding:1px 4px;font-size:0.8em;color:#0af">{t}</span>'
                for t in a["tags"].split(",") if t.strip()
            )
        # Orbital debris badge
        deb_count = len(a.get("orbital_debris", []))
        deb_badge = (f'<span style="background:#3a0000;border:1px solid #f44;border-radius:3px;'
                     f'padding:1px 4px;font-size:0.8em;color:#f44">'
                     f'&#9762; {deb_count} DEB</span>') if deb_count else ''

        rows += f"""<tr>
            <td style="color:{color};font-weight:bold;padding:4px 8px">{a['dist_nm']} nm</td>
            <td style="padding:4px 8px;color:#cde">{a['facility']}</td>
            <td style="padding:4px 8px;color:#7ab">{a['component']}</td>
            <td style="padding:4px 8px;color:#cde">{a['closure_name']}</td>
            <td style="padding:4px 8px;color:#7ab">{a['source']}</td>
            <td style="padding:4px 8px">{tags_html} {deb_badge}</td>
            <td style="padding:4px 8px;color:#7ab">{a['start']}</td>
        </tr>"""

        # Build debris detail rows
        for deb in a.get("orbital_debris", []):
            lon_str = f"{deb['longitude']:.1f}°E" if deb.get('longitude') is not None else "unknown"
            debris_section_rows += f"""<tr>
                <td style="color:#f44;padding:3px 8px">&#9762; DEB</td>
                <td style="padding:3px 8px;color:#f88">{deb.get('satname','')}</td>
                <td style="padding:3px 8px;color:#7ab">{deb.get('country','')} / {deb.get('spadoc','')}</td>
                <td style="padding:3px 8px;color:#cde">{a['closure_name'][:40]}</td>
                <td style="padding:3px 8px;color:#7ab">NORAD {deb.get('norad_id','')}</td>
                <td style="padding:3px 8px;color:#7ab">Lon: {lon_str} | Inc: {deb.get('inclination','')}°</td>
                <td style="padding:3px 8px;color:#7ab">{deb.get('launch','')}</td>
            </tr>"""

    if not rows:
        rows = f'<tr><td colspan="7" style="color:#7ab;padding:12px">No alerts within {radius_nm} nm.</td></tr>'

    debris_section = ""
    if debris_section_rows:
        debris_section = f"""
<h2 style="color:#f44;font-size:0.95em;margin-top:24px;border-top:1px solid #3a0000;padding-top:12px">
  &#9762; Orbital Debris Cross-Reference (SPACE_DEBRIS tagged closures)
</h2>
<table>
  <thead><tr>
    <th>Type</th><th>Object</th><th>Nation / SPADOC</th>
    <th>Associated Closure</th><th>NORAD ID</th><th>Orbital Params</th><th>Launch</th>
  </tr></thead>
  <tbody>{debris_section_rows}</tbody>
</table>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Sky-Net (Lite) Proximity Alerts — {run_time}</title>
<style>
  body {{ background:#0a0e16; color:#cde; font-family:'Segoe UI',sans-serif; padding:20px; }}
  h1 {{ color:#0af; font-size:1.1em; border-bottom:1px solid #1a3a5c; padding-bottom:8px; }}
  .summary {{ display:flex; gap:20px; margin:12px 0; }}
  .stat {{ background:#0d1a2e; border:1px solid #1a3a5c; border-radius:6px; padding:10px 18px; text-align:center; }}
  .stat .val {{ font-size:1.8em; font-weight:bold; color:#f80; }}
  .stat .lbl {{ font-size:0.7em; color:#7ab; text-transform:uppercase; }}
  table {{ width:100%; border-collapse:collapse; margin-top:12px; font-size:0.82em; }}
  th {{ color:#7ab; font-size:0.75em; text-transform:uppercase; padding:4px 8px; text-align:left;
        border-bottom:1px solid #1a3a5c; }}
  tr:nth-child(even) {{ background:rgba(255,255,255,0.03); }}
  .legend {{ display:flex; gap:12px; margin:8px 0; font-size:0.75em; }}
  .leg {{ padding:2px 8px; border-radius:3px; }}
</style>
</head>
<body>
<h1>&#9888; Sky-Net (Lite) Proximity Alerts &mdash; {radius_nm} nm radius &mdash; {run_time}</h1>
<div class="summary">
  <div class="stat"><div class="val">{len(alerts)}</div><div class="lbl">Total Alerts</div></div>
  <div class="stat"><div class="val">{sum(1 for a in alerts if a['dist_nm'] < 10)}</div><div class="lbl">&lt;10 nm</div></div>
  <div class="stat"><div class="val">{sum(1 for a in alerts if a['dist_nm'] < 25)}</div><div class="lbl">&lt;25 nm</div></div>
  <div class="stat"><div class="val">{len(set(a['facility'] for a in alerts))}</div><div class="lbl">Facilities Affected</div></div>
</div>
<div class="legend">
  <span class="leg" style="background:#ff4444;color:#000">&lt;10 nm CRITICAL</span>
  <span class="leg" style="background:#ff8800;color:#000">&lt;25 nm HIGH</span>
  <span class="leg" style="background:#ffcc00;color:#000">&lt;50 nm MEDIUM</span>
  <span class="leg" style="background:#44ff88;color:#000">50+ nm LOW</span>
</div>
<table>
  <thead><tr>
    <th>Distance</th><th>Facility</th><th>Component</th>
    <th>Closure</th><th>Source</th><th>Intel Tags</th><th>Issue Date</th>
  </tr></thead>
  <tbody>{rows}</tbody>
</table>
{debris_section}
</body>
</html>"""

    with open(HTML_OUT, "w", encoding="utf-8") as f:
        f.write(html)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sky-Net (Lite) Proximity Alert")
    parser.add_argument("--radius-nm", type=float, default=50,
                        help="Alert radius in nautical miles (default: 50)")
    args = parser.parse_args()
    run(radius_nm=args.radius_nm)
