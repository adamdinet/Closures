# -*- coding: utf-8 -*-
"""Rebuild notam_forecast.html from existing CSVs + updated template."""
import csv, json, os, sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE_DIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR  = os.path.join(BASE_DIR, "data")
HTML_OUT  = os.path.join(DATA_DIR, "notam_forecast.html")
TMPL_PATH = os.path.join(BASE_DIR, "scripts", "notam_forecast_template.html")

results = {}
for label in ["24h", "72h", "96h"]:
    path = os.path.join(DATA_DIR, "notam_forecast_%s.csv" % label)
    with open(path, newline="", encoding="utf-8") as f:
        results[label] = list(csv.DictReader(f))
    print("[+] Loaded %s: %d rows" % (label, len(results[label])))

now_iso   = "2026-04-20T16:12Z"
data_json = json.dumps(results)

with open(TMPL_PATH, encoding="utf-8") as f:
    html = f.read()

html = html.replace("__DATA_JSON__", data_json).replace("__NOW_ISO__", now_iso)

with open(HTML_OUT, "w", encoding="utf-8") as f:
    f.write(html)

size_mb = os.path.getsize(HTML_OUT) / 1024 / 1024
print("[+] HTML written: %.2f MB -> %s" % (size_mb, HTML_OUT))
