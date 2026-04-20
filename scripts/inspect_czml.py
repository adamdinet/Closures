# -*- coding: utf-8 -*-
"""Inspect closures.czml for polygon issues."""
import json, os, sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(BASE_DIR, 'data', 'closures.czml'), encoding='utf-8') as f:
    czml = json.load(f)

print("Total CZML entities:", len(czml))
poly_count = 0
issues = []

for e in czml:
    if not isinstance(e, dict):
        continue
    poly = e.get('polygon', {})
    if not poly:
        continue
    poly_count += 1
    pts = poly.get('positions', {}).get('cartographicDegrees', [])
    n = len(pts) // 3

    # Also check polyline
    pl = e.get('polyline', {})
    pl_pts = pl.get('positions', {}).get('cartographicDegrees', []) if pl else []
    pl_n = len(pl_pts) // 3

    name = (e.get('name') or e.get('id') or '?')[:70]
    if n > 6 or pl_n > 8:
        print("  [%d poly pts / %d line pts] %s" % (n, pl_n, name))
        if n > 6:
            coords = [(pts[i], pts[i+1]) for i in range(0, len(pts)-2, 3)]
            print("    Poly coords:", coords)

print("\nTotal polygon entities:", poly_count)
