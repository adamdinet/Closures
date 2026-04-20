# -*- coding: utf-8 -*-
"""
Build a compact TLE index for browser use.
Reads data/tle_bulk.txt and writes data/tle_index.json:
  { "NORAD_ID": ["line1", "line2"], ... }

This is ~3 MB (vs 11.5 MB for tle_bulk.json) and loads fast in the browser.

Usage:
    python scripts/build_tle_index.py
"""
import json, os, sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
TLE_TXT  = os.path.join(DATA_DIR, "tle_bulk.txt")
IDX_OUT  = os.path.join(DATA_DIR, "tle_index.json")

with open(TLE_TXT, 'r', encoding='utf-8', errors='replace') as f:
    raw = f.read().splitlines()

index = {}
i = 0
while i < len(raw) - 2:
    l1 = raw[i+1].strip()
    l2 = raw[i+2].strip()
    if l1.startswith('1 ') and l2.startswith('2 '):
        norad = l1[2:7].strip().lstrip('0') or '0'
        index[norad] = [l1, l2]
        i += 3
    else:
        i += 1

with open(IDX_OUT, 'w', encoding='utf-8') as f:
    json.dump(index, f, separators=(',', ':'))

size_mb = os.path.getsize(IDX_OUT) / 1024 / 1024
print("[+] TLE index: %d satellites -> %.1f MB -> %s" % (len(index), size_mb, IDX_OUT))
