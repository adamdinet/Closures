# Sky-Net (Lite)

**Unified Multi-Domain Intelligence Grid** — a real-time, browser-based Common Operating Picture (COP) that fuses airspace, maritime, space, and ground-domain threat data onto a single 3-D globe.

---

## Capabilities

| Domain | What it shows |
|---|---|
| **Airspace / TFR** | FAA NOTAMs, ICAO aeronautical warnings, Temporary Flight Restrictions |
| **Maritime** | NGA MSI NAVAREA broadcast warnings, USCG Local Notice to Mariners, Hydrolant/Hydropac/NAVTEX |
| **Live Fire / Ordnance** | Active weapons ranges, ordnance disposal, missile test corridors |
| **Subsea / Cable / Survey** | Submarine cable operations, hydrographic surveys, offshore activity |
| **Space / Aerospace** | Launch corridors, rocket range closures, space debris warnings |
| **GEO Orbital COP** | Full geostationary belt plotted at 35,786 km — payloads, debris, rocket bodies color-coded by type; click any object for full Space-Track intel |
| **Military Facilities** | 133+ US military installations overlaid with component color-coding (USAF, USN, USA, USMC, USSF, NASA) |
| **Proximity Alerts** | Automated detection of active closures within configurable radius of military facilities |
| **Metocean Go/No-Go** | Per-closure weather assessment (wind, wave height, cloud cover, temperature) scored as GO / CAUTION / DEGRADED / WEATHER HOLD |
| **Multi-Domain Intersection** | Automatic detection and highlighting of overlapping airspace + maritime + aerospace closures |
| **Intel Tag Filter** | Filter all entities by threat tag: UAS · LIVE WEAPONS · SUBMARINE · MISSILE · SPACE DEBRIS · NUCLEAR · EXERCISE · LASER · CYBER/EMP · MODU/DRILLING |

---

## Local Network Access

The system is served on the local network at:

```
http://10.1.32.42:8000
```

To start the server, run [`start_server.bat`](start_server.bat) or:

```bash
python -m http.server 8000
```

---

## Data Sources

### Airspace & Maritime (auto-fetched by `scripts/grab_notams.py`)

| Source | Type | Auth |
|---|---|---|
| **FAA NOTAM Search API** | Aeronautical NOTAMs (US domestic + ICAO) | Public — no key required |
| **NGA MSI Broadcast Warnings** | NAVAREA maritime warnings | Public — no key required |
| **USCG Local Notice to Mariners** | Coastal/inland waterway warnings | Public RSS/JSON — no key required |
| **NGA MSI Hydrolant / Hydropac / NAVTEX** | Oceanic text warnings | Public — no key required |

### Space / Orbital (manual export from Space-Track.org)

| Source | File | Notes |
|---|---|---|
| **Space-Track.org** — Box Score | `data/box_score.txt` | Country orbital inventory (payload / debris / rocket body counts) |
| **Space-Track.org** — GEO Report | `data/geo_report.txt` | GEO belt catalog; longitude estimated from COMMENTCODE field |

Credentials for Space-Track are stored in [`data/spacetrack_creds.json`](data/spacetrack_creds.json) (not committed to version control).

### Weather (auto-fetched by `scripts/fetch_weather.py`)

| Source | Notes |
|---|---|
| **Open-Meteo** (`open-meteo.com`) | Free, no API key, no rate limit for small use. Provides wind speed, wave height, cloud cover, temperature per closure centroid. |

### Military Facilities

| Source | File | Notes |
|---|---|---|
| **Custom curated dataset** | `data/military_facilities.geojson` | 133+ US military installations with component, type, and location |

---

## Scripts

| Script | Purpose |
|---|---|
| [`scripts/grab_notams.py`](scripts/grab_notams.py) | Fetch all airspace/maritime warnings → `data/closures.czml` |
| [`scripts/parse_orbital.py`](scripts/parse_orbital.py) | Parse Space-Track exports → `data/geo_catalog.json`, `data/box_score.json` |
| [`scripts/fetch_weather.py`](scripts/fetch_weather.py) | Score closures with Go/No-Go weather assessment → `data/weather.json` |
| [`scripts/proximity_alert.py`](scripts/proximity_alert.py) | Detect closures near military facilities → `data/proximity_alerts.json` |
| [`scripts/fetch_facilities.py`](scripts/fetch_facilities.py) | Update/rebuild the military facilities GeoJSON |
| [`scripts/delta_report.py`](scripts/delta_report.py) | Generate a delta report of new/expired closures |

---

## Recommended Run Order

```bash
python scripts/grab_notams.py
python scripts/parse_orbital.py
python scripts/fetch_weather.py
python scripts/proximity_alert.py
```

Then open `http://10.1.32.42:8000` in a browser.

---

*Sky-Net (Lite) — Unclassified / For Authorized Use*
