# -*- coding: utf-8 -*-
"""
TLE Bulk Parser
===============
Parses data/tle_bulk.txt (3LE format from Space-Track.org) into
data/tle_bulk.json — a structured JSON array for use by the frontend
and other scripts.

Each entry:
  {
    "name":        "STARLINK-1234",
    "norad_id":    12345,
    "line1":       "1 12345U ...",
    "line2":       "2 12345 ...",
    "epoch":       "2026-04-20T03:35:47Z",
    "inclination": 53.0,
    "eccentricity":0.0001,
    "mean_motion": 15.05,
    "altitude_km": 550.0   (estimated from mean motion)
  }

Usage:
    python scripts/parse_tle_bulk.py
"""

import json, os, math

BASE_DIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR  = os.path.join(BASE_DIR, "data")
TLE_TXT   = os.path.join(DATA_DIR, "tle_bulk.txt")
TLE_JSON  = os.path.join(DATA_DIR, "tle_bulk.json")

# Earth gravitational parameter (km^3/s^2) and radius (km)
GM    = 398600.4418
RE    = 6371.0


def epoch_from_tle(epoch_str):
    """Convert TLE epoch field (YYDDD.DDDDDDDD) to ISO-8601 UTC string."""
    try:
        year2 = int(epoch_str[:2])
        year  = 2000 + year2 if year2 < 57 else 1900 + year2
        day_of_year = float(epoch_str[2:])
        doy_int  = int(day_of_year)
        frac_day = day_of_year - doy_int
        # Build date from day-of-year
        import datetime
        base = datetime.datetime(year, 1, 1) + datetime.timedelta(days=doy_int - 1)
        total_seconds = frac_day * 86400
        base += datetime.timedelta(seconds=total_seconds)
        return base.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return None


def mean_motion_to_altitude(mean_motion_rev_per_day):
    """Estimate altitude (km) from mean motion (revolutions/day)."""
    try:
        n = mean_motion_rev_per_day * 2 * math.pi / 86400  # rad/s
        a = (GM / (n * n)) ** (1.0 / 3.0)                  # semi-major axis km
        return round(a - RE, 1)
    except Exception:
        return None


def parse_tle_file(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        raw = f.read().splitlines()

    satellites = []
    i = 0
    while i < len(raw) - 2:
        name_line = raw[i].strip()
        l1        = raw[i + 1].strip()
        l2        = raw[i + 2].strip()

        # Validate: line1 starts with "1 " and line2 starts with "2 "
        if l1.startswith("1 ") and l2.startswith("2 "):
            # Strip leading "0 " from name line if present (Space-Track 3LE format)
            name = name_line.lstrip("0 ").strip()

            try:
                norad_id    = int(l1[2:7])
                epoch_str   = l1[18:32].strip()
                inclination = float(l2[8:16].strip())
                ecc_str     = l2[26:33].strip()
                eccentricity= float("0." + ecc_str)
                mean_motion = float(l2[52:63].strip())
                altitude    = mean_motion_to_altitude(mean_motion)
                epoch_iso   = epoch_from_tle(epoch_str)
            except Exception:
                i += 3
                continue

            satellites.append({
                "name":         name,
                "norad_id":     norad_id,
                "line1":        l1,
                "line2":        l2,
                "epoch":        epoch_iso,
                "inclination":  inclination,
                "eccentricity": eccentricity,
                "mean_motion":  mean_motion,
                "altitude_km":  altitude,
            })
            i += 3
        else:
            i += 1  # re-sync if misaligned

    return satellites


def main():
    print(f"[*] Parsing {TLE_TXT} ...")
    sats = parse_tle_file(TLE_TXT)
    print(f"[+] Parsed {len(sats)} satellites.")

    with open(TLE_JSON, "w", encoding="utf-8") as f:
        json.dump(sats, f, indent=2)
    print(f"[+] Saved -> {TLE_JSON}")

    # Quick stats
    altitudes = [s["altitude_km"] for s in sats if s["altitude_km"] is not None]
    leo  = sum(1 for a in altitudes if a < 2000)
    meo  = sum(1 for a in altitudes if 2000 <= a < 35000)
    geo  = sum(1 for a in altitudes if 35000 <= a < 36500)
    heo  = sum(1 for a in altitudes if a >= 36500)
    print(f"\n    LEO (<2000 km)   : {leo:>6}")
    print(f"    MEO (2000-35000) : {meo:>6}")
    print(f"    GEO (~35786 km)  : {geo:>6}")
    print(f"    HEO (>36500 km)  : {heo:>6}")
    print(f"    Total            : {len(sats):>6}")


if __name__ == "__main__":
    main()
