# -*- coding: utf-8 -*-
"""
Bulk TLE Downloader — Space-Track.org
======================================
Downloads the full active satellite TLE catalog from Space-Track.org
using credentials stored in data/spacetrack_creds.json.

Outputs:
  data/tle_bulk.txt  — 3LE format (name + 2 TLE lines) for all active objects
  data/tle_bulk.json — same data as structured JSON

Usage:
    python scripts/fetch_tle_bulk.py

Requires:
    pip install requests
"""

import json
import os
import sys
import requests

BASE_DIR   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR   = os.path.join(BASE_DIR, "data")
CREDS_PATH = os.path.join(DATA_DIR, "spacetrack_creds.json")
TLE_TXT    = os.path.join(DATA_DIR, "tle_bulk.txt")
TLE_JSON   = os.path.join(DATA_DIR, "tle_bulk.json")

LOGIN_URL  = "https://www.space-track.org/ajaxauth/login"
BULK_URL   = (
    "https://www.space-track.org/basicspacedata/query"
    "/class/gp/decay_date/null-val/epoch/%3Enow-30"
    "/orderby/norad_cat_id/format/3le"
)
BULK_JSON_URL = (
    "https://www.space-track.org/basicspacedata/query"
    "/class/gp/decay_date/null-val/epoch/%3Enow-30"
    "/orderby/norad_cat_id/format/json"
)


def load_creds():
    with open(CREDS_PATH, "r") as f:
        creds = json.load(f)
    return creds["user"], creds["pass"]


def main():
    print("[*] Loading credentials...")
    username, password = load_creds()

    session = requests.Session()

    print("[*] Logging in to Space-Track.org...")
    resp = session.post(LOGIN_URL, data={"identity": username, "password": password})
    # Space-Track returns HTTP 200 for both success and failure.
    # A failed login returns a JSON body like {"Login": "Failed"}.
    # A successful login returns an empty body or non-JSON content.
    try:
        body = resp.json()
        if isinstance(body, dict) and body.get("Login") == "Failed":
            print(f"[!] Login failed — bad credentials.")
            sys.exit(1)
    except Exception:
        pass  # non-JSON response means login succeeded
    if resp.status_code != 200:
        print(f"[!] Unexpected HTTP {resp.status_code}")
        sys.exit(1)
    print("[+] Login successful.")

    # ── Download 3LE text format ──────────────────────────────────────────────
    print("[*] Downloading bulk TLE data (3LE format)...")
    resp = session.get(BULK_URL)
    if resp.status_code != 200:
        print(f"[!] TLE download failed (HTTP {resp.status_code})")
        sys.exit(1)

    tle_text = resp.text
    line_count = tle_text.strip().count("\n") + 1
    sat_count  = line_count // 3
    print(f"[+] Downloaded {sat_count} satellites ({line_count} lines).")

    with open(TLE_TXT, "w", encoding="utf-8") as f:
        f.write(tle_text)
    print(f"[+] Saved 3LE text → {TLE_TXT}")

    # ── Download JSON format ──────────────────────────────────────────────────
    print("[*] Downloading bulk TLE data (JSON format)...")
    resp = session.get(BULK_JSON_URL)
    if resp.status_code != 200:
        print(f"[!] JSON download failed (HTTP {resp.status_code})")
        sys.exit(1)

    catalog = resp.json()
    print(f"[+] JSON catalog contains {len(catalog)} entries.")

    with open(TLE_JSON, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2)
    print(f"[+] Saved JSON catalog → {TLE_JSON}")

    print("\n[✓] Bulk TLE download complete.")
    print(f"    3LE file : {TLE_TXT}")
    print(f"    JSON file: {TLE_JSON}")


if __name__ == "__main__":
    main()
