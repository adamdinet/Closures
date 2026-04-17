# -*- coding: utf-8 -*-
"""
Sky-Net (Lite) Delta Report
============================
Compares the current closures.czml against a saved snapshot from the
previous run and produces a human-readable HTML + plain-text diff report.

Usage:
    python scripts/delta_report.py

Workflow:
    1. On first run: saves a snapshot of closures.czml → data/snapshot_prev.czml
       and exits (nothing to diff yet).
    2. On subsequent runs: diffs current vs snapshot, writes report to
       data/delta_report.html and data/delta_report.txt, then updates snapshot.

Schedule this with Windows Task Scheduler or a cron job to run after
grab_notams.py completes.
"""

import io, json, os, sys
from datetime import datetime, timezone

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
else:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

BASE_DIR     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR     = os.path.join(BASE_DIR, "data")
CURRENT_PATH = os.path.join(DATA_DIR, "closures.czml")
SNAPSHOT_PATH= os.path.join(DATA_DIR, "snapshot_prev.czml")
HTML_REPORT  = os.path.join(DATA_DIR, "delta_report.html")
TXT_REPORT   = os.path.join(DATA_DIR, "delta_report.txt")


def load_czml(path):
    """Load CZML and return a dict of {entity_id: entity_dict} (excludes document)."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        data = json.load(f)
    return {
        e["id"]: e
        for e in data
        if isinstance(e, dict) and e.get("id") != "document"
    }


def get_prop(entity, key):
    """Safely get a property value from a CZML entity dict."""
    props = entity.get("properties", {})
    val = props.get(key)
    if isinstance(val, dict):
        return val.get("string") or val.get("boolean") or val.get("number") or str(val)
    return val


def entity_summary(entity):
    """Return a short summary string for an entity."""
    name   = entity.get("name", entity.get("id", "?"))
    source = get_prop(entity, "source") or "?"
    start  = get_prop(entity, "timestamp") or "?"
    end    = get_prop(entity, "end_time") or "open"
    tags   = get_prop(entity, "tags") or ""
    avail  = entity.get("availability", "")
    return f"{name} | {source} | {start} → {end} | tags: {tags} | avail: {avail}"


def diff_czml(prev, curr):
    """
    Compare two entity dicts.
    Returns:
        new_ids      — IDs present in curr but not prev
        removed_ids  — IDs present in prev but not curr
        changed_ids  — IDs in both but with different availability/tags/active
    """
    prev_ids = set(prev.keys())
    curr_ids = set(curr.keys())

    new_ids     = curr_ids - prev_ids
    removed_ids = prev_ids - curr_ids
    common_ids  = prev_ids & curr_ids

    changed_ids = []
    for eid in common_ids:
        p = prev[eid]
        c = curr[eid]
        # Compare availability, tags, active status
        if (p.get("availability") != c.get("availability") or
                get_prop(p, "tags")   != get_prop(c, "tags")   or
                get_prop(p, "active") != get_prop(c, "active")):
            changed_ids.append(eid)

    return sorted(new_ids), sorted(removed_ids), sorted(changed_ids)


def write_txt_report(new_ids, removed_ids, changed_ids, prev, curr, run_time):
    lines = [
        "=" * 60,
        f"Sky-Net (Lite) Delta Report — {run_time}",
        "=" * 60,
        f"NEW closures:     {len(new_ids)}",
        f"REMOVED closures: {len(removed_ids)}",
        f"CHANGED closures: {len(changed_ids)}",
        "",
    ]

    if new_ids:
        lines.append("── NEW ──────────────────────────────────────────────")
        for eid in new_ids:
            lines.append(f"  + {entity_summary(curr[eid])}")
        lines.append("")

    if removed_ids:
        lines.append("── REMOVED / CANCELLED ──────────────────────────────")
        for eid in removed_ids:
            lines.append(f"  - {entity_summary(prev[eid])}")
        lines.append("")

    if changed_ids:
        lines.append("── CHANGED ──────────────────────────────────────────")
        for eid in changed_ids:
            p_avail = prev[eid].get("availability", "?")
            c_avail = curr[eid].get("availability", "?")
            name    = curr[eid].get("name", eid)
            lines.append(f"  ~ {name}")
            if p_avail != c_avail:
                lines.append(f"      avail: {p_avail}")
                lines.append(f"          -> {c_avail}")
        lines.append("")

    if not new_ids and not removed_ids and not changed_ids:
        lines.append("No changes detected since last run.")

    lines.append("=" * 60)
    report = "\n".join(lines)

    with open(TXT_REPORT, "w", encoding="utf-8") as f:
        f.write(report)
    return report


def write_html_report(new_ids, removed_ids, changed_ids, prev, curr, run_time):
    def row(color, symbol, summary):
        return (f'<tr style="color:{color}">'
                f'<td style="padding:3px 8px;font-weight:bold">{symbol}</td>'
                f'<td style="padding:3px 8px;font-size:0.85em">{summary}</td></tr>')

    rows = []
    for eid in new_ids:
        rows.append(row("#44ff88", "NEW", entity_summary(curr[eid])))
    for eid in removed_ids:
        rows.append(row("#ff6666", "CANCELLED", entity_summary(prev[eid])))
    for eid in changed_ids:
        p_avail = prev[eid].get("availability", "?")
        c_avail = curr[eid].get("availability", "?")
        name    = curr[eid].get("name", eid)
        detail  = f"{name} | avail: {p_avail} → {c_avail}"
        rows.append(row("#ffaa44", "CHANGED", detail))

    if not rows:
        rows.append('<tr><td colspan="2" style="color:#7ab;padding:8px">No changes detected since last run.</td></tr>')

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Sky-Net (Lite) Delta Report — {run_time}</title>
<style>
  body {{ background:#0a0e16; color:#cde; font-family:'Segoe UI',sans-serif; padding:20px; }}
  h1 {{ color:#0af; font-size:1.1em; border-bottom:1px solid #1a3a5c; padding-bottom:8px; }}
  .summary {{ display:flex; gap:20px; margin:12px 0; }}
  .stat {{ background:#0d1a2e; border:1px solid #1a3a5c; border-radius:6px; padding:10px 18px; text-align:center; }}
  .stat .val {{ font-size:1.8em; font-weight:bold; }}
  .stat.new .val {{ color:#44ff88; }}
  .stat.rem .val {{ color:#ff6666; }}
  .stat.chg .val {{ color:#ffaa44; }}
  .stat .lbl {{ font-size:0.7em; color:#7ab; text-transform:uppercase; }}
  table {{ width:100%; border-collapse:collapse; margin-top:12px; }}
  tr:nth-child(even) {{ background:rgba(255,255,255,0.03); }}
</style>
</head>
<body>
<h1>&#9651; Sky-Net (Lite) Delta Report &mdash; {run_time}</h1>
<div class="summary">
  <div class="stat new"><div class="val">{len(new_ids)}</div><div class="lbl">New</div></div>
  <div class="stat rem"><div class="val">{len(removed_ids)}</div><div class="lbl">Cancelled</div></div>
  <div class="stat chg"><div class="val">{len(changed_ids)}</div><div class="lbl">Changed</div></div>
</div>
<table>
  <thead><tr style="color:#7ab;font-size:0.75em;text-transform:uppercase">
    <th style="padding:4px 8px;text-align:left">Status</th>
    <th style="padding:4px 8px;text-align:left">Closure</th>
  </tr></thead>
  <tbody>{''.join(rows)}</tbody>
</table>
</body>
</html>"""

    with open(HTML_REPORT, "w", encoding="utf-8") as f:
        f.write(html)


