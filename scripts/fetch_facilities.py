# -*- coding: utf-8 -*-
"""
Fetch HIFLD Military Installations, Ranges and Training Areas
from the ArcGIS REST API and save as GeoJSON.
Run once; output is static reference data.
"""
import io, json, os, sys, time
import urllib3

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
else:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

try:
    import requests
except ImportError:
    print("ERROR: pip install requests")
    sys.exit(1)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(BASE_DIR, "data", "military_facilities.geojson")

# HIFLD Military Installations, Ranges and Training Areas — public ArcGIS layer
HIFLD_URL = (
    "https://services1.arcgis.com/Hp6G80Pky0om7QvQ/arcgis/rest/services/"
    "Military_Installations_Ranges_and_Training_Areas/FeatureServer/0/query"
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json"
}

def fetch_all_features():
    features = []
    offset   = 0
    page_size = 500
    total    = None

    while True:
        params = {
            "where":             "1=1",
            "outFields":         "SITE_NAME,COMPONENT,STATE_TERR,COUNTRY,OPER_STAT,JOINT_BASE,URL,LABEL_NAME",
            "outSR":             "4326",
            "f":                 "geojson",
            "resultOffset":      offset,
            "resultRecordCount": page_size,
            "returnGeometry":    "true",
        }
        try:
            r = requests.get(HIFLD_URL, params=params, headers=HEADERS,
                             verify=False, timeout=60)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"  X Request failed at offset {offset}: {e}")
            break

        batch = data.get("features", [])
        features.extend(batch)
        print(f"  -> Fetched {len(features)} features so far (batch={len(batch)})")

        # Stop if we got fewer than a full page
        if len(batch) < page_size:
            break

        offset += page_size
        time.sleep(0.3)   # be polite

    return features

def main():
    print("[HIFLD] Fetching Military Installations, Ranges & Training Areas...")
    features = fetch_all_features()

    if not features:
        print("ERROR: No features returned. Check network / API availability.")
        sys.exit(1)

    geojson = {
        "type": "FeatureCollection",
        "features": features
    }

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(geojson, f)

    print(f"\nSUCCESS: {len(features)} facilities saved to {OUT_PATH}")

    # Print a sample
    if features:
        p = features[0].get("properties", {})
        print("Sample:", json.dumps(p, indent=2))

if __name__ == "__main__":
    main()
