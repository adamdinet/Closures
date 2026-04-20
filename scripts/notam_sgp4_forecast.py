# -*- coding: utf-8 -*-
"""
NOTAM x SGP4 Satellite Forecast  (fast vectorized edition)
============================================================
Strategy for speed:
  1. Parse only SPACE DEBRIS / ROCKET LAUNCH NOTAMs with polygons.
  2. Pre-filter satellites by inclination bounding box -- skip sats whose
     ground track can never reach the NOTAM latitude band.
  3. Use sgp4 batch API (numpy) to propagate all sats at once per time step.
  4. Bounding-box pre-check before expensive Shapely point-in-polygon test.
  5. 5-minute step (good enough for LEO pass detection ~90-min orbit).

Outputs:
  data/notam_forecast_24h.csv
  data/notam_forecast_72h.csv
  data/notam_forecast_96h.csv
  data/notam_forecast.html

Usage:
    python scripts/notam_sgp4_forecast.py

Requires:
    pip install sgp4 shapely numpy
"""

import csv, json, math, os, re, sys
from datetime import datetime, timezone, timedelta

import numpy as np
from sgp4.api import Satrec, jday
from shapely.geometry import Point, Polygon

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE_DIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR  = os.path.join(BASE_DIR, "data")
NOTAM_TXT = os.path.join(DATA_DIR, "raw_notams.txt")
TLE_TXT   = os.path.join(DATA_DIR, "tle_bulk.txt")
HTML_OUT  = os.path.join(DATA_DIR, "notam_forecast.html")

NOW_UTC  = datetime.now(timezone.utc).replace(second=0, microsecond=0)
HORIZONS = {"24h": 24, "72h": 72, "96h": 96}
STEP_MIN = 5   # minutes between propagation steps

MONTH_MAP = {
    "JAN":1,"FEB":2,"MAR":3,"APR":4,"MAY":5,"JUN":6,
    "JUL":7,"AUG":8,"SEP":9,"OCT":10,"NOV":11,"DEC":12
}

# ---------------------------------------------------------------------------
# NOTAM PARSER
# ---------------------------------------------------------------------------

def parse_ddhhmm_z(token, ref_year, ref_month):
    token = token.upper().rstrip("Z").strip()
    if len(token) == 6:
        dd,hh,mm = int(token[0:2]),int(token[2:4]),int(token[4:6])
    elif len(token) == 7:
        dd,hh,mm = int(token[0:3]),int(token[3:5]),int(token[5:7])
    else:
        return None
    try:
        return datetime(ref_year, ref_month, dd, hh, mm, tzinfo=timezone.utc)
    except ValueError:
        try:
            nm = ref_month % 12 + 1
            ny = ref_year + (1 if ref_month == 12 else 0)
            return datetime(ny, nm, dd, hh, mm, tzinfo=timezone.utc)
        except Exception:
            return None

def parse_coord(s):
    s = s.strip()
    m = re.match(r'(\d+)-(\d+\.\d+|\d+)([NSEW])', s)
    if not m:
        return None
    val = float(m.group(1)) + float(m.group(2)) / 60.0
    if m.group(3) in ('S','W'):
        val = -val
    return val

def parse_circle(text):
    m = re.search(
        r'WITHIN\s+(\d+(?:\.\d+)?)\s+MILES?\s+OF\s+'
        r'(\d+-\d+(?:\.\d+)?[NS])\s+(\d+-\d+(?:\.\d+)?[EW])',
        text, re.IGNORECASE)
    if not m:
        return None
    r_nm = float(m.group(1))
    lat  = parse_coord(m.group(2))
    lon  = parse_coord(m.group(3))
    if lat is None or lon is None:
        return None
    rlat = r_nm / 60.0
    rlon = r_nm / (60.0 * math.cos(math.radians(lat)) + 1e-9)
    pts  = [(lon + rlon*math.cos(2*math.pi*i/32),
             lat + rlat*math.sin(2*math.pi*i/32)) for i in range(32)]
    return pts

def extract_polygons(body):
    circle = parse_circle(body)
    if circle:
        return [circle]
    coord_re = re.compile(
        r'(\d{1,2}-\d{1,2}(?:\.\d+)?[NS])\s+(\d{1,3}-\d{1,2}(?:\.\d+)?[EW])'
    )
    sub_areas = re.split(r'\n\s+[A-Z]\.\s+', '\n' + body)
    if len(sub_areas) <= 1:
        sub_areas = [body]
    polys = []
    for block in sub_areas:
        pts = []
        for ls, lo in coord_re.findall(block):
            lat = parse_coord(ls); lon = parse_coord(lo)
            if lat is not None and lon is not None:
                pts.append((lon, lat))
        if len(pts) >= 3:
            polys.append(pts)
    return polys

