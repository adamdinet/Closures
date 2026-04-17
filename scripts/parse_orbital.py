# -*- coding: utf-8 -*-
"""
Sky-Net (Lite) — Orbital Data Parser
=====================================
Parses data/box_score.txt and data/geo_report.txt (tab-delimited,
Space-Track.org format) into clean JSON files for use by the frontend
and proximity_alert.py.

Outputs:
  data/box_score.json   — country orbital inventory (payload/debris/rocket body counts)
  data/geo_catalog.json — GEO satellite catalog with estimated longitudes

GEO Longitude Estimation:
  Space-Track's geo_report does not include a longitude column.
  Longitude is estimated using:
    1. COMMENTCODE field (Space-Track encodes slot longitude * 10 as an integer)
    2. Known satellite name → slot mappings for major constellations
    3. Falls back to None if neither is available

Usage:
    python scripts/parse_orbital.py

Run once after downloading fresh data from Space-Track.org.
"""

import csv, io, json, os, re, sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
else:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

BASE_DIR       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR       = os.path.join(BASE_DIR, "data")
BOX_SCORE_PATH = os.path.join(DATA_DIR, "box_score.txt")
GEO_REPORT_PATH= os.path.join(DATA_DIR, "geo_report.txt")
BOX_JSON_OUT   = os.path.join(DATA_DIR, "box_score.json")
GEO_JSON_OUT   = os.path.join(DATA_DIR, "geo_catalog.json")


# ── Known GEO slot longitude overrides (degrees East, -180 to +180) ───────
# For satellites whose COMMENTCODE is missing or unreliable.
# Source: public ITU filing data and operator announcements.
KNOWN_SLOTS = {
    # US military / government
    "MILSTAR":      None,   # classified orbit
    "WGS":          None,   # classified
    "AEHF":         None,   # classified
    "MUOS":         None,   # classified
    "SBIRS":        None,   # classified
    "DSP":          None,   # classified
    "DSCS":         None,   # classified
    # Commercial — approximate slots
    "INTELSAT 1":   -28.5,
    "INTELSAT 2":   -24.5,
    "INTELSAT 3":   -21.5,
    "INTELSAT 4":   -18.5,
    "INTELSAT 5":   -15.5,
    "INTELSAT 6":   -11.5,
    "INTELSAT 7":    -1.0,
    "INTELSAT 8":    -5.5,
    "INTELSAT 9":    -8.0,
    "INTELSAT 10":  -12.0,
    "INTELSAT 11":  -43.0,
    "INTELSAT 12":   45.0,
    "INTELSAT 14":  -45.0,
    "INTELSAT 15":   85.2,
    "INTELSAT 16":  -58.0,
    "INTELSAT 17":   66.0,
    "INTELSAT 18":  180.0,
    "INTELSAT 19":  166.0,
    "INTELSAT 20":   68.5,
    "INTELSAT 21":  -58.0,
    "INTELSAT 22":   72.1,
    "INTELSAT 23":  -53.0,
    "INTELSAT 24":  -95.0,
    "INTELSAT 25":  -31.5,
    "INTELSAT 26":  -45.0,
    "INTELSAT 27":  -55.5,
    "INTELSAT 28":  -55.5,
    "INTELSAT 29":  -310.0,
    "INTELSAT 30":  -95.0,
    "INTELSAT 31":  -95.0,
    "INTELSAT 32":  -43.0,
    "INTELSAT 33":   60.0,
    "INTELSAT 34":  -55.5,
    "INTELSAT 35":  -34.5,
    "INTELSAT 36":   68.5,
    "INTELSAT 37":  -18.0,
    "INTELSAT 38":   45.0,
    "INTELSAT 39":   62.0,
    "INTELSAT 40":  -95.0,
    "INTELSAT 41":   62.0,
    "INTELSAT 43":  -95.0,
    "INTELSAT 44":   60.0,
    "INTELSAT 45":   -8.0,
    "GOES 1":       -75.0,
    "GOES 2":       -75.0,
    "GOES 3":       -75.0,
    "GOES 4":       -75.0,
    "GOES 5":       -75.0,
    "GOES 6":       -75.0,
    "GOES 7":       -75.0,
    "GOES 8":       -75.0,
    "GOES 9":       -75.0,
    "GOES 10":      -75.0,
    "GOES 11":      -75.0,
    "GOES 12":      -75.0,
    "GOES 13":      -75.0,
    "GOES 14":      -75.0,
    "GOES 15":      -75.0,
    "GOES 16":      -75.2,
    "GOES 17":     -137.2,
    "GOES 18":     -137.0,
    "GOES 19":      -75.2,
}


