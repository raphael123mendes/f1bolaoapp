"""
season.py — single entry point for the season calendar
=======================================================
The master calendar lives in  calendar/<season>.json  and the season is set
once in the root config.json  ("season": 2026).  Every script reads the
calendar through this module, and the website's races.json is generated
from it — never edit races.json by hand.

Usage from a script in bot/ or results_SC/:

    import os, sys
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import season
    cal = season.load_calendar()          # {"season": 2026, "rounds": [...]}

Regenerate races.json by hand after editing the calendar:

    python season.py
"""

import json
import os
import unicodedata

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))


def current_season():
    """Season number from the root config.json (e.g. 2026)."""
    with open(os.path.join(REPO_ROOT, "config.json"), encoding="utf-8") as f:
        return int(json.load(f)["season"])


def calendar_path(season=None):
    return os.path.join(REPO_ROOT, "calendar", f"{season or current_season()}.json")


def load_calendar(season=None):
    path = calendar_path(season)
    if not os.path.exists(path):
        raise FileNotFoundError(f"Calendar not found: {path} — create it or fix 'season' in config.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def race_date(rnd):
    """Date (YYYY-MM-DD, UTC) of the race session, or '' if missing."""
    s = next((s for s in rnd["sessions"] if s["session_type"] == "race"), None)
    return s["utc_start"][:10] if s else ""


def races_for_site(cal=None):
    """Rows for the website's races.json (format the pages already use)."""
    cal = cal or load_calendar()
    return [{
        "round":               r["round"],
        "race_name":           r["name"],
        "location":            r.get("location", ""),
        "venue":               r.get("circuit", ""),
        "race_date":           race_date(r),
        "sprint_weekend":      bool(r.get("sprint")),
        "cancelled":           bool(r.get("cancelled")),
        "cancellation_reason": r.get("cancellation_reason", ""),
        "notes":               r.get("notes", ""),
    } for r in cal["rounds"]]


def write_races_json(path=None, cal=None):
    path = path or os.path.join(REPO_ROOT, "races.json")
    rows = races_for_site(cal)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    return path, len(rows)


def _norm(name):
    name = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    return name.lower().replace("grand prix", "").replace("-", " ").strip()


def check_against_api(api_calendar, cal=None):
    """Compare the master calendar with F1 Fantasy's own calendar
    (list of {"matchday", "race_name", "race_date"} from f1_calendar.fetch_calendar).
    Prints and returns a list of warnings; never raises."""
    if not api_calendar:
        print("  ⚠ Calendar check skipped: F1 Fantasy calendar not available")
        return []
    try:
        cal = cal or load_calendar()
        mine = {r["gameday_id"]: r for r in cal["rounds"] if not r.get("cancelled")}
        warnings = []
        for g in api_calendar:
            gd = g.get("matchday")
            r = mine.get(gd)
            if r is None:
                warnings.append(f"GD{gd} {g.get('race_name')} is in F1 Fantasy but not in calendar/{cal['season']}.json")
                continue
            a, b = _norm(g.get("race_name")), _norm(r["name"])
            if a and a not in b and b not in a:   # "Barcelona" vs "Barcelona-Catalunya" is fine
                warnings.append(f"GD{gd}: F1 Fantasy says '{g['race_name']}', calendar says '{r['name']}'")
            if g.get("race_date") and g["race_date"] != race_date(r):
                warnings.append(f"GD{gd} {r['name']}: race date F1 Fantasy {g['race_date']} vs calendar {race_date(r)}")
        # F1 Fantasy only lists gamedays released so far → check up to the last one it has
        api_gds = {g.get("matchday") for g in api_calendar}
        last_api = max(api_gds)
        for gd, r in mine.items():
            if gd <= last_api and gd not in api_gds:
                warnings.append(f"GD{gd} {r['name']} is in the calendar but not in F1 Fantasy")
        if warnings:
            print(f"  ⚠ Calendar check: {len(warnings)} difference(s) vs F1 Fantasy:")
            for w in warnings:
                print(f"     - {w}")
        else:
            print(f"  ✅ Calendar check: calendar/{cal['season']}.json matches F1 Fantasy")
        return warnings
    except Exception as e:
        print(f"  ⚠ Calendar check skipped: {e}")
        return []


if __name__ == "__main__":
    path, n = write_races_json()
    print(f"races.json regenerated from {calendar_path()} ({n} rounds) → {path}")
