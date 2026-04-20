# -*- coding: utf-8 -*-
"""
Sky-Net (Lite) NOTAM/Warning Ingest Script
===========================================
Sources:
  1. NGA MSI Broadcast Warnings (NAVAREA maritime warnings) - public, no key
  2. FAA NOTAM Search API (aeronautical NOTAMs) - public, no key
  3. USCG Local Notice to Mariners (LNM) - public RSS/JSON feed
  4. ICAO NOTAMam via FAA NOTAM API (international aeronautical)
  5. NGA MSI Hydrolant/Hydropac/Navtex text warnings - public

All sources are merged, deduplicated, and written to data/closures.czml
"""

import io
import json
import math
import os
import re
import sys
import time
import urllib3
from datetime import datetime, timezone, timedelta

# Force UTF-8 output so Unicode characters print correctly on Windows cp1252 terminals
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
else:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

try:
    import requests
except ImportError:
    print("ERROR: 'requests' library not found. Run: pip install requests")
    sys.exit(1)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
OUT_PATH = os.path.join(DATA_DIR, "closures.czml")
RAW_PATH = os.path.join(DATA_DIR, "raw_notams.txt")

# ─────────────────────────────────────────────
# SOURCE ENDPOINTS
# ─────────────────────────────────────────────
NGA_BROADCAST_URL   = "https://msi.nga.mil/api/publications/broadcast-warn"

# NGA MSI publishes HYDROLANT, HYDROPAC, and NAVTEX as plain-text files
# These are the actual downloadable .txt files from the NGA MSI website
NGA_TEXT_FILES = {
    "HYDROLANT": "https://msi.nga.mil/api/publications/download?type=view&key=16694622/SFH00000/Hydrolant.txt",
    "HYDROPAC":  "https://msi.nga.mil/api/publications/download?type=view&key=16694622/SFH00000/Hydropac.txt",
    "NAVTEX":    "https://msi.nga.mil/api/publications/download?type=view&key=16694622/SFH00000/Navtex.txt",
    # NAVAREA XII in-force (same format as raw_notams.txt)
    "NAVAREA_XII": "https://msi.nga.mil/api/publications/download?type=view&key=16694622/SFH00000/NavArea_XII.txt",
    "NAVAREA_IV":  "https://msi.nga.mil/api/publications/download?type=view&key=16694622/SFH00000/NavArea_IV.txt",
}

# FAA NOTAM API v1 — requires registered client_id/secret
FAA_NOTAM_URL       = "https://external-api.faa.gov/notamapi/v1/notams"
# ICAO NOTAMs via AIM FAA search (public, no key)
FAA_AIM_URL         = "https://notams.aim.faa.gov/notamSearch/search"

# USCG NavCen — multiple endpoint attempts
USCG_LNM_URLS = [
    "https://www.navcen.uscg.gov/json/lnmSummary",
    "https://www.navcen.uscg.gov/json/lnmSummary/getAll",
    "https://www.navcen.uscg.gov/LNM/district/all",
    "https://www.navcen.uscg.gov/?pageName=lnmMain",   # HTML fallback
]
USCG_BNM_RSS        = "https://www.navcen.uscg.gov/rss/bnm.xml"

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
MONTH_MAP = {
    "JAN":"01","FEB":"02","MAR":"03","APR":"04","MAY":"05","JUN":"06",
    "JUL":"07","AUG":"08","SEP":"09","OCT":"10","NOV":"11","DEC":"12"
}

def dm_to_dd(coord_str):
    """Convert degrees-minutes (e.g. 46-31.23N) to decimal degrees."""
    try:
        m = re.match(r"(\d{1,3})-(\d{1,2}(?:\.\d+)?)([NSWEnswe])", coord_str.strip())
        if not m:
            return None
        deg = float(m.group(1))
        mins = float(m.group(2))
        hemi = m.group(3).upper()
        dd = deg + (mins / 60.0)
        if hemi in ('S', 'W'):
            dd = -dd
        return dd
    except Exception:
        return None

def dd_to_dd(coord_str):
    """Parse plain decimal degree strings like '34.5N' or '-117.3'."""
    try:
        m = re.match(r"(-?\d+\.?\d*)([NSWEnswe]?)", coord_str.strip())
        if not m:
            return None
        val = float(m.group(1))
        hemi = m.group(2).upper()
        if hemi in ('S', 'W'):
            val = -val
        return val
    except Exception:
        return None

def parse_date_flexible(date_str):
    """
    Try multiple date formats and return ISO-8601 UTC string.
    Returns None if unparseable.
    """
    if not date_str:
        return None
    date_str = str(date_str).strip()

    # Format: "181536Z Mar 26" or "100102Z APR 26"
    m = re.match(r"(\d{2})\d{4}Z\s+([A-Za-z]{3})\s+(\d{2,4})", date_str)
    if m:
        day = m.group(1)
        mon = MONTH_MAP.get(m.group(2).upper())
        yr  = m.group(3)
        if mon:
            yr = yr if len(yr) == 4 else f"20{yr}"
            return f"{yr}-{mon}-{day}T12:00:00Z"

    # Format: "2026-04-10T14:30:00Z" (already ISO)
    m = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", date_str)
    if m:
        s = m.group(1)
        if not s.endswith("Z"):
            s += "Z"
        return s

    # Format: "2026-04-10" date only
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", date_str)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}T00:00:00Z"

    # Format: "04/10/2026" or "10/04/2026"
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", date_str)
    if m:
        return f"{m.group(3)}-{m.group(1).zfill(2)}-{m.group(2).zfill(2)}T00:00:00Z"

    # Format: "10 APR 2026" or "10 APR 26"
    m = re.match(r"(\d{1,2})\s+([A-Za-z]{3})\s+(\d{2,4})", date_str)
    if m:
        day = m.group(1).zfill(2)
        mon = MONTH_MAP.get(m.group(2).upper())
        yr  = m.group(3)
        if mon:
            yr = yr if len(yr) == 4 else f"20{yr}"
            return f"{yr}-{mon}-{day}T00:00:00Z"

    # Unix timestamp (integer or float)
    try:
        ts = float(date_str)
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        pass

    return None

def parse_cancel_date(text):
    """
    Extract the cancellation/expiry date from NAVAREA text like:
    'CANCEL THIS MSG 221443Z APR 26'
    Returns ISO-8601 string or None.
    """
    m = re.search(
        r"CANCEL\s+THIS\s+MSG\s+(\d{6}Z\s+[A-Za-z]{3}\s+\d{2,4})",
        text, re.IGNORECASE
    )
    if m:
        raw = m.group(1)
        # raw looks like "221443Z APR 26" — reformat to match parse_date_flexible
        dm = re.match(r"(\d{2})\d{4}Z\s+([A-Za-z]{3})\s+(\d{2,4})", raw)
        if dm:
            day = dm.group(1)
            mon = MONTH_MAP.get(dm.group(2).upper())
            yr  = dm.group(3)
            if mon:
                yr = yr if len(yr) == 4 else f"20{yr}"
                return f"{yr}-{mon}-{day}T23:59:59Z"
    return None

def is_currently_active(start_iso, end_iso):
    """Return True if the warning is active right now."""
    now = datetime.now(tz=timezone.utc)
    try:
        start = datetime.fromisoformat(start_iso.replace("Z", "+00:00")) if start_iso else None
    except Exception:
        start = None
    try:
        end = datetime.fromisoformat(end_iso.replace("Z", "+00:00")) if end_iso else None
    except Exception:
        end = None

    if start and start > now:
        return False   # hasn't started yet
    if end and end < now:
        return False   # already expired
    return True