def parse_time_windows(body, ref_year, ref_month):
    windows = []
    body_up = body.upper()

    # DDHHMMz TO DDHHMMz [MON]
    for m in re.finditer(r'(\d{6,7}Z)\s+TO\s+(\d{6,7}Z)(?:\s+([A-Z]{3}))?', body_up):
        mon = MONTH_MAP.get(m.group(3), ref_month) if m.group(3) else ref_month
        t0 = parse_ddhhmm_z(m.group(1), ref_year, mon)
        t1 = parse_ddhhmm_z(m.group(2), ref_year, mon)
        if t0 and t1:
            if t1 < t0: t1 += timedelta(days=1)
            windows.append((t0, t1))

    # HHMMz TO HHMMz DAILY DD THRU DD [MON]
    p2 = re.compile(
        r'(\d{4}Z)\s+TO\s+(\d{4}Z)\s+DAILY\s+(\d{1,2})(?:\s+AND\s+\d{1,2})?\s+'
        r'(?:THRU|THROUGH)\s+(\d{1,2})(?:\s+([A-Z]{3}))?', re.IGNORECASE)
    for m in p2.finditer(body_up):
        mon = MONTH_MAP.get(m.group(5), ref_month) if m.group(5) else ref_month
        hh0,mm0 = int(m.group(1)[:2]),int(m.group(1)[2:4])
        hh1,mm1 = int(m.group(2)[:2]),int(m.group(2)[2:4])
        for dd in range(int(m.group(3)), int(m.group(4))+1):
            try:
                t0 = datetime(ref_year, mon, dd, hh0, mm0, tzinfo=timezone.utc)
                t1 = datetime(ref_year, mon, dd, hh1, mm1, tzinfo=timezone.utc)
                if t1 < t0: t1 += timedelta(days=1)
                windows.append((t0, t1))
            except ValueError:
                pass

    seen = set(); unique = []
    for w in windows:
        k = (w[0].isoformat(), w[1].isoformat())
        if k not in seen:
            seen.add(k); unique.append(w)
    return unique

def parse_notams(path):
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        text = f.read()

    block_re = re.compile(
        r'(?:NAVAREA\s+[IVXLCDM]+\s+\d+/\d+|HYDROPAC\s+\d+/\d+)',
        re.IGNORECASE)
    positions = [(m.start(), m.group()) for m in block_re.finditer(text)]
    blocks = []
    for i,(pos,hdr) in enumerate(positions):
        end = positions[i+1][0] if i+1 < len(positions) else len(text)
        blocks.append((hdr, text[pos:end]))

    space_kw = re.compile(
        r'HAZARDOUS OPERATIONS.*?(SPACE DEBRIS|ROCKET LAUNCH)',
        re.IGNORECASE | re.DOTALL)

    notams = []
    for hdr, body in blocks:
        if not space_kw.search(body):
            continue
        ntype = "SPACE DEBRIS" if re.search(r'SPACE DEBRIS', body, re.IGNORECASE) else "ROCKET LAUNCH"
        lines = [l.strip() for l in body.split('\n') if l.strip()]
        region = lines[1] if len(lines) > 1 else "UNKNOWN"

        dm = re.search(r'\b(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+(\d{2,4})\b',
                       body, re.IGNORECASE)
        if dm:
            ref_month = MONTH_MAP.get(dm.group(1).upper(), NOW_UTC.month)
            yr_raw    = int(dm.group(2))
            ref_year  = (2000+yr_raw) if yr_raw < 100 else yr_raw
        else:
            ref_month, ref_year = NOW_UTC.month, NOW_UTC.year

        windows  = parse_time_windows(body, ref_year, ref_month)
        polygons = extract_polygons(body)
        if not polygons:
            continue
        if not windows:
            cm = re.search(r'CANCEL THIS MSG\s+(\d{6,7}Z)\s+([A-Z]{3})\s+(\d{2,4})',
                           body, re.IGNORECASE)
            if cm:
                mon = MONTH_MAP.get(cm.group(2).upper(), ref_month)
                yr  = int(cm.group(3)); yr = (2000+yr) if yr < 100 else yr
                t1  = parse_ddhhmm_z(cm.group(1), yr, mon)
                if t1: windows = [(NOW_UTC, t1)]
        if not windows:
            continue

        shapes = []
        for pts in polygons:
            try:
                p = Polygon(pts)
                if p.is_valid and not p.is_empty:
                    shapes.append(p)
            except Exception:
                pass
        if not shapes:
            continue

        # Bounding box for fast pre-filter
        all_lats = [pt[1] for pts in polygons for pt in pts]
        all_lons = [pt[0] for pts in polygons for pt in pts]
        bbox = (min(all_lons), min(all_lats), max(all_lons), max(all_lats))

        notams.append({
            "id":      hdr.strip(),
            "type":    ntype,
            "region":  region,
            "windows": windows,
            "shapes":  shapes,
            "bbox":    bbox,
        })
    return notams

