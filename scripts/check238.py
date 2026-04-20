# -*- coding: utf-8 -*-
import json, sys
if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8',errors='replace')
with open('data/closures.czml', encoding='utf-8') as f:
    czml = json.load(f)
for e in czml:
    if not isinstance(e, dict): continue
    name = str(e.get('name',''))
    eid  = str(e.get('id',''))
    if '238' in name or '238' in eid:
        poly = e.get('polygon',{})
        pts  = poly.get('positions',{}).get('cartographicDegrees',[]) if poly else []
        pl   = e.get('polyline',{})
        lpts = pl.get('positions',{}).get('cartographicDegrees',[]) if pl else []
        print("ID:", eid[:80])
        print("Name:", name)
        print("Poly pts:", len(pts)//3)
        print("Line pts:", len(lpts)//3)
        if pts:
            coords = [(pts[i],pts[i+1]) for i in range(0,len(pts)-2,3)]
            print("Coords:", coords)
        print()
print("Done")