def main():
    run_time = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if not os.path.exists(CURRENT_PATH):
        print("ERROR: closures.czml not found. Run grab_notams.py first.")
        sys.exit(1)

    # First run — save snapshot and exit
    if not os.path.exists(SNAPSHOT_PATH):
        import shutil
        shutil.copy2(CURRENT_PATH, SNAPSHOT_PATH)
        print(f"First run: snapshot saved to {SNAPSHOT_PATH}")
        print("Run grab_notams.py again, then re-run delta_report.py to see changes.")
        sys.exit(0)

    print(f"Loading current:  {CURRENT_PATH}")
    print(f"Loading snapshot: {SNAPSHOT_PATH}")

    curr = load_czml(CURRENT_PATH)
    prev = load_czml(SNAPSHOT_PATH)

    print(f"Current:  {len(curr)} entities")
    print(f"Snapshot: {len(prev)} entities")

    new_ids, removed_ids, changed_ids = diff_czml(prev, curr)

    print(f"\nNew:       {len(new_ids)}")
    print(f"Removed:   {len(removed_ids)}")
    print(f"Changed:   {len(changed_ids)}")

    txt = write_txt_report(new_ids, removed_ids, changed_ids, prev, curr, run_time)
    write_html_report(new_ids, removed_ids, changed_ids, prev, curr, run_time)

    print(f"\nReports written:")
    print(f"  {TXT_REPORT}")
    print(f"  {HTML_REPORT}")
    print()
    print(txt)

    # Update snapshot to current
    import shutil
    shutil.copy2(CURRENT_PATH, SNAPSHOT_PATH)
    print(f"Snapshot updated: {SNAPSHOT_PATH}")


if __name__ == "__main__":
    main()