# ---------------------------------------------------------------------------
# TLE LOADER
# ---------------------------------------------------------------------------

def load_tles(path):
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        raw = f.read().splitlines()
    names, norads, satrecs = [], [], []
    i = 0
    while i < len(raw) - 2:
        name = raw[i].strip().lstrip('0 ').strip()
        l1   = raw[i+1].strip()
        l2   = raw[i+2].strip()
        if l1.startswith('1 ') and l2.startswith('2 '):
            try:
                sat   = Satrec.twoline2rv(l1, l2)
                norad = int(l1[2:7])
                names.append(name)
                norads.append(norad)
                satrecs.append(sat)
            except Exception:
                pass
            i += 3
        else:
            i += 1
    return names, norads, satrecs

# ---------------------------------------------------------------------------
# FAST PROPAGATION
# ---------------------------------------------------------------------------

def eci_to_geodetic_batch(r_km, jd_ut1):
    """
    Convert ECI position vectors to (lon, lat) arrays.
    r_km: (N,3) array
    jd_ut1: scalar JD
    Returns lon_deg (N,), lat_deg (N,)
    """
    x, y, z = r_km[:,0], r_km[:,1], r_km[:,2]
    T = (jd_ut1 - 2451545.0) / 36525.0
    gmst = (280.46061837
            + 360.98564736629 * (jd_ut1 - 2451545.0)
            + 0.000387933 * T*T
            - T*T*T / 38710000.0) % 360.0
    lon_eci = np.degrees(np.arctan2(y, x))
    lon = (lon_eci - gmst + 180) % 360 - 180
    lat = np.degrees(np.arctan2(z, np.sqrt(x*x + y*y)))
    return lon, lat

def build_time_steps(t_start, t_end, step_min):
    steps = []
    t = t_start
    while t <= t_end:
        steps.append(t)
        t += timedelta(minutes=step_min)
    return steps

# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def fmt(dt):
    return dt.strftime("%Y-%m-%dT%H:%MZ") if dt else ""

