# -*- coding: utf-8 -*-
"""Debug sub-area splitting for NAVAREA XII 238/26."""
import re, sys, os
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# Inline the functions from grab_notams.py
def dm_to_dd(coord_str):
    m = re.match(r'(\d+)-(\d+(?:\.\d+)?)([NSEW])', coord_str.strip())
    if not m: return None
    val = float(m.group(1)) + float(m.group(2)) / 60.0
    if m.group(3) in ('S', 'W'): val = -val
    return val

def extract_coords_from_text(text):
    pattern = re.compile(r"(\d{1,2}-\d{2}(?:\.\d+)?[NS])\s+(\d{1,3}-\d{2}(?:\.\d+)?[WE])")
    pairs = pattern.findall(text)
    carto = []
    for lat_s, lon_s in pairs:
        lat = dm_to_dd(lat_s); lon = dm_to_dd(lon_s)
        if lat is not None and lon is not None:
            carto.extend([lon, lat, 0])
    return carto

def _carto_is_global(carto):
    if len(carto) < 6: return False
    lats = [carto[i+1] for i in range(0, len(carto)-2, 3)]
    lons = [carto[i]   for i in range(0, len(carto)-2, 3)]
    return (max(lats)-min(lats)) > 140.0 or (max(lons)-min(lons)) > 330.0

def extract_sub_area_cartos(text):
    coord_re = re.compile(r"(\d{1,2}-\d{2}(?:\.\d+)?[NS])\s+(\d{1,3}-\d{2}(?:\.\d+)?[WE])")
    sub_re = re.compile(r'(?m)^\s{2,}([A-Z])\.\s+')
    parts = sub_re.split(text)
    print("  Parts count:", len(parts))

    if len(parts) <= 2:
        all_carto = extract_coords_from_text(text)
        return [all_carto] if (all_carto and not _carto_is_global(all_carto)) else []

    result = []
    i = 2
    while i < len(parts):
        block = parts[i]
        pairs = coord_re.findall(block)
        carto = []
        for lat_s, lon_s in pairs:
            lat = dm_to_dd(lat_s); lon = dm_to_dd(lon_s)
            if lat is not None and lon is not None:
                carto.extend([lon, lat, 0])
        print(f"  Block[{i}] -> {len(carto)//3} pts: {[(carto[j],carto[j+1]) for j in range(0,len(carto)-2,3)]}")
        if len(carto) >= 6 and not _carto_is_global(carto):
            result.append(carto)
        i += 2

    if not result:
        all_carto = extract_coords_from_text(text)
        return [all_carto] if (all_carto and not _carto_is_global(all_carto)) else []
    return result

# Read the raw block
with open('data/raw_notams.txt', encoding='utf-8', errors='replace') as f:
    text = f.read()

start = text.find('NAVAREA XII 238/26')
end   = text.find('\n030926Z', start)
block = text[start:end]

print("=== extract_sub_area_cartos result ===")
result = extract_sub_area_cartos(block)
print(f"Sub-areas returned: {len(result)}")
for i, sc in enumerate(result):
    pts = [(sc[j], sc[j+1]) for j in range(0, len(sc)-2, 3)]
    print(f"  Sub-area {i}: {len(pts)} pts -> {pts}")