def get_color(text):
    t = text.upper()
    if any(kw in t for kw in ["ROCKET","SPACE","MISSILE","AEROSPACE","LAUNCH","DEBRIS","SATELLITE"]):
        return {"poly": [0, 255, 255, 60], "line": [0, 255, 255, 255], "label": "Aerospace/Missile"}
    elif any(kw in t for kw in ["CABLE","SURVEY","DREDGE","MOORING","BUOY","PIPELINE","SEISMIC"]):
        return {"poly": [0, 100, 255, 60], "line": [0, 100, 255, 255], "label": "Subsea/Cable Survey"}
    elif any(kw in t for kw in ["GUNNERY","ORDNANCE","EXPLOSIVE","LIVE FIRE","FIRING","TORPEDO","MINE"]):
        return {"poly": [255, 0, 0, 60], "line": [255, 0, 0, 255], "label": "Live Fire/Ordnance"}
    elif any(kw in t for kw in ["NOTAM","TFR","RESTRICTED","PROHIBITED","AIRSPACE","FLIGHT"]):
        return {"poly": [180, 0, 255, 60], "line": [180, 0, 255, 255], "label": "Airspace/TFR"}
    else:
        return {"poly": [255, 150, 0, 60], "line": [255, 150, 0, 255], "label": "General Hazard"}

# Intelligence keyword taxonomy — each entry is (tag_name, [keywords])
# Tags are written to CZML properties so the UI can filter/highlight by them.
INTEL_KEYWORDS = [
    ("UAS",          ["UAS","UAV","DRONE","UNMANNED","RPV","REMOTELY PILOTED"]),
    ("LIVE_WEAPONS", ["LIVE FIRE","LIVE WEAPON","GUNNERY","ORDNANCE","EXPLOSIVE",
                      "TORPEDO","MINE","FIRING","AMMUNITION","WARHEAD"]),
    ("SUBMARINE",    ["SUBMARINE","SUBMERGED","SUB EXERCISE","SUBSURFACE","SSN","SSBN","SSK"]),
    ("SPACE_DEBRIS", ["SPACE DEBRIS","REENTRY","RE-ENTRY","FALLING DEBRIS","ORBITAL DEBRIS"]),
    ("MISSILE",      ["MISSILE","ROCKET","BALLISTIC","CRUISE MISSILE","ICBM","SRBM","MRBM",
                      "HYPERSONIC","LAUNCH VEHICLE"]),
    ("NUCLEAR",      ["NUCLEAR","RADIOLOGICAL","NBC","CBRN","RADIOACTIVE"]),
    ("EXERCISE",     ["EXERCISE","TRAINING","DRILL","WAR GAME","WARGAME","JOINT EXERCISE",
                      "OPERATION","OPS AREA"]),
    ("LASER",        ["LASER","HIGH ENERGY","DIRECTED ENERGY","HEL","DEW"]),
    ("CYBER",        ["CYBER","EMP","ELECTROMAGNETIC PULSE","JAMMING","GPS JAMMING",
                      "GNSS INTERFERENCE","SPOOFING"]),
    ("MODU",         ["DRILLING","MODU","MOBILE OFFSHORE","DRILL SHIP","DRILLSHIP",
                      "OFFSHORE PLATFORM","WELLHEAD"]),
]

def extract_keywords(text):
    """
    Scan text for intelligence-relevant keywords.
    Returns a list of matched tag strings (e.g. ["UAS", "EXERCISE"]).
    """
    t = text.upper()
    matched = []
    for tag, keywords in INTEL_KEYWORDS:
        if any(kw in t for kw in keywords):
            matched.append(tag)
    return matched

def extract_coords_from_text(text):
    """
    Extract all coordinate pairs from free-text NAVAREA/NOTAM messages.
    Handles DM format: 46-31.23N 140-10.57W
    Returns list of [lon, lat, 0, ...] cartographic degrees (all areas merged).
    """
    pattern = re.compile(
        r"(\d{1,2}-\d{2}(?:\.\d+)?[NS])\s+(\d{1,3}-\d{2}(?:\.\d+)?[WE])"
    )
    pairs = pattern.findall(text)
    carto = []
    for lat_s, lon_s in pairs:
        lat = dm_to_dd(lat_s)
        lon = dm_to_dd(lon_s)
        if lat is not None and lon is not None:
            carto.extend([lon, lat, 0])
    return carto


def _carto_is_global(carto):
    """
    Return True if the carto list spans the entire globe (or nearly so).
    Only used to suppress polygon rendering for worldwide NOTAMs like
    NAVAREA IV 138/22 which covers the entire Southern Ocean / polar region.
    Threshold: lat span > 140° OR lon span > 330°.
    """
    if len(carto) < 6:
        return False
    lats = [carto[i+1] for i in range(0, len(carto)-2, 3)]
    lons = [carto[i]   for i in range(0, len(carto)-2, 3)]
    lat_span = max(lats) - min(lats)
    lon_span = max(lons) - min(lons)
    return lat_span > 140.0 or lon_span > 330.0


def extract_sub_area_cartos(text):
    """
    Split a NOTAM text on sub-area labels (A., B., C. ...) and return a list
    of separate carto lists — one per sub-area.  If no sub-area labels are
    found, returns a single-element list containing all coordinates.

    This prevents multi-area NOTAMs from being merged into one giant
    self-intersecting polygon.

    Sub-area format in NAVAREA/HYDROPAC texts:
        "   A. <text>"  (3+ spaces, single uppercase letter, dot, space)
    """
    coord_re = re.compile(
        r"(\d{1,2}-\d{2}(?:\.\d+)?[NS])\s+(\d{1,3}-\d{2}(?:\.\d+)?[WE])"
    )
    # Match "   A. " or "   B. " etc. — 3+ spaces, capital letter, dot, whitespace
    sub_re = re.compile(r'(?m)^\s{2,}([A-Z])\.\s+')
    parts = sub_re.split(text)

    # sub_re.split returns [before_first, label1, block1, label2, block2, ...]
    # We want the blocks (every other element starting at index 2, or index 1 if no prefix)
    if len(parts) <= 2:
        # No sub-areas found — return all coords as one block
        all_carto = extract_coords_from_text(text)
        return [all_carto] if (all_carto and not _carto_is_global(all_carto)) else []

    # Collect blocks: parts[0] is preamble, then alternating label/block
    result = []
    # parts = [preamble, 'A', block_A, 'B', block_B, ...]
    i = 2  # skip preamble and first label
    while i < len(parts):
        block = parts[i]
        pairs = coord_re.findall(block)
        carto = []
        for lat_s, lon_s in pairs:
            lat = dm_to_dd(lat_s)
            lon = dm_to_dd(lon_s)
            if lat is not None and lon is not None:
                carto.extend([lon, lat, 0])
        if len(carto) >= 6 and not _carto_is_global(carto):
            result.append(carto)
        i += 2  # skip next label, go to next block

    if not result:
        all_carto = extract_coords_from_text(text)
        return [all_carto] if (all_carto and not _carto_is_global(all_carto)) else []
    return result

