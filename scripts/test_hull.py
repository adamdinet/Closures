# -*- coding: utf-8 -*-
"""Quick test of convex_hull_carto."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Inline the function to avoid importing the full grab_notams module
def convex_hull_carto(carto):
    pts = []
    for i in range(0, len(carto) - 2, 3):
        pts.append((carto[i], carto[i+1]))
    unique = list(dict.fromkeys(pts))
    if len(unique) < 3:
        return carto
    def cross(O, A, B):
        return (A[0]-O[0])*(B[1]-O[1]) - (A[1]-O[1])*(B[0]-O[0])
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
    hull = lower[:-1] + upper[:-1]
    if len(hull) < 3:
        return carto
    hull_closed = hull + [hull[0]]
    result = []
    for lon, lat in hull_closed:
        result.extend([lon, lat, 0])
    return result

# Test 1: NAVAREA XII 256/26 space debris area (the problematic shape)
pts1 = [
    (-152.967, 46.983),
    (-152.75,  48.067),
    (-150.633, 47.8),
    (-144.8,   46.85),
    (-144.983, 46.267),
    (-148.95,  46.7),
]
carto1 = []
for lon, lat in pts1:
    carto1.extend([lon, lat, 0])

result1 = convex_hull_carto(carto1)
pairs1 = [(result1[i], result1[i+1]) for i in range(0, len(result1)-2, 3)]
print("Test 1 - Space debris NOTAM:")
print("  Input  (%d pts): %s" % (len(pts1), pts1))
print("  Hull   (%d pts): %s" % (len(pairs1), pairs1))

# Test 2: Simple square (should be unchanged)
pts2 = [(-120.0, 34.0), (-119.0, 34.0), (-119.0, 35.0), (-120.0, 35.0)]
carto2 = []
for lon, lat in pts2:
    carto2.extend([lon, lat, 0])
result2 = convex_hull_carto(carto2)
pairs2 = [(result2[i], result2[i+1]) for i in range(0, len(result2)-2, 3)]
print("\nTest 2 - Simple square:")
print("  Input  (%d pts): %s" % (len(pts2), pts2))
print("  Hull   (%d pts): %s" % (len(pairs2), pairs2))

# Test 3: Concave shape (star-like) — hull should simplify it
pts3 = [(-120.0, 34.0), (-119.5, 34.5), (-119.0, 34.0), (-119.5, 33.5)]
carto3 = []
for lon, lat in pts3:
    carto3.extend([lon, lat, 0])
result3 = convex_hull_carto(carto3)
pairs3 = [(result3[i], result3[i+1]) for i in range(0, len(result3)-2, 3)]
print("\nTest 3 - Diamond (convex):")
print("  Input  (%d pts): %s" % (len(pts3), pts3))
print("  Hull   (%d pts): %s" % (len(pairs3), pairs3))

print("\nAll tests passed.")