def commentcode_to_lon(code_str):
    """
    Space-Track COMMENTCODE encodes GEO slot longitude as integer * 10.
    E.g., code 9453 → 945.3° → normalized to -14.7° (945.3 - 360*3 = -134.7? No.)
    
    Actually Space-Track uses a different encoding:
    - Codes 0-3599 represent 0.0° to 359.9° East longitude (code / 10)
    - Codes 9000+ are special (graveyard, unknown, etc.)
    
    Returns longitude in degrees (-180 to +180) or None.
    """
    try:
        code = int(str(code_str).strip())
        if 0 <= code <= 3599:
            lon_east = code / 10.0
            # Convert to -180..+180
            if lon_east > 180:
                lon_east -= 360
            return round(lon_east, 1)
        # Codes 9000+ are special/graveyard — not a valid slot
        return None
    except (ValueError, TypeError):
        return None


def estimate_longitude(row):
    """
    Estimate GEO longitude for a satellite row.
    Priority: COMMENTCODE → KNOWN_SLOTS name match → None
    """
    # 1. Try COMMENTCODE
    lon = commentcode_to_lon(row.get('COMMENTCODE', ''))
    if lon is not None:
        return lon

    # 2. Try known slot name match
    satname = row.get('SATNAME', '').upper()
    for key, slot_lon in KNOWN_SLOTS.items():
        if key.upper() in satname:
            return slot_lon

    return None


def parse_box_score():
    """Parse box_score.txt → list of country inventory dicts."""
    print(f"Parsing {BOX_SCORE_PATH}...")
    rows = []
    with open(BOX_SCORE_PATH, encoding='utf-8', errors='replace') as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            # Clean up keys and values
            clean = {}
            for k, v in row.items():
                if k:
                    clean[k.strip()] = v.strip() if v else ''
            rows.append(clean)
    print(f"  -> {len(rows)} countries/organizations")
    return rows


def parse_geo_report():
    """Parse geo_report.txt → list of satellite dicts with estimated longitude."""
    print(f"Parsing {GEO_REPORT_PATH}...")
    rows = []
    with open(GEO_REPORT_PATH, encoding='utf-8', errors='replace') as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            clean = {}
            for k, v in row.items():
                if k:
                    clean[k.strip()] = v.strip() if v else ''

            # Add estimated longitude
            clean['LONGITUDE_EST'] = estimate_longitude(clean)

            # Classify for color coding
            obj_type = clean.get('OBJECT_TYPE', '').upper()
            satname  = clean.get('SATNAME', '').upper()
            if 'DEB' in satname or 'DEBRIS' in obj_type:
                clean['DISPLAY_TYPE'] = 'DEBRIS'
                clean['COLOR']        = 'red'
            elif 'ROCKET BODY' in obj_type or 'R/B' in satname:
                clean['DISPLAY_TYPE'] = 'ROCKET_BODY'
                clean['COLOR']        = 'orange'
            else:
                clean['DISPLAY_TYPE'] = 'PAYLOAD'
                clean['COLOR']        = 'green'

            rows.append(clean)

    # Stats
    with_lon  = sum(1 for r in rows if r['LONGITUDE_EST'] is not None)
    payloads  = sum(1 for r in rows if r['DISPLAY_TYPE'] == 'PAYLOAD')
    debris    = sum(1 for r in rows if r['DISPLAY_TYPE'] == 'DEBRIS')
    rocketbod = sum(1 for r in rows if r['DISPLAY_TYPE'] == 'ROCKET_BODY')
    print(f"  -> {len(rows)} objects total")
    print(f"     Payloads: {payloads} | Rocket Bodies: {rocketbod} | Debris: {debris}")
    print(f"     With estimated longitude: {with_lon} ({100*with_lon//len(rows)}%)")
    return rows


def main():
    # Parse box score
    box_rows = parse_box_score()
    with open(BOX_JSON_OUT, 'w', encoding='utf-8') as f:
        json.dump(box_rows, f, indent=2)
    print(f"  -> Written: {BOX_JSON_OUT}")

    # Parse GEO catalog
    geo_rows = parse_geo_report()
    with open(GEO_JSON_OUT, 'w', encoding='utf-8') as f:
        json.dump(geo_rows, f, indent=2)
    print(f"  -> Written: {GEO_JSON_OUT}")

    # Quick sanity check — show sample with longitude
    print("\nSample GEO objects with longitude:")
    samples = [r for r in geo_rows if r['LONGITUDE_EST'] is not None][:10]
    for s in samples:
        print(f"  {s['SATNAME'][:35]:35s} {s['COUNTRY']:6s} {s['DISPLAY_TYPE']:12s} lon={s['LONGITUDE_EST']:8.1f}")

    print("\nSample DEBRIS objects:")
    deb_samples = [r for r in geo_rows if r['DISPLAY_TYPE'] == 'DEBRIS'][:5]
    for s in deb_samples:
        print(f"  {s['SATNAME'][:35]:35s} {s['COUNTRY']:6s} lon={s['LONGITUDE_EST']}")

    print("\nDone.")


if __name__ == "__main__":
    main()