def convex_hull_carto(carto):
    """
    Given a flat [lon, lat, alt, lon, lat, alt, ...] list, compute the
    convex hull of the (lon, lat) points and return a new flat list in
    counter-clockwise winding order (closed — first point repeated at end).

    This prevents self-intersecting / bowtie polygons that occur when
    NOTAM coordinates are listed in text order rather than winding order.

    Falls back to the original carto if fewer than 3 unique points.
    """
    # Unpack into (lon, lat) pairs (ignore altitude)
    pts = []
    for i in range(0, len(carto) - 2, 3):
        pts.append((carto[i], carto[i+1]))

    # Deduplicate
    unique = list(dict.fromkeys(pts))
    if len(unique) < 3:
        return carto

    # Graham scan convex hull
    def cross(O, A, B):
        return (A[0] - O[0]) * (B[1] - O[1]) - (A[1] - O[1]) * (B[0] - O[0])

    pts_sorted = sorted(unique)
    lower = []
    for p in pts_sorted:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts_sorted):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    hull = lower[:-1] + upper[:-1]   # CCW, not closed

    if len(hull) < 3:
        return carto

    # Close the ring and flatten back to [lon, lat, 0, ...]
    hull_closed = hull + [hull[0]]
    result = []
    for lon, lat in hull_closed:
        result.extend([lon, lat, 0])
    return result


def build_czml_entity(entity_id, name, description, start_iso, end_iso,
                       carto, colors, source_tag, active_now, sub_cartos=None):
    """Build a CZML entity dict.

    sub_cartos: optional list of per-sub-area carto lists (from extract_sub_area_cartos).
    When provided, each sub-area gets its own convex-hull polygon.
    """
    safe_id = re.sub(r"[^A-Za-z0-9_\-]", "_", entity_id)

    # Compute effective end date.
    # If no end date is provided the record has no known expiry (e.g. NGA standing
    # notices stay active until explicitly cancelled).  Use a fixed far-future sentinel
    # so the availability string is stable across runs (prevents false delta-report hits).
    effective_end = end_iso
    if not effective_end:
        effective_end = "2099-12-31T23:59:59Z"

    # CZML availability interval
    availability = None
    if start_iso and effective_end:
        availability = f"{start_iso}/{effective_end}"
    elif start_iso:
        availability = start_iso  # open-ended

    # Recompute active_now using the effective end date
    active_now = is_currently_active(start_iso, effective_end)

    # Extract intelligence keyword tags from the raw description text
    tags = extract_keywords(description)
    tags_str = ",".join(tags) if tags else ""

    # Build HTML badge string for the description popup
    badge_html = ""
    if tags:
        badge_html = "<br/><b>Intel Tags:</b> " + " ".join(
            f'<span style="background:#1a3a5c;border:1px solid #0af;border-radius:3px;'
            f'padding:1px 5px;font-size:0.85em;color:#0af;margin-right:3px">{t}</span>'
            for t in tags
        )

    entity = {
        "id": safe_id,
        "name": name,
        "description": (
            f"<b>Source:</b> {source_tag}<br/>"
            f"<b>Active:</b> {'YES' if active_now else 'NO'}<br/>"
            f"<b>Start:</b> {start_iso or 'Unknown'}<br/>"
            f"<b>End:</b> {effective_end or 'Unknown'}"
            f"{badge_html}<br/><br/>{description}"
        ),
        "properties": {
            "timestamp": start_iso or datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_time": effective_end or "",
            "source": source_tag,
            "active": active_now,
            "tags": tags_str,
            "category": colors.get("label", "General Hazard")
        }
    }

    if availability:
        entity["availability"] = availability

    # Determine which carto lists to use for polygons.
    # Use sub_cartos (per-sub-area) if available; otherwise fall back to merged carto.
    # Filter out any sub-area that spans an unreasonably large area (worldwide NOTAMs).
    if sub_cartos and len(sub_cartos) > 0:
        poly_cartos = [sc for sc in sub_cartos if sc and not _carto_is_global(sc)]
    elif carto and not _carto_is_global(carto):
        poly_cartos = [carto]
    else:
        poly_cartos = []

    if len(carto) >= 6:
        # Polyline: use the first valid sub-area hull for the outline,
        # or fall back to the raw carto if not global.
        line_src = poly_cartos[0] if poly_cartos else (carto if not _carto_is_global(carto) else None)
        if line_src:
            hull_line = convex_hull_carto(line_src)
            entity["polyline"] = {
                "positions": {"cartographicDegrees": hull_line},
                "material": {"solidColor": {"color": {"rgba": colors["line"]}}},
                "width": 3 if active_now else 1
            }

        # Build one polygon per sub-area, each with its own convex hull
        valid_polys = []
        for sc in poly_cartos:
            if len(sc) < 9:
                continue
            closed = sc + sc[:3] if sc[:3] != sc[-3:] else sc
            hull = convex_hull_carto(closed)
            if len(hull) >= 12:
                valid_polys.append(hull)

        if valid_polys:
            # Primary polygon (first sub-area or only area)
            entity["polygon"] = {
                "positions": {"cartographicDegrees": valid_polys[0]},
                "material": {"solidColor": {"color": {"rgba": colors["poly"]}}},
                "height": 0,
                "clampToGround": True
            }
            # Additional sub-area polygons stored in properties for JS rendering
            if len(valid_polys) > 1:
                entity.setdefault("properties", {})["extra_polygons"] = valid_polys[1:]
    elif len(carto) == 3:
        # Single point — render as billboard/point
        entity["position"] = {
            "cartographicDegrees": carto
        }
        entity["point"] = {
            "pixelSize": 10,
            "color": {"rgba": colors["line"]},
            "outlineColor": {"rgba": [255, 255, 255, 200]},
            "outlineWidth": 1
        }

    return entity

