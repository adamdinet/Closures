# -*- coding: utf-8 -*-
"""
Sky-Net (Lite) — Metocean Go/No-Go Engine
==========================================
Reads closures.czml, computes the centroid of each active closure,
queries OpenMeteo (free, no API key) for current weather conditions,
then scores each closure with a Go/No-Go probability for military activity.

Outputs:
  data/weather.json  — machine-readable weather + probability per closure

Usage:
    python scripts/fetch_weather.py

Run after grab_notams.py:
    python scripts/grab_notams.py && python scripts/fetch_weather.py

OpenMeteo API: https://open-meteo.com/  (free, no key, no rate limit for small use)
"""

import io, json, math, os, sys, time
from datetime import datetime, timezone

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
else:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

try:
    import requests
except ImportError:
    print("ERROR: pip install requests")
    sys.exit(1)

BASE_DIR    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR    = os.path.join(BASE_DIR, "data")
CZML_PATH   = os.path.join(DATA_DIR, "closures.czml")
WEATHER_OUT = os.path.join(DATA_DIR, "weather.json")

OPENMETEO_URL = "https://api.open-meteo.com/v1/forecast"

# ── Go/No-Go thresholds ────────────────────────────────────────────────────
# These are approximate military-grade thresholds for common exercise types.
# Adjust as needed for specific mission profiles.

THRESHOLDS = {
    # Sea state (wave height in metres)
    "wave_height_hold":    3.0,   # > 3m = likely weather hold (Sea State 5+)
    "wave_height_caution": 1.5,   # 1.5-3m = degraded ops

    # Wind speed (knots)
    "wind_hold":    35,   # > 35kn = likely hold
    "wind_caution": 20,   # 20-35kn = degraded

    # Cloud cover (%)
    "cloud_hold":    90,  # > 90% = likely hold for air ops
    "cloud_caution": 60,  # 60-90% = degraded air ops

    # Precipitation (mm/hr)
    "precip_hold":    5.0,  # > 5mm/hr = hold
    "precip_caution": 1.0,  # 1-5mm/hr = caution
}


# ── Helpers ────────────────────────────────────────────────────────────────

def get_centroid(entity):
    """
    Extract centroid [lon, lat] from a CZML entity dict.
    Handles polyline, polygon, and point geometries.
    """
    # Polyline
    pl = entity.get("polyline", {})
    if pl:
        pos = pl.get("positions", {}).get("cartographicDegrees", [])
        if len(pos) >= 3:
            lons = [pos[i]   for i in range(0, len(pos), 3)]
            lats = [pos[i+1] for i in range(0, len(pos), 3)]
            return [sum(lons)/len(lons), sum(lats)/len(lats)]

    # Polygon
    pg = entity.get("polygon", {})
    if pg:
        pos = pg.get("positions", {}).get("cartographicDegrees", [])
        if len(pos) >= 3:
            lons = [pos[i]   for i in range(0, len(pos), 3)]
            lats = [pos[i+1] for i in range(0, len(pos), 3)]
            return [sum(lons)/len(lons), sum(lats)/len(lats)]

    # Point
    pt = entity.get("position", {})
    if pt:
        coords = pt.get("cartographicDegrees", [])
        if len(coords) >= 2:
            return [coords[0], coords[1]]

    return None


def score_go_nogo(weather):
    """
    Compute a Go/No-Go probability score (0-100) and status label.

    Returns dict:
        score      : int 0-100 (100 = full GO, 0 = definite HOLD)
        status     : "GO" | "CAUTION" | "HOLD" | "WEATHER HOLD"
        color      : hex color for UI
        reasons    : list of strings explaining deductions
    """
    score   = 100
    reasons = []
    t = THRESHOLDS

    wave   = weather.get("wave_height")
    wind   = weather.get("wind_speed_kn")
    cloud  = weather.get("cloud_cover_pct")
    precip = weather.get("precipitation_mm")

    if wave is not None:
        if wave > t["wave_height_hold"]:
            score -= 40
            reasons.append(f"Wave height {wave:.1f}m (>{t['wave_height_hold']}m HOLD)")
        elif wave > t["wave_height_caution"]:
            score -= 20
            reasons.append(f"Wave height {wave:.1f}m (caution)")

    if wind is not None:
        if wind > t["wind_hold"]:
            score -= 30
            reasons.append(f"Wind {wind:.0f}kn (>{t['wind_hold']}kn HOLD)")
        elif wind > t["wind_caution"]:
            score -= 15
            reasons.append(f"Wind {wind:.0f}kn (caution)")

    if cloud is not None:
        if cloud > t["cloud_hold"]:
            score -= 20
            reasons.append(f"Cloud cover {cloud:.0f}% (>{t['cloud_hold']}% HOLD for air ops)")
        elif cloud > t["cloud_caution"]:
            score -= 10
            reasons.append(f"Cloud cover {cloud:.0f}% (caution)")

    if precip is not None:
        if precip > t["precip_hold"]:
            score -= 20
            reasons.append(f"Precipitation {precip:.1f}mm/hr (>{t['precip_hold']}mm HOLD)")
        elif precip > t["precip_caution"]:
            score -= 10
            reasons.append(f"Precipitation {precip:.1f}mm/hr (caution)")

    score = max(0, min(100, score))

    if score >= 80:
        status = "GO"
        color  = "#44ff88"
    elif score >= 50:
        status = "CAUTION"
        color  = "#ffcc00"
    elif score >= 20:
        status = "DEGRADED"
        color  = "#ff8800"
    else:
        status = "WEATHER HOLD"
        color  = "#ff4444"

    return {"score": score, "status": status, "color": color, "reasons": reasons}


