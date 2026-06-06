"""
f1_calendar.py — Shared calendar helper for F1 Fantasy tracker scripts
=======================================================================
Fetches the full race calendar from the F1 Fantasy API (via any player's
playerstats feed) and auto-detects the current and next matchday.

Used by:
  - f1_price_tracker.py
  - f1_teamvalue_tracker.py

No manual race settings needed — just run and it figures out where we are.
"""

import requests
import json
import os
import time
from datetime import datetime, timezone

BASE_FEED = "https://fantasy.formula1.com/feeds"

# Any valid player ID works — Leclerc is reliable
REFERENCE_PLAYER_ID = "115"

# Flags for race names (add more as season progresses)
RACE_FLAGS = {
    "Australian Grand Prix":  "🇦🇺",
    "Chinese Grand Prix":     "🇨🇳",
    "Japanese Grand Prix":    "🇯🇵",
    "Bahrain Grand Prix":     "🇧🇭",
    "Saudi Arabian Grand Prix": "🇸🇦",
    "Miami Grand Prix":       "🇺🇸",
    "Emilia Romagna Grand Prix": "🇮🇹",
    "Monaco Grand Prix":      "🇲🇨",
    "Spanish Grand Prix":     "🇪🇸",
    "Canadian Grand Prix":    "🇨🇦",
    "Austrian Grand Prix":    "🇦🇹",
    "British Grand Prix":     "🇬🇧",
    "Belgian Grand Prix":     "🇧🇪",
    "Hungarian Grand Prix":   "🇭🇺",
    "Dutch Grand Prix":       "🇳🇱",
    "Italian Grand Prix":     "🇮🇹",
    "Azerbaijan Grand Prix":  "🇦🇿",
    "Singapore Grand Prix":   "🇸🇬",
    "United States Grand Prix": "🇺🇸",
    "Mexico City Grand Prix": "🇲🇽",
    "São Paulo Grand Prix":   "🇧🇷",
    "Las Vegas Grand Prix":   "🇺🇸",
    "Qatar Grand Prix":       "🇶🇦",
    "Abu Dhabi Grand Prix":   "🇦🇪",
}


def get_buster_str():
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def fetch_calendar(headers):
    """
    Fetch the full race calendar from FixtureWiseStats.
    Returns a list of race dicts sorted by GamedayId:
      {
        "matchday":    1,
        "race_name":   "Australian Grand Prix",
        "race_flag":   "🇦🇺",
        "race_date":   "2026-03-08",          # date of the Race session
        "race_dt":     datetime(..., utc),     # full UTC datetime of Race
        "sessions":    [...],                  # all session dicts
        "is_sprint":   False,
      }
    """
    url = f"{BASE_FEED}/popup/playerstats_{REFERENCE_PLAYER_ID}.json?buster={get_buster_str()}"
    try:
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  ⚠ Calendar fetch error: {e}")
        return []

    fixtures = data["Value"].get("FixtureWiseStats", [])
    gd_map = {}

    for fx in fixtures:
        gd = fx["GamedayId"]
        if gd not in gd_map:
            gd_map[gd] = {
                "matchday":  gd,
                "race_name": None,
                "race_flag": None,
                "race_date": None,
                "race_dt":   None,
                "sessions":  [],
                "is_sprint": False,
            }

        for rd in fx.get("RaceDayWise", []):
            session_type  = rd.get("SessionType", "")
            meeting_name  = rd.get("MeetingName", "")
            session_start = rd.get("SessionStartDate", "")
            match_status  = rd.get("MatchStatus", "0")  # "4" = completed

            gd_map[gd]["race_name"] = meeting_name
            gd_map[gd]["race_flag"] = RACE_FLAGS.get(meeting_name, "🏁")
            gd_map[gd]["sessions"].append({
                "type":         session_type,
                "start":        session_start,
                "match_status": match_status,
            })

            if "Sprint" in session_type:
                gd_map[gd]["is_sprint"] = True

            # Use the Race session date as the canonical race date (kept for reference)
            if session_type == "Race" and session_start:
                try:
                    import re
                    dt_str_clean = re.sub(r'([+-]\d{2}):(\d{2})$', r'\1\2', session_start)
                    dt_local = datetime.strptime(dt_str_clean, "%Y-%m-%dT%H:%M:%S%z")
                    dt_utc   = dt_local.astimezone(timezone.utc)
                    gd_map[gd]["race_dt"]   = dt_utc
                    gd_map[gd]["race_date"] = dt_utc.strftime("%Y-%m-%d")
                except Exception:
                    gd_map[gd]["race_date"] = session_start[:10]

    return sorted(gd_map.values(), key=lambda x: x["matchday"])


def detect_current_race(calendar):
    """
    Auto-detect which matchday we're currently in or just finished.

    Logic:
      - MatchStatus "4" = session completed
      - A gameday is DONE if ALL its sessions have MatchStatus "4"
      - Current = last gameday that is NOT fully done (active/in progress)
        OR the last gameday if all are done (between races)
      - Next = the gameday after current (may not exist in API yet)

    Note: the API only returns gamedays that have started or are imminent,
    not the full season ahead. So we work with what's available.

    Returns (current, next) as race dicts.
    """
    def is_completed(race):
        sessions = race.get("sessions", [])
        return sessions and all(s.get("match_status") == "4" for s in sessions)

    completed = [r for r in calendar if is_completed(r)]
    active    = [r for r in calendar if not is_completed(r)]

    if active:
        # There's a race currently in progress or about to start
        current = active[0]   # first non-completed = current
        # Next = next active one, or None
        nxt = active[1] if len(active) > 1 else None
    elif completed:
        # All known races are done — we're between races
        current = completed[-1]   # most recently completed
        nxt     = None            # next race not in API yet
    else:
        current = None
        nxt     = None

    return current, nxt


def detect_last_completed_race(calendar):
    """
    Returns the last race where ALL sessions have MatchStatus == "4" (completed).
    Used by f1_price_tracker.py to fetch prices after a race finishes.

    Example: after Race 2 completes, returns GD2 as "last completed"
    so price tracker fetches GD2 → GD3 price transition correctly.
    """
    def is_completed(race):
        sessions = race.get("sessions", [])
        return sessions and all(s.get("match_status") == "4" for s in sessions)

    completed = [r for r in calendar if is_completed(r)]
    return completed[-1] if completed else None


def print_calendar(calendar, current, nxt):
    print(f"\n  {'GD':<4} {'Race':<35} {'Date':<12} {'Sprint':<7} {'Status'}")
    print(f"  {'─'*4} {'─'*35} {'─'*12} {'─'*7} {'─'*10}")
    for r in calendar:
        status = ""
        if current and r["matchday"] == current["matchday"]:
            status = "◀ CURRENT"
        elif nxt and r["matchday"] == nxt["matchday"]:
            status = "▶ NEXT"
        sprint = "Sprint" if r["is_sprint"] else ""
        date   = r["race_date"] or "?"
        print(f"  {r['matchday']:<4} {r['race_flag']} {r['race_name']:<33} {date:<12} {sprint:<7} {status}")