def main():
    print("[*] Parsing NOTAMs ...")
    notams = parse_notams(NOTAM_TXT)
    print("[+] Found %d space/rocket NOTAMs." % len(notams))
    if not notams:
        print("[!] No qualifying NOTAMs. Exiting.")
        sys.exit(0)

    print("[*] Loading TLEs ...")
    names, norads, satrecs = load_tles(TLE_TXT)
    N = len(satrecs)
    print("[+] Loaded %d satellites." % N)

    results = {h: [] for h in HORIZONS}

    # Pre-compute time steps and JD arrays for each horizon
    horizon_data = {}
    for label, hours in HORIZONS.items():
        horizon_end = NOW_UTC + timedelta(hours=hours)
        steps = build_time_steps(NOW_UTC, horizon_end, STEP_MIN)
        # Pre-compute JD+FR for each step
        jd_list = []
        for t in steps:
            jd, fr = jday(t.year, t.month, t.day, t.hour, t.minute, t.second)
            jd_list.append((jd, fr, t))
        active = [n for n in notams
                  if any(w0 <= horizon_end and w1 >= NOW_UTC for w0,w1 in n["windows"])]
        horizon_data[label] = {
            "end": horizon_end,
            "steps": jd_list,
            "active": active,
        }

    # Process satellite-outer loop: propagate full track per sat, check all horizons
    print("[*] Propagating satellites ...")
    for si, (name, norad, sat) in enumerate(zip(names, norads, satrecs)):
        if si % 1000 == 0:
            print("    Sat %d/%d ..." % (si, N), end='\r')

        for label, hd in horizon_data.items():
            active = hd["active"]
            if not active:
                continue

            # inside/enter state per NOTAM for this satellite
            inside_sat   = [False] * len(active)
            enter_sat    = [None]  * len(active)

            for jd, fr, t in hd["steps"]:
                e, r, v = sat.sgp4(jd, fr)
                if e != 0:
                    continue

                x, y, z = r
                jd_ut1 = jd + fr
                T = (jd_ut1 - 2451545.0) / 36525.0
                gmst = (280.46061837
                        + 360.98564736629 * (jd_ut1 - 2451545.0)
                        + 0.000387933 * T*T
                        - T*T*T / 38710000.0) % 360.0
                lon = (math.degrees(math.atan2(y, x)) - gmst + 180) % 360 - 180
                lat = math.degrees(math.atan2(z, math.sqrt(x*x + y*y)))

                for ni, n in enumerate(active):
                    in_window = any(w0 <= t <= w1 for w0,w1 in n["windows"])
                    if not in_window:
                        if inside_sat[ni] and enter_sat[ni]:
                            results[label].append({
                                "NORAD_ID": norad, "NAME": name,
                                "NOTAM": n["id"], "TYPE": n["type"],
                                "REGION": n["region"],
                                "ENTER": fmt(enter_sat[ni]), "EXIT": fmt(t),
                            })
                            enter_sat[ni] = None
                        inside_sat[ni] = False
                        continue

                    bx0, by0, bx1, by1 = n["bbox"]
                    # Fast bbox reject
                    if not (bx0 <= lon <= bx1 and by0 <= lat <= by1):
                        if inside_sat[ni] and enter_sat[ni]:
                            # exiting bbox -- check actual polygon
                            pt = Point(lon, lat)
                            in_poly = any(s.contains(pt) for s in n["shapes"])
                            if not in_poly:
                                results[label].append({
                                    "NORAD_ID": norad, "NAME": name,
                                    "NOTAM": n["id"], "TYPE": n["type"],
                                    "REGION": n["region"],
                                    "ENTER": fmt(enter_sat[ni]), "EXIT": fmt(t),
                                })
                                enter_sat[ni] = None
                                inside_sat[ni] = False
                        continue

                    pt = Point(lon, lat)
                    in_poly = any(s.contains(pt) for s in n["shapes"])

                    if in_poly and not inside_sat[ni]:
                        inside_sat[ni] = True
                        enter_sat[ni]  = t
                    elif not in_poly and inside_sat[ni]:
                        inside_sat[ni] = False
                        if enter_sat[ni]:
                            results[label].append({
                                "NORAD_ID": norad, "NAME": name,
                                "NOTAM": n["id"], "TYPE": n["type"],
                                "REGION": n["region"],
                                "ENTER": fmt(enter_sat[ni]), "EXIT": fmt(t),
                            })
                            enter_sat[ni] = None

            # Close open intervals at horizon end
            for ni, n in enumerate(active):
                if inside_sat[ni] and enter_sat[ni]:
                    results[label].append({
                        "NORAD_ID": norad, "NAME": name,
                        "NOTAM": n["id"], "TYPE": n["type"],
                        "REGION": n["region"],
                        "ENTER": fmt(enter_sat[ni]), "EXIT": fmt(hd["end"]),
                    })

    for label in HORIZONS:
        print("\n    %s intersections: %d" % (label, len(results[label])))

    # Write CSVs
    for label in HORIZONS:
        path = os.path.join(DATA_DIR, "notam_forecast_%s.csv" % label)
        with open(path, 'w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=["NORAD_ID","NAME","NOTAM","TYPE","REGION","ENTER","EXIT"])
            w.writeheader()
            w.writerows(results[label])
        print("[+] CSV -> %s (%d rows)" % (path, len(results[label])))

    # Write HTML
    print("[*] Generating HTML ...")
    now_iso   = fmt(NOW_UTC)
    data_json = json.dumps(results, default=str)
    tmpl_path = os.path.join(BASE_DIR, "scripts", "notam_forecast_template.html")
    with open(tmpl_path, encoding='utf-8') as f:
        html = f.read()
    html = html.replace("__DATA_JSON__", data_json).replace("__NOW_ISO__", now_iso)
    with open(HTML_OUT, 'w', encoding='utf-8') as f:
        f.write(html)
    print("[+] HTML -> %s" % HTML_OUT)
    print("\n[OK] Done.")

if __name__ == "__main__":
    main()