def fetch_weather_for_point(lon, lat, session):
    """
    Query OpenMeteo for current conditions at a lat/lon point.
    Returns a dict of weather values, or None on failure.
    """
    params = {
        "latitude":       round(lat, 4),
        "longitude":      round(lon, 4),
        "current":        "temperature_2m,wind_speed_10m,cloud_cover,precipitation,weather_code",
        "hourly":         "wave_height,wind_speed_10m",
        "wind_speed_unit":"kn",
        "forecast_days":  1,
        "timezone":       "UTC"
    }
    try:
        r = session.get(OPENMETEO_URL, params=params, timeout=10)
        if r.status_code != 200:
            return None
        d = r.json()

        current = d.get("current", {})
        hourly  = d.get("hourly", {})

        # Get current wave height from first hourly value (OpenMeteo puts
        # wave_height in hourly, not current)
        wave_heights = hourly.get("wave_height", [])
        wave = wave_heights[0] if wave_heights else None

        return {
            "temperature_c":      current.get("temperature_2m"),
            "wind_speed_kn":      current.get("wind_speed_10m"),
            "cloud_cover_pct":    current.get("cloud_cover"),
            "precipitation_mm":   current.get("precipitation"),
            "weather_code":       current.get("weather_code"),
            "wave_height":        wave,
            "fetched_at":         datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        }
    except Exception:
        return None


# ── WMO weather code descriptions ─────────────────────────────────────────
WMO_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Icy fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
    77: "Snow grains",
    80: "Slight showers", 81: "Moderate showers", 82: "Violent showers",
    85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm w/ hail", 99: "Thunderstorm w/ heavy hail"
}


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    run_time = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"Sky-Net (Lite) Metocean Go/No-Go Engine — {run_time}")

    if not os.path.exists(CZML_PATH):
        print(f"ERROR: {CZML_PATH} not found. Run grab_notams.py first.")
        sys.exit(1)

    with open(CZML_PATH, "r", encoding="utf-8", errors="replace") as f:
        czml = json.load(f)

    entities = [e for e in czml if isinstance(e, dict) and e.get("id") != "document"]
    print(f"Loaded {len(entities)} entities from closures.czml")

    # Only fetch weather for active entities with geometry
    active = []
    for e in entities:
        props = e.get("properties", {})
        active_val = props.get("active")
        if isinstance(active_val, dict):
            active_val = active_val.get("boolean", True)
        if active_val is False:
            continue
        centroid = get_centroid(e)
        if centroid:
            active.append((e, centroid))

    print(f"Active entities with geometry: {len(active)}")

    # Deduplicate by rounded centroid to avoid hammering the API
    # (many NAVAREA records share the same general area)
    seen_cells = {}
    deduped = []
    for entity, (lon, lat) in active:
        cell = (round(lon, 1), round(lat, 1))  # 0.1° grid cell ≈ 6nm
        if cell not in seen_cells:
            seen_cells[cell] = True
            deduped.append((entity, lon, lat))

    print(f"Unique weather query points (0.1deg grid): {len(deduped)}")
    print("Fetching weather from OpenMeteo...")

    session  = requests.Session()
    results  = {}
    ok_count = 0
    fail_count = 0

    for i, (entity, lon, lat) in enumerate(deduped):
        eid = entity.get("id", f"entity_{i}")
        w = fetch_weather_for_point(lon, lat, session)
        if w:
            go_nogo = score_go_nogo(w)
            w["go_nogo"] = go_nogo
            w["weather_desc"] = WMO_CODES.get(w.get("weather_code"), "Unknown")
            results[eid] = {
                "id":       eid,
                "name":     entity.get("name", eid),
                "lon":      lon,
                "lat":      lat,
                "weather":  w,
                "go_nogo":  go_nogo
            }
            ok_count += 1
            if (i + 1) % 20 == 0:
                print(f"  ... {i+1}/{len(deduped)} fetched")
        else:
            fail_count += 1

        # Polite rate limiting — OpenMeteo allows ~10k/day free
        time.sleep(0.05)

    print(f"\nFetched: {ok_count} OK, {fail_count} failed")

    # Summary stats
    statuses = {}
    for r in results.values():
        s = r["go_nogo"]["status"]
        statuses[s] = statuses.get(s, 0) + 1
    print("Go/No-Go summary:")
    for status, count in sorted(statuses.items()):
        print(f"  {status:15s}: {count}")

    # Write output
    output = {
        "generated":    run_time,
        "total_queried": ok_count,
        "summary":      statuses,
        "closures":     results
    }
    with open(WEATHER_OUT, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    print(f"\nWeather data written to {WEATHER_OUT}")


if __name__ == "__main__":
    main()