# ─────────────────────────────────────────────
# SOURCE 1: NGA MSI Broadcast Warnings (NAVAREA)
# ─────────────────────────────────────────────
def fetch_nga_broadcast():
    """
    Fetch ALL active NGA MSI Broadcast Warnings.
    The API returns all NAVAREAs in a single call when no navArea filter is set.
    We also query each known navArea individually to catch any that the bulk
    call might miss, then deduplicate.
    """
    print("\n[SOURCE 1] NGA MSI Broadcast Warnings (all NAVAREAs)...")
    headers = {'Accept': 'application/json',
               'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    results = []
    seen_msg_ids = set()

    def _ingest_warnings(warnings, label):
        count = 0
        for w in warnings:
            msg_num  = w.get('msgNumber', 'UNK')
            nav_area = w.get('navArea', w.get('navarea', 'UNK'))
            dedup_key = f"{nav_area}_{msg_num}"
            if dedup_key in seen_msg_ids:
                continue
            seen_msg_ids.add(dedup_key)

            text      = w.get('text', w.get('body', ''))
            subregion = w.get('subregion', w.get('subareaName', ''))
            name      = f"NAVAREA {nav_area} {msg_num}"
            if subregion:
                name += f" - {subregion}"

            start_raw = (w.get('issueDate') or w.get('issue_date') or
                         w.get('effectiveDate') or '')
            end_raw   = (w.get('cancelDate') or w.get('cancel_date') or
                         w.get('expiryDate') or '')

            start_iso = parse_date_flexible(start_raw)
            if not start_iso:
                dm = re.search(r"(\d{2}\d{4}Z\s+[A-Za-z]{3}\s+\d{2,4})", text)
                start_iso = parse_date_flexible(dm.group(1)) if dm else None

            end_iso = parse_date_flexible(end_raw)
            if not end_iso:
                end_iso = parse_cancel_date(text)

            sub_cartos = extract_sub_area_cartos(text)
            carto  = sub_cartos[0] if sub_cartos else []
            colors = get_color(text)
            active = is_currently_active(start_iso, end_iso)

            results.append({
                "id": f"NGA_BW_{nav_area}_{msg_num}",
                "name": name,
                "description": text,
                "start_iso": start_iso,
                "end_iso": end_iso,
                "carto": carto,
                "sub_cartos": sub_cartos,
                "colors": colors,
                "source": "NGA NAVAREA",
                "active": active
            })
            count += 1
        return count

    # ── Primary: bulk fetch all active warnings ────────────
    try:
        r = requests.get(NGA_BROADCAST_URL,
                         params={"status": "active", "output": "json"},
                         headers=headers, verify=False, timeout=30)
        r.raise_for_status()
        data = r.json()
        warnings = (data.get('broadcast-warn') or data.get('results') or
                    data.get('maritimeApi') or (data if isinstance(data, list) else []))
        n = _ingest_warnings(warnings, "bulk")
        print(f"  -> {n} records from NGA bulk fetch")
    except Exception as e:
        print(f"  X NGA bulk fetch failed: {e}")

    # ── Secondary: per-navArea queries to catch any gaps ──
    # All 16 NAVAREAs + special types
    nav_areas = ["I","II","III","IV","V","VI","VII","VIII","IX","X",
                 "XI","XII","XIII","XIV","XV","XVI",
                 "hydrolant","hydropac","navtex"]
    extra = 0
    for area in nav_areas:
        try:
            r = requests.get(NGA_BROADCAST_URL,
                             params={"status": "active", "output": "json",
                                     "navArea": area},
                             headers=headers, verify=False, timeout=15)
            if r.status_code == 200:
                data = r.json()
                w2 = (data.get('broadcast-warn') or data.get('results') or
                      (data if isinstance(data, list) else []))
                extra += _ingest_warnings(w2, area)
        except Exception:
            pass
    if extra:
        print(f"  -> {extra} additional records from per-navArea queries")

    print(f"  -> Total NGA records: {len(results)}")
    return results

# ─────────────────────────────────────────────
# SOURCE 2: NGA MSI text file downloads
#   HYDROLANT, HYDROPAC, NAVTEX, and additional NAVAREA in-force files
#   are published as plain .txt files on the NGA MSI website.
#   Same format as raw_notams.txt — reuse the same block parser.
# ─────────────────────────────────────────────
def _parse_nga_text_blocks(content, source_tag):
    """
    Parse NGA-format plain text (HYDROLANT/HYDROPAC/NAVTEX/NAVAREA in-force).
    Returns list of standard record dicts.
    """
    results = []

    # Split on NAVAREA / HYDROLANT / HYDROPAC / NAVTEX message headers
    # Each block starts with a DTG line then a message header line
    blocks = re.split(
        r"(?=\d{6}Z\s+[A-Za-z]{3}\s+\d{2,4}\s*\n)",
        content
    )

    for block in blocks:
        block = block.strip()
        if len(block) < 20:
            continue

        # Extract issue timestamp
        ts_match = re.match(r"(\d{6}Z\s+[A-Za-z]{3}\s+\d{2,4})", block)
        start_iso = parse_date_flexible(ts_match.group(1)) if ts_match else None

        # Try NAVAREA header
        nav_match = re.search(r"(NAVAREA|HYDROLANT|HYDROPAC|NAVTEX)\s+([IVXLCDM\d]+)\s+([\d/]+)\.", block)
        if not nav_match:
            # Try simpler number-only header e.g. "HYDROLANT 1234/26."
            nav_match2 = re.search(r"(HYDROLANT|HYDROPAC|NAVTEX)\s+([\d/]+)\.", block)
            if not nav_match2:
                continue
            msg_type = nav_match2.group(1)
            nav_area = ""
            msg_num  = nav_match2.group(2)
        else:
            msg_type = nav_match.group(1)
            nav_area = nav_match.group(2)
            msg_num  = nav_match.group(3)

        name    = f"{msg_type} {nav_area} {msg_num}".strip()
        end_iso = parse_cancel_date(block)
        sub_cartos = extract_sub_area_cartos(block)
        carto   = sub_cartos[0] if sub_cartos else []
        colors  = get_color(block)
        active  = is_currently_active(start_iso, end_iso)
        safe_id = f"{source_tag}_{msg_type}_{nav_area}_{msg_num}".replace("/", "_").replace(" ", "_")

        results.append({
            "id": safe_id,
            "name": name,
            "description": block[:2000],
            "start_iso": start_iso,
            "end_iso": end_iso,
            "sub_cartos": sub_cartos,
            "carto": carto,
            "colors": colors,
            "source": f"NGA {msg_type}",
            "active": active
        })

    return results


def fetch_nga_hydro():
    """
    Download NGA MSI HYDROLANT, HYDROPAC, NAVTEX, and additional NAVAREA
    in-force text files directly from the NGA MSI website.
    These are publicly accessible without authentication.
    """
    print("\n[SOURCE 2] NGA MSI text file downloads (HYDROLANT/HYDROPAC/NAVTEX)...")
    results = []
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/plain,text/html,*/*'
    }

    for label, url in NGA_TEXT_FILES.items():
        try:
            r = requests.get(url, headers=headers, verify=False, timeout=30)
            if r.status_code == 200 and len(r.text) > 100:
                parsed = _parse_nga_text_blocks(r.text, label)
                print(f"  -> {label}: {len(parsed)} blocks parsed ({len(r.text)//1024}KB)")
                results.extend(parsed)
            else:
                print(f"  X {label}: HTTP {r.status_code} or empty response")
        except Exception as e:
            print(f"  X {label} download failed: {e}")

    return results

# ─────────────────────────────────────────────
# SOURCE 3: FAA NOTAM API + AIM fallback
# ─────────────────────────────────────────────
def _parse_faa_notam_item(n, idx):
    """Parse a single FAA NOTAM JSON item into our standard record dict."""
    props     = n.get('properties', n)
    core_data = props.get('coreNOTAMData', {})
    core      = core_data.get('notam', props)

    notam_id  = (core.get('id') or core.get('notamID') or
                 props.get('id') or f"FAA_{idx}")
    location  = (core.get('location') or core.get('icaoLocation') or
                 props.get('location') or '')
    text      = (core.get('text') or core.get('traditionalMessage') or
                 core.get('message') or props.get('text') or '')
    start_raw = (core.get('effectiveStart') or core.get('issueDate') or
                 props.get('effectiveStart') or '')
    end_raw   = (core.get('effectiveEnd') or core.get('expirationDate') or
                 props.get('effectiveEnd') or '')

    # Coordinates — may be in geometry or coordinates field
    lat, lon = None, None
    geom = n.get('geometry') or props.get('geometry') or {}
    if geom.get('type') == 'Point' and geom.get('coordinates'):
        lon, lat = geom['coordinates'][0], geom['coordinates'][1]
    elif isinstance(core.get('coordinates'), dict):
        lat = core['coordinates'].get('lat')
        lon = core['coordinates'].get('lon')

    name      = f"FAA NOTAM {notam_id} {location}".strip()
    start_iso = parse_date_flexible(start_raw)
    end_iso   = parse_date_flexible(end_raw)
    colors    = get_color(text)
    active    = is_currently_active(start_iso, end_iso)

    carto = []
    if lat is not None and lon is not None:
        try:
            carto = [float(lon), float(lat), 0]
        except Exception:
            pass
    if not carto:
        carto = extract_coords_from_text(text)

    return {
        "id": f"FAA_{notam_id}",
        "name": name,
        "description": text,
        "start_iso": start_iso,
        "end_iso": end_iso,
        "carto": carto,
        "colors": colors,
        "source": "FAA NOTAM",
        "active": active
    }


def fetch_faa_notams():
    """
    FAA NOTAM API v1 — requires client_id/client_secret registered at
    https://api.faa.gov  (free registration).
    Falls back to the public FAA AIM NOTAM search if the API key is missing.
    """
    print("\n[SOURCE 3] FAA NOTAM API (aeronautical)...")
    results = []

    # ── Try the registered API first ──────────────────────
    # To use: replace the empty strings below with your credentials from
    # https://api.faa.gov/  (free, instant registration)
    CLIENT_ID     = ""   # e.g. "abc123"
    CLIENT_SECRET = ""   # e.g. "xyz789"

    if CLIENT_ID and CLIENT_SECRET:
        headers = {
            'Accept': 'application/json',
            'client_id': CLIENT_ID,
            'client_secret': CLIENT_SECRET
        }
        page = 1
        total_fetched = 0
        while True:
            params = {
                "pageSize": 1000,
                "pageNum": page,
                "sortBy": "issueDate",
                "sortOrder": "Desc"
            }
            try:
                r = requests.get(FAA_NOTAM_URL, params=params,
                                 headers=headers, timeout=30)
                if r.status_code in (401, 403):
                    print("  X FAA API credentials rejected — falling back to AIM search")
                    break
                r.raise_for_status()
                data  = r.json()
                items = data.get('items', data.get('notams',
                                 data if isinstance(data, list) else []))
                if not items:
                    break
                total_fetched += len(items)
                print(f"  -> Page {page}: {len(items)} FAA NOTAMs")
                for i, n in enumerate(items):
                    results.append(_parse_faa_notam_item(n, total_fetched - len(items) + i))
                total_count = data.get('totalCount', data.get('total', 0))
                if total_fetched >= total_count or len(items) < 1000:
                    break
                page += 1
                time.sleep(0.3)
            except Exception as e:
                print(f"  X FAA NOTAM page {page} failed: {e}")
                break
        print(f"  -> Total FAA NOTAMs via API: {total_fetched}")
        if results:
            return results

    # ── Fallback: FAA AIM public NOTAM search (no key needed) ──
    print("  -> Trying FAA AIM public search (no key)...")
    try:
        # AIM search accepts POST with JSON body
        payload = {
            "notamType": "ALL",
            "radius": 9999,
            "lat": 38.0,
            "lon": -97.0,
            "pageSize": 500,
            "pageNum": 1
        }
        r = requests.post(FAA_AIM_URL, json=payload,
                          headers={'Accept': 'application/json',
                                   'Content-Type': 'application/json'},
                          timeout=30)
        if r.status_code == 200:
            data  = r.json()
            items = data.get('notamList', data.get('items',
                             data if isinstance(data, list) else []))
            print(f"  -> {len(items)} NOTAMs from FAA AIM search")
            for i, n in enumerate(items):
                results.append(_parse_faa_notam_item(n, i))
        else:
            print(f"  X FAA AIM search returned {r.status_code}")
    except Exception as e:
        print(f"  X FAA AIM search failed: {e}")

    print(f"  -> Total FAA NOTAMs fetched: {len(results)}")
    return results


# ─────────────────────────────────────────────
# SOURCE 4: USCG NavCen — LNM + BNM RSS
# ─────────────────────────────────────────────
def _parse_uscg_item(item, source_tag):
    lnm_id    = (item.get('lnmNumber') or item.get('bnmNumber') or
                 item.get('id') or 'UNK')
    district  = item.get('district', item.get('districtName', ''))
    title     = item.get('title', item.get('subject', item.get('headline', '')))
    text      = (item.get('text') or item.get('body') or
                 item.get('description') or item.get('summary') or '')
    start_raw = (item.get('issueDate') or item.get('date') or
                 item.get('pubDate') or '')
    end_raw   = (item.get('expiryDate') or item.get('cancelDate') or
                 item.get('expireDate') or '')
    lat       = item.get('latitude', item.get('lat'))
    lon       = item.get('longitude', item.get('lon'))

    name      = f"USCG {source_tag} {district}-{lnm_id}: {title}"[:120]
    start_iso = parse_date_flexible(start_raw)
    end_iso   = parse_date_flexible(end_raw)
    colors    = get_color(text + " " + title)
    active    = is_currently_active(start_iso, end_iso)

    carto = []
    if lat is not None and lon is not None:
        try:
            carto = [float(lon), float(lat), 0]
        except Exception:
            pass
    if not carto:
        carto = extract_coords_from_text(text)

    return {
        "id": f"USCG_{source_tag}_{district}_{lnm_id}",
        "name": name,
        "description": text,
        "start_iso": start_iso,
        "end_iso": end_iso,
        "carto": carto,
        "colors": colors,
        "source": f"USCG {source_tag}",
        "active": active
    }


def fetch_uscg_lnm():
    print("\n[SOURCE 4] USCG NavCen (LNM + BNM)...")
    results = []

    # ── LNM JSON (try all known endpoints) ────────────────
    lnm_ok = False
    for url in USCG_LNM_URLS:
        if url.endswith('.gov/?pageName=lnmMain'):
            # HTML fallback — scrape the page for coordinate data
            try:
                r = requests.get(url, timeout=20, verify=False,
                                 headers={'User-Agent': 'Mozilla/5.0',
                                          'Accept': 'text/html'})
                if r.status_code == 200 and len(r.text) > 500:
                    # Extract any coordinate pairs from the HTML
                    text_content = re.sub(r'<[^>]+>', ' ', r.text)
                    carto = extract_coords_from_text(text_content)
                    if carto:
                        now_iso = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                        results.append({
                            "id": "USCG_LNM_HTML_MAIN",
                            "name": "USCG LNM Main Page (scraped)",
                            "description": text_content[:1000],
                            "start_iso": now_iso,
                            "end_iso": None,
                            "carto": carto,
                            "colors": get_color(text_content),
                            "source": "USCG LNM",
                            "active": True
                        })
                        print(f"  -> USCG LNM HTML scrape: {len(carto)//3} coords")
                        lnm_ok = True
            except Exception as e:
                print(f"  X USCG LNM HTML {url}: {e}")
            continue

        try:
            r = requests.get(url, timeout=20, verify=False,
                             headers={'Accept': 'application/json',
                                      'User-Agent': 'Mozilla/5.0'})
            if r.status_code == 200:
                try:
                    data  = r.json()
                    items = data if isinstance(data, list) else data.get('lnm', data.get('items', []))
                    print(f"  -> {len(items)} USCG LNM records from {url}")
                    for item in items:
                        results.append(_parse_uscg_item(item, "LNM"))
                    lnm_ok = True
                    break
                except Exception:
                    # Not JSON — try parsing as text
                    carto = extract_coords_from_text(r.text)
                    if carto:
                        print(f"  -> USCG LNM text parse: {len(carto)//3} coords from {url}")
                        lnm_ok = True
            else:
                print(f"  X LNM {url}: HTTP {r.status_code}")
        except Exception as e:
            print(f"  X LNM {url}: {e}")

    if not lnm_ok:
        print("  X All USCG LNM endpoints failed")

    # ── BNM RSS feed ──────────────────────────────────────
    try:
        r = requests.get(USCG_BNM_RSS, timeout=20, verify=False)
        if r.status_code == 200:
            # Parse RSS XML manually (no external lib needed)
            xml = r.text
            items_xml = re.findall(r'<item>(.*?)</item>', xml, re.DOTALL)
            print(f"  -> {len(items_xml)} USCG BNM RSS items")
            for i, item_xml in enumerate(items_xml):
                def tag(t):
                    m = re.search(rf'<{t}[^>]*>(.*?)</{t}>', item_xml, re.DOTALL)
                    return m.group(1).strip() if m else ''
                title     = re.sub(r'<[^>]+>', '', tag('title'))
                desc      = re.sub(r'<[^>]+>', '', tag('description'))
                pub_date  = tag('pubDate')
                link      = tag('link')
                start_iso = parse_date_flexible(pub_date)
                end_iso   = parse_cancel_date(desc)
                colors    = get_color(title + " " + desc)
                active    = is_currently_active(start_iso, end_iso)
                carto     = extract_coords_from_text(desc)
                results.append({
                    "id": f"USCG_BNM_{i}_{re.sub(r'[^A-Za-z0-9]','_',title[:30])}",
                    "name": f"USCG BNM: {title}"[:120],
                    "description": desc,
                    "start_iso": start_iso,
                    "end_iso": end_iso,
                    "carto": carto,
                    "colors": colors,
                    "source": "USCG BNM",
                    "active": active
                })
        else:
            print(f"  X USCG BNM RSS returned {r.status_code}")
    except Exception as e:
        print(f"  X USCG BNM RSS failed: {e}")

    return results

# ─────────────────────────────────────────────
# SOURCE 5: Parse local raw_notams.txt (NGA text file)
# ─────────────────────────────────────────────
def parse_local_raw_notams():
    print("\n[SOURCE 5] Local raw_notams.txt (NGA text file)...")
    results = []
    if not os.path.exists(RAW_PATH):
        print("  X raw_notams.txt not found -- skipping")
        return results

    with open(RAW_PATH, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    # Split on NAVAREA headers — each block starts with a timestamp line then NAVAREA XX NNN/YY
    # Pattern: timestamp line followed by NAVAREA block
    blocks = re.split(r"(?=\d{6}Z\s+[A-Za-z]{3}\s+\d{2,4}\s*\n\s*NAVAREA)", content)

    parsed = 0
    for block in blocks:
        block = block.strip()
        if not block:
            continue

        # Extract issue timestamp
        ts_match = re.match(r"(\d{6}Z\s+[A-Za-z]{3}\s+\d{2,4})", block)
        start_iso = parse_date_flexible(ts_match.group(1)) if ts_match else None

        # Extract NAVAREA header
        nav_match = re.search(r"NAVAREA\s+([IVXLCDM\d]+)\s+([\d/]+)\.", block)
        if not nav_match:
            continue
        nav_area = nav_match.group(1)
        msg_num  = nav_match.group(2)
        name     = f"NAVAREA {nav_area} {msg_num} (local)"

        end_iso    = parse_cancel_date(block)
        sub_cartos = extract_sub_area_cartos(block)
        carto      = sub_cartos[0] if sub_cartos else extract_coords_from_text(block)
        colors     = get_color(block)
        active     = is_currently_active(start_iso, end_iso)

        results.append({
            "id":          f"LOCAL_NAVAREA_{nav_area}_{msg_num.replace('/', '_')}",
            "name":        name,
            "description": block[:2000],
            "start_iso":   start_iso,
            "end_iso":     end_iso,
            "carto":       carto,
            "sub_cartos":  sub_cartos,
            "colors":      colors,
            "source":      "NGA Text (local)",
            "active":      active
        })
        parsed += 1

    print(f"  -> Parsed {parsed} blocks from raw_notams.txt")
    return results

# ─────────────────────────────────────────────
# SOURCE 6: NGA MSI Anti-Shipping Activity Messages (ASAM)
# Multiple endpoint attempts — NGA has changed this URL several times.
# ─────────────────────────────────────────────
NGA_ASAM_URLS = [
    "https://msi.nga.mil/api/publications/asam",                          # original
    "https://msi.nga.mil/api/publications/anti-shipping-activity-messages",# v2 attempt
    "https://msi.nga.mil/api/publications/asam?output=json",              # with param
    "https://msi.nga.mil/publications/asam",                              # no /api/
]

def fetch_nga_asam():
    """
    Fetch NGA MSI Anti-Shipping Activity Messages (ASAM).
    These report hostile actions, piracy, and harassment against vessels.
    API returns JSON with lat/lon already parsed.
    """
    print("\n[SOURCE 6] NGA MSI Anti-Shipping Activity Messages (ASAM)...")
    headers = {'Accept': 'application/json',
               'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    results = []

    data = None
    for url in NGA_ASAM_URLS:
        try:
            r = requests.get(url, params={"output": "json", "maxRecords": 2000},
                             headers=headers, verify=False, timeout=15)
            if r.status_code == 200 and len(r.content) > 10:
                data = r.json()
                print(f"  -> ASAM endpoint OK: {url}")
                break
            else:
                print(f"  X ASAM {url} -> {r.status_code}")
        except Exception as e:
            print(f"  X ASAM {url} -> {type(e).__name__}")

    try:
        if not data:
            print("  X All ASAM endpoints failed")
            return results
        items = (data.get('asam') or data.get('results') or
                 (data if isinstance(data, list) else []))
        print(f"  -> {len(items)} ASAM records fetched")

        for item in items:
            ref      = item.get('reference', item.get('ref', 'UNK'))
            subreg   = item.get('subreg', item.get('subregion', ''))
            navarea  = item.get('navArea', item.get('navarea', ''))
            desc     = item.get('description', item.get('desc', item.get('text', '')))
            victim   = item.get('victim', '')
            aggressor= item.get('aggressor', '')
            hostility= item.get('hostility', '')

            # Build a rich description
            full_desc = desc
            if victim:    full_desc += f"\nVictim: {victim}"
            if aggressor: full_desc += f"\nAggressor: {aggressor}"
            if hostility: full_desc += f"\nHostility: {hostility}"

            # Date fields
            date_raw  = (item.get('date') or item.get('occurrenceDate') or
                         item.get('dateOccurrence') or '')
            start_iso = parse_date_flexible(str(date_raw)) if date_raw else None

            # Coordinates — ASAM API provides lat/lon directly
            lat = item.get('latitude',  item.get('lat'))
            lon = item.get('longitude', item.get('lon'))
            carto = []
            if lat is not None and lon is not None:
                try:
                    carto = [float(lon), float(lat), 0]
                except (ValueError, TypeError):
                    pass

            # If no direct coords, try extracting from text
            if not carto:
                carto = extract_coords_from_text(full_desc)

            name   = f"ASAM {ref}"
            if subreg:  name += f" - {subreg}"
            if navarea: name += f" (NAVAREA {navarea})"

            # ASAM events are always hostile/threat — use red color
            colors = {"poly": [255, 50, 50, 60], "line": [255, 50, 50, 255],
                      "label": "Live Fire/Ordnance"}

            results.append({
                "id":          f"ASAM_{ref.replace('/', '_').replace('-', '_')}",
                "name":        name,
                "description": full_desc,
                "start_iso":   start_iso,
                "end_iso":     None,
                "carto":       carto,
                "colors":      colors,
                "source":      "NGA ASAM",
                "active":      True
            })

    except Exception as e:
        print(f"  X ASAM parse error: {e}")

    print(f"  -> Total ASAM records: {len(results)}")
    return results


# ─────────────────────────────────────────────
# SOURCE 7: USCG NavCen — updated endpoints
# ─────────────────────────────────────────────
USCG_LNM_NEW_URLS = [
    "https://www.navcen.uscg.gov/api/lnm/getAll",
    "https://www.navcen.uscg.gov/api/lnm",
    "https://www.navcen.uscg.gov/api/broadcasts",
    "https://www.navcen.uscg.gov/json/lnmSummary",          # old (404 now)
    "https://www.navcen.uscg.gov/json/lnmSummary/getAll",   # old (404 now)
]
USCG_BNM_NEW_URLS = [
    "https://www.navcen.uscg.gov/api/bnm/rss",
    "https://www.navcen.uscg.gov/rss/bnm.xml",              # old
    "https://www.navcen.uscg.gov/api/bnm",
]

def fetch_uscg_updated():
    """
    Attempt updated USCG NavCen endpoints.
    Falls back gracefully if all fail.
    """
    print("\n[SOURCE 7] USCG NavCen (updated endpoints)...")
    headers = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json,text/xml,*/*'}
    results = []

    # Try LNM JSON endpoints
    for url in USCG_LNM_NEW_URLS:
        try:
            r = requests.get(url, headers=headers, verify=False, timeout=15)
            if r.status_code == 200 and len(r.content) > 50:
                try:
                    data = r.json()
                    items = data if isinstance(data, list) else (
                            data.get('lnm') or data.get('results') or data.get('data') or [])
                    print(f"  -> LNM endpoint OK: {url} ({len(items)} items)")
                    for item in items:
                        text  = (item.get('text') or item.get('description') or
                                 item.get('body') or str(item))
                        name  = item.get('title') or item.get('name') or 'USCG LNM'
                        start = parse_date_flexible(str(item.get('date') or item.get('issueDate') or ''))
                        carto = extract_coords_from_text(text)
                        colors= get_color(text)
                        results.append({
                            "id":          f"USCG_LNM_{hash(name) & 0xFFFFFF}",
                            "name":        name,
                            "description": text,
                            "start_iso":   start,
                            "end_iso":     None,
                            "carto":       carto,
                            "colors":      colors,
                            "source":      "USCG LNM",
                            "active":      True
                        })
                    break
                except Exception:
                    pass  # not JSON, try next
            else:
                print(f"  X LNM {url} -> {r.status_code}")
        except Exception as e:
            print(f"  X LNM {url} -> {type(e).__name__}")

    # Try BNM RSS
    for url in USCG_BNM_NEW_URLS:
        try:
            r = requests.get(url, headers=headers, verify=False, timeout=15)
            if r.status_code == 200 and len(r.content) > 50:
                # Parse RSS/XML
                import xml.etree.ElementTree as ET
                try:
                    root = ET.fromstring(r.content)
                    ns = {'atom': 'http://www.w3.org/2005/Atom'}
                    items = root.findall('.//item') or root.findall('.//atom:entry', ns)
                    print(f"  -> BNM RSS OK: {url} ({len(items)} items)")
                    for item in items:
                        title = (item.findtext('title') or item.findtext('atom:title', namespaces=ns) or 'USCG BNM')
                        desc  = (item.findtext('description') or item.findtext('atom:summary', namespaces=ns) or '')
                        pub   = (item.findtext('pubDate') or item.findtext('atom:published', namespaces=ns) or '')
                        start = parse_date_flexible(pub)
                        carto = extract_coords_from_text(desc)
                        colors= get_color(desc)
                        results.append({
                            "id":          f"USCG_BNM_{hash(title) & 0xFFFFFF}",
                            "name":        title,
                            "description": desc,
                            "start_iso":   start,
                            "end_iso":     None,
                            "carto":       carto,
                            "colors":      colors,
                            "source":      "USCG BNM",
                            "active":      True
                        })
                    break
                except Exception as xe:
                    print(f"  X BNM XML parse error: {xe}")
            else:
                print(f"  X BNM {url} -> {r.status_code}")
        except Exception as e:
            print(f"  X BNM {url} -> {type(e).__name__}")

    if not results:
        print("  X All USCG endpoints failed")
    else:
        print(f"  -> Total USCG records: {len(results)}")
    return results


# ─────────────────────────────────────────────
# SOURCE 8: FAA Special Use Airspace (SUA)
# FAA publishes SUA status via their ADDS (Aviation Digital Data Service).
# The SUA GeoJSON endpoint returns active/scheduled restricted areas,
# MOAs, warning areas, and prohibited areas.
# ─────────────────────────────────────────────
FAA_SUA_URLS = [
    # FAA ADDS SUA status feed
    "https://adds-faa.gov/api/sua/v1/sua",
    "https://aviationweather.gov/api/data/sua?format=json",
    "https://aviationweather.gov/cgi-bin/data/sua.php?format=json",
    # FAA ArcGIS SUA layer
    "https://services6.arcgis.com/ssFJjBXIUyZDrSYZ/arcgis/rest/services/Special_Use_Airspace/FeatureServer/0/query?where=1%3D1&outFields=NAME,TYPE_CODE,LOWER_VAL,UPPER_VAL,TIME_CODE&outSR=4326&f=geojson&resultRecordCount=500",
]

def fetch_faa_sua():
    """
    Fetch FAA Special Use Airspace (SUA) — MOAs, restricted areas,
    warning areas, prohibited areas.
    """
    print("\n[SOURCE 8] FAA Special Use Airspace (SUA)...")
    headers = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json,*/*'}
    results = []

    for url in FAA_SUA_URLS:
        try:
            r = requests.get(url, headers=headers, verify=False, timeout=20)
            if r.status_code != 200 or len(r.content) < 50:
                print(f"  X SUA {url} -> {r.status_code}")
                continue

            data = r.json()
            print(f"  -> SUA endpoint OK: {url}")

            # Handle GeoJSON FeatureCollection
            features = data.get('features', [])
            if features:
                print(f"  -> {len(features)} SUA features")
                for feat in features:
                    props = feat.get('properties', {})
                    geom  = feat.get('geometry', {})
                    name  = (props.get('NAME') or props.get('name') or
                             props.get('airspaceName') or 'SUA')
                    atype = (props.get('TYPE_CODE') or props.get('type') or
                             props.get('airspaceClass') or '')
                    full_name = f"{atype} {name}".strip() if atype else name

                    # Extract coordinates from geometry
                    carto = []
                    if geom.get('type') == 'Polygon':
                        for ring in geom.get('coordinates', []):
                            for lon, lat in ring:
                                carto.extend([lon, lat, 0])
                    elif geom.get('type') == 'MultiPolygon':
                        for poly in geom.get('coordinates', []):
                            for ring in poly:
                                for lon, lat in ring:
                                    carto.extend([lon, lat, 0])
                    elif geom.get('type') == 'Point':
                        lon, lat = geom['coordinates'][:2]
                        carto = [lon, lat, 0]

                    colors = {"poly": [180, 0, 255, 50], "line": [180, 0, 255, 255],
                              "label": "Airspace/TFR"}

                    results.append({
                        "id":          f"SUA_{hash(full_name) & 0xFFFFFF:06x}",
                        "name":        full_name,
                        "description": f"Type: {atype}\n{str(props)}",
                        "start_iso":   None,
                        "end_iso":     None,
                        "carto":       carto,
                        "colors":      colors,
                        "source":      "FAA SUA",
                        "active":      True
                    })
                break

            # Handle plain JSON array
            items = data if isinstance(data, list) else data.get('sua', data.get('results', []))
            if items:
                print(f"  -> {len(items)} SUA records")
                for item in items:
                    name  = (item.get('name') or item.get('airspaceName') or 'SUA')
                    atype = (item.get('type') or item.get('typeCode') or '')
                    text  = str(item)
                    carto = extract_coords_from_text(text)
                    colors= {"poly": [180, 0, 255, 50], "line": [180, 0, 255, 255],
                             "label": "Airspace/TFR"}
                    results.append({
                        "id":          f"SUA_{hash(name) & 0xFFFFFF:06x}",
                        "name":        f"{atype} {name}".strip(),
                        "description": text,
                        "start_iso":   None,
                        "end_iso":     None,
                        "carto":       carto,
                        "colors":      colors,
                        "source":      "FAA SUA",
                        "active":      True
                    })
                break

        except Exception as e:
            print(f"  X SUA {url} -> {type(e).__name__}: {str(e)[:60]}")

    if not results:
        print("  X All FAA SUA endpoints failed")
    else:
        print(f"  -> Total SUA records: {len(results)}")
    return results


# ─────────────────────────────────────────────
# SOURCE 9: Space-Track.org — reentry & decay predictions
# Requires free account: https://www.space-track.org/auth/createAccount
# Set env vars: SPACETRACK_USER and SPACETRACK_PASS
# or create a file data/spacetrack_creds.json: {"user":"...","pass":"..."}
# ─────────────────────────────────────────────
SPACETRACK_LOGIN  = "https://www.space-track.org/ajaxauth/login"
SPACETRACK_DECAY  = "https://www.space-track.org/basicspacedata/query/class/decay/DECAY_EPOCH/>now-30/orderby/DECAY_EPOCH%20desc/format/json"
SPACETRACK_LAUNCH = "https://www.space-track.org/basicspacedata/query/class/launch_site/format/json"

def _get_spacetrack_creds():
    """Load Space-Track credentials from env vars or creds file."""
    import os as _os
    user = _os.environ.get('SPACETRACK_USER')
    pwd  = _os.environ.get('SPACETRACK_PASS')
    if user and pwd:
        return user, pwd
    creds_path = _os.path.join(DATA_DIR, 'spacetrack_creds.json')
    if _os.path.exists(creds_path):
        try:
            with open(creds_path) as f:
                c = json.load(f)
            return c.get('user'), c.get('pass')
        except Exception:
            pass
    return None, None

def fetch_spacetrack_decay():
    """
    Fetch recent satellite reentry/decay predictions from Space-Track.org.
    These are objects predicted to reenter within 30 days — relevant for
    debris hazard zones near launch corridors and test ranges.
    """
    print("\n[SOURCE 9] Space-Track.org (reentry/decay predictions)...")
    user, pwd = _get_spacetrack_creds()
    if not user or not pwd:
        print("  X No Space-Track credentials found.")
        print("    Create data/spacetrack_creds.json: {\"user\":\"email\",\"pass\":\"password\"}")
        print("    Or set env vars SPACETRACK_USER and SPACETRACK_PASS")
        print("    Free account: https://www.space-track.org/auth/createAccount")
        return []

    results = []
    session = requests.Session()
    try:
        # Login
        login_r = session.post(SPACETRACK_LOGIN,
                               data={'identity': user, 'password': pwd},
                               verify=False, timeout=20)
        if login_r.status_code != 200 or 'Failed' in login_r.text:
            print(f"  X Space-Track login failed: {login_r.status_code}")
            return []
        print("  -> Space-Track login OK")

        # Fetch decay predictions
        r = session.get(SPACETRACK_DECAY, verify=False, timeout=30)
        r.raise_for_status()
        decays = r.json()
        print(f"  -> {len(decays)} decay predictions fetched")

        for d in decays:
            norad   = d.get('NORAD_CAT_ID', 'UNK')
            name    = d.get('OBJECT_NAME', f'Object {norad}')
            country = d.get('COUNTRY', '')
            epoch   = d.get('DECAY_EPOCH', '')
            msg     = d.get('MSG_EPOCH', '')
            lat     = d.get('LATITUDE')
            lon     = d.get('LONGITUDE')

            start_iso = parse_date_flexible(epoch or msg)
            carto = []
            if lat is not None and lon is not None:
                try:
                    carto = [float(lon), float(lat), 0]
                except (ValueError, TypeError):
                    pass

            desc = (f"NORAD ID: {norad}\nObject: {name}\nCountry: {country}\n"
                    f"Predicted Reentry: {epoch}\nMsg Epoch: {msg}")

            colors = {"poly": [0, 255, 255, 60], "line": [0, 255, 255, 255],
                      "label": "Aerospace/Missile"}

            results.append({
                "id":          f"DECAY_{norad}",
                "name":        f"REENTRY: {name}",
                "description": desc,
                "start_iso":   start_iso,
                "end_iso":     None,
                "carto":       carto,
                "colors":      colors,
                "source":      "Space-Track Decay",
                "active":      True
            })

    except Exception as e:
        print(f"  X Space-Track error: {e}")
    finally:
        try:
            session.get("https://www.space-track.org/ajaxauth/logout", timeout=5)
        except Exception:
            pass

    print(f"  -> Total decay records: {len(results)}")
    return results


# ─────────────────────────────────────────────
# MAIN: Merge all sources → CZML
# ─────────────────────────────────────────────
def generate_czml():
    all_records = []

    all_records += fetch_nga_broadcast()
    all_records += fetch_nga_hydro()
    all_records += fetch_faa_notams()
    all_records += fetch_uscg_lnm()
    all_records += parse_local_raw_notams()
    all_records += fetch_nga_asam()
    all_records += fetch_uscg_updated()
    all_records += fetch_faa_sua()
    all_records += fetch_spacetrack_decay()

    # Deduplicate by ID
    seen_ids = set()
    unique_records = []
    for rec in all_records:
        rid = rec["id"]
        if rid not in seen_ids:
            seen_ids.add(rid)
            unique_records.append(rec)

    print(f"\n{'='*50}")
    print(f"Total unique records ingested: {len(unique_records)}")
    active_count = sum(1 for r in unique_records if r["active"])
    with_coords  = sum(1 for r in unique_records if r["carto"])
    print(f"  Active now:    {active_count}")
    print(f"  With coords:   {with_coords}")
    print(f"  Without coords (text-only): {len(unique_records) - with_coords}")

    # Build CZML
    czml = [{
        "id": "document",
        "name": "Sky-Net (Lite) Live Ingest",
        "version": "1.0",
        "clock": {
            "interval": "2020-01-01T00:00:00Z/2030-12-31T23:59:59Z",
            "currentTime": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "multiplier": 1,
            "range": "UNBOUNDED",
            "step": "SYSTEM_CLOCK"
        }
    }]

    entity_count = 0
    no_coord_count = 0

    for rec in unique_records:
        if not rec["carto"]:
            no_coord_count += 1
            # Still include text-only records as a labeled point at 0,0 is bad;
            # skip rendering but keep count
            continue

        entity = build_czml_entity(
            entity_id   = rec["id"],
            name        = rec["name"],
            description = rec["description"],
            start_iso   = rec["start_iso"],
            end_iso     = rec["end_iso"],
            carto       = rec["carto"],
            colors      = rec["colors"],
            source_tag  = rec["source"],
            active_now  = rec["active"],
            sub_cartos  = rec.get("sub_cartos")
        )
        czml.append(entity)
        entity_count += 1

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(czml, f, indent=2)

    print(f"\nSUCCESS: {entity_count} entities written to {OUT_PATH}")
    print(f"  (Skipped {no_coord_count} records with no parseable coordinates)")
    print(f"  Active right now: {sum(1 for r in unique_records if r['active'] and r['carto'])}")


if __name__ == "__main__":
    generate_czml()