"""
F1 Fantasy League — Full Pipeline
===================================
What this script does in one run:
  1. Fetches full league data from F1 Fantasy API
  2. Recalculates LIVE rankings from gd_points (accurate during race weekend)
  3. Saves a per-race JSON file (results_SC/data/race_NN_*.json)

SETUP INSTRUCTIONS (one time only):
─────────────────────────────────────────────────────────────────────

1. INSTALL DEPENDENCIES:
     pip install requests

2. SET YOUR COOKIE (refresh when you get 401 errors):
     - Go to fantasy.formula1.com (logged in)
     - DevTools (F12) → Network → click any /services/ request
     - Right-click → Copy → Copy as cURL
     - Paste in Notepad, find: -H 'cookie: XXXX'
     - Copy just that cookie value (everything after 'cookie: ')
     - Paste it into cookie.txt (same folder as this script)
     - No quotes, no extra spaces — just the raw cookie string

─────────────────────────────────────────────────────────────────────
"""

import requests
import json
import sys
import time
import os
import re
from datetime import datetime, timezone
from urllib.parse import unquote

from f1_config import cfg, SCRIPT_DIR
from f1_calendar import fetch_calendar, detect_current_race, detect_last_completed_race

# ══════════════════════════════════════════════════════════════════
#  SETTINGS — loaded from config.json via f1_config.py
#  No manual changes needed here between races!
# ══════════════════════════════════════════════════════════════════

USER_UUID   = cfg.get("user_uuid", "")
LEAGUE_ID   = cfg.get("league_id", "")
LEAGUE_NAME = cfg.get("league_name", "F1 Fantasy")
SAVE_FOLDER = cfg.get("save_folder", ".")

# ── Cookie ────────────────────────────────────────────────────────
COOKIE_FILE = os.path.join(SCRIPT_DIR, "cookie.txt")

def load_cookie():
    if not os.path.exists(COOKIE_FILE):
        print(f"\n⚠️  ERROR: cookie.txt not found!")
        print(f"  Create a file named 'cookie.txt' in: {SCRIPT_DIR}")
        print(f"  Paste your F1 Fantasy cookie value into it and save.")
        return None
    cookie = open(COOKIE_FILE, encoding="utf-8").read().strip()
    if not cookie:
        print(f"\n⚠️  ERROR: cookie.txt is empty!")
        print(f"  Paste your F1 Fantasy cookie value into cookie.txt and save.")
        return None
    return cookie

COOKIE = load_cookie() or ""


def abort_cookie(detail=""):
    print("\n" + "!" * 60)
    print("  F1 FANTASY COOKIE EXPIRED OR INVALID" + (f" ({detail})" if detail else ""))
    print("  Nothing was saved. Refresh the F1_COOKIE secret")
    print("  (Actions -> F1 Refresh Cookie, or paste a new cookie manually).")
    print("!" * 60)
    sys.exit(2)

# ── Auto-detect race from calendar ───────────────────────────────
def _detect_race():
    """Try to auto-detect current race from API. Falls back to config.json."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept":     "application/json, text/plain, */*",
            "Referer":    "https://fantasy.formula1.com/",
            "Cookie":     COOKIE,
        }
        calendar     = fetch_calendar(headers)
        # Warn if the master calendar (calendar/<season>.json) disagrees with F1 Fantasy
        try:
            import sys as _sys, os as _os
            _sys.path.append(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
            import season
            season.check_against_api(calendar)
        except Exception as _e:
            print(f"  ⚠ Calendar check skipped: {_e}")
        current_race = detect_last_completed_race(calendar)

        # ── --matchday override (set by run_all.py --matchday N) ──
        override = cfg.get("matchday_override")
        if override:
            match = next((r for r in calendar if r["matchday"] == override), None)
            if match:
                print(f"  ⚡ Matchday override: Race {override} — {match['race_name']}")
                return (
                    match["matchday"],
                    match["race_name"],
                    match.get("race_flag", "🏎️"),
                )
            else:
                print(f"  ⚠  Override Race {override} not found in calendar — falling back")

        if current_race:
            print(f"  ✅ Auto-detected: Race {current_race['matchday']} — {current_race['race_name']}")
            return (
                current_race["matchday"],
                current_race["race_name"],
                current_race.get("race_flag", "🏎️"),
            )
    except Exception as e:
        print(f"  ⚠  Calendar auto-detect failed: {e}")

    # Fallback to config.json manual override
    matchday  = cfg.get("matchday", 1)
    race_name = cfg.get("race_name", "Race")
    race_flag = cfg.get("race_flag", "🏎️")
    print(f"  ⚠  Using config.json fallback: Race {matchday} — {race_name}")
    return matchday, race_name, race_flag

MATCHDAY, RACE_NAME, RACE_FLAG = _detect_race()

# ══════════════════════════════════════════════════════════════════

BASE_SVC  = "https://fantasy.formula1.com/services/user"
BASE_FEED = "https://fantasy.formula1.com/feeds"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept":     "application/json, text/plain, */*",
    "Referer":    "https://fantasy.formula1.com/",
    "Cookie":     COOKIE,
}

DRIVER_MAP = {
    "11":    "Alexander Albon",    # Williams
    "11149": "Arvid Lindblad",     # Racing Bulls
    "125":   "Carlos Sainz",       # Williams
    "115":   "Charles Leclerc",    # Ferrari
    "118":   "Esteban Ocon",       # Haas
    "12":    "Fernando Alonso",    # Aston Martin
    "11059": "Franco Colapinto",   # Alpine
    "11051": "Gabriel Bortoleto",  # Audi
    "124":   "George Russell",     # Mercedes
    "11032": "Isack Hadjar",       # Red Bull Racing
    "11161": "Kimi Antonelli",     # Mercedes
    "129":   "Lance Stroll",       # Aston Martin
    "117":   "Lando Norris",       # McLaren
    "110":   "Lewis Hamilton",     # Ferrari
    "114":   "Liam Lawson",        # Racing Bulls
    "131":   "Max Verstappen",     # Red Bull Racing
    "111":   "Nico Hulkenberg",    # Audi
    "11031": "Oliver Bearman",     # Haas
    "1982":  "Oscar Piastri",      # McLaren
    "18":    "Pierre Gasly",       # Alpine
    "121":   "Sergio Perez",       # Cadillac
    "13":    "Valtteri Bottas",    # Cadillac
}

CONSTRUCTOR_MAP = {
    "23":   "Alpine",
    "24":   "Aston Martin",
    "2640": "Audi",
    "2641": "Cadillac",
    "25":   "Ferrari",
    "26":   "Haas F1 Team",
    "27":   "McLaren",
    "28":   "Mercedes",
    "2636": "Racing Bulls",
    "29":   "Red Bull Racing",
    "210":  "Williams",
}

CARD_FIELDS = {
    "isWildcardtaken":   ("Wildcard",    "wildCardtakengd"),
    "isLimitlesstaken":  ("Limitless",   "limitLesstakengd"),
    "isNonigativetaken": ("No Negative", "noNigativetakengd"),
    "isFinalfixtaken":   ("Final Fix",   "finalFixtakengd"),
    "isExtradrstaken":   ("Extra DRS",   "extraDrstakengd"),
    "isAutopilottaken":  ("Autopilot",   "isAutopilottakengd"),
}



# ── Helpers ───────────────────────────────────────────────────────

def get(url):
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()

def buster():
    return int(time.time() * 1000)

def slugify(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")

def rank_change_arrow(change):
    if change > 0:   return f"▲{change}"
    elif change < 0: return f"▼{abs(change)}"
    return "─"


# ── Rank recalculation ────────────────────────────────────────────

def recalculate_ranks(results):
    """
    Recalculates overall rank (from live total_points) and
    gameday rank (from live gd_points). Handles ties correctly.
    Also computes rank change vs cached API rank.
    """
    def assign_ranks(items, key):
        sorted_items = sorted(items, key=lambda x: x[key] if x[key] is not None else 0, reverse=True)
        rank = 1
        for i, t in enumerate(sorted_items):
            if i > 0 and t[key] == sorted_items[i-1][key]:
                t[f"_{key}_rank"] = sorted_items[i-1][f"_{key}_rank"]
            else:
                t[f"_{key}_rank"] = rank
            rank += 1

    assign_ranks(results, "total_points")
    assign_ranks(results, "gd_points")

    for t in results:
        t["overall_rank"]  = t.pop("_total_points_rank")
        t["live_gd_rank"]  = t.pop("_gd_points_rank")
        t["rank_change"]   = t["rank"] - t["overall_rank"]

    return sorted(results, key=lambda x: (x["overall_rank"], x["live_gd_rank"]))


# ── API calls ─────────────────────────────────────────────────────

def get_live_buster():
    data = get(f"{BASE_FEED}/live/mixapi.json?buster={buster()}")
    return data["Value"]["lv"]

def get_leaderboard():
    url = f"{BASE_FEED}/leaderboard/privateleague/list_1_{LEAGUE_ID}_0_1.json?buster={buster()}"
    return get(url)["Value"]["leaderboard"]

def get_cards(user_guid):
    # Last param must be 1 (cumulative view) — passing MATCHDAY returns wrong card state.
    # Filter by takengd == MATCHDAY so only cards used THIS race are shown.
    url = f"{BASE_SVC}/opponentteam/opponentgamedayget/1/{user_guid}/1?buster={buster()}"
    data = get(url)["Data"]["Value"]
    cards = []
    for field, (label, gd_field) in CARD_FIELDS.items():
        if data.get(field, 0):
            gd = data.get(gd_field, "?")
            if gd == MATCHDAY:          # only include cards used in the current race
                cards.append(f"{label} (GD{gd})")
    return cards

def get_team_detail(user_guid):
    url = (f"{BASE_SVC}/opponentteam/opponentgamedayplayerteamget"
           f"/1/{user_guid}/1/{MATCHDAY}/1?buster={buster()}")
    return get(url)["Data"]["Value"]["userTeam"][0]

def get_penalties(detail):
    """Penalty values exactly as the F1 Fantasy team screen shows them.

    The game's team page reads these fields from the same response:
      - usersubsleft  < 0  -> extra transfers made (e.g. -1 = one over the limit)
      - extrasubscost      -> points lost per extra transfer (10)
        Screen: "Additional transfers points deducted from overall
                 score (-10 x 1 = -10 pts)"
      - inactive_driver_penality_points -> already a signed value (e.g. -25)
        Screen: "Inactive driver penalty points = -25 pts"
    The final gdpoints already include both, so these are for display only.
    """
    subs_left     = int(detail.get("usersubsleft") or 0)
    cost          = int(detail.get("extrasubscost") or 0)
    extra         = -subs_left if subs_left < 0 else 0
    transfer_pen  = -extra * cost
    inactive_pen  = int(detail.get("inactive_driver_penality_points") or 0)
    if transfer_pen or inactive_pen:
        print(f"    Penalties (from game): transfers {transfer_pen} "
              f"({extra} x -{cost}), inactive driver {inactive_pen}")
    return {
        "transfers_made":     int(detail.get("usersubs") or 0),
        "transfers_allowed":  int(detail.get("subsallowed") or 0),
        "extra_transfers":    extra,
        "extra_transfer_cost": cost,
        "transfer_penalty":   transfer_pen,
        "inactive_penalty":   inactive_pen,
        "penalty_points":     transfer_pen + inactive_pen,
    }

def get_player_stats(player_id, lv):
    url = f"{BASE_FEED}/popup/playerstats_{player_id}.json?buster={lv}"
    try:
        data = get(url)["Value"]["GamedayWiseStats"]
        for gd in data:
            if gd["GamedayId"] == MATCHDAY:
                total = next((s["Value"] for s in gd["StatsWise"]
                              if s["Event"] == "Total"), None)
                breakdown = [f"{s['Event']}: {s['Value']:+d}"
                             for s in gd["StatsWise"] if s["Event"] != "Total"]
                return total, breakdown
    except Exception:
        pass
    return None, []


# ── Step 1: Fetch all data ────────────────────────────────────────

def fetch_all():
    print("\n[1/5] Fetching live buster...")
    lv = get_live_buster()

    print("[2/5] Fetching leaderboard...")
    leaderboard = get_leaderboard()
    print(f"  Found {len(leaderboard)} teams")

    results = []
    print("[3/5] Fetching team details + player stats...")

    for entry in leaderboard:
        user_guid  = entry["user_guid"]
        team_name  = unquote(entry["team_name"])
        user_name  = entry["user_name"]
        api_rank   = int(entry["cur_rank"])
        cur_points = entry["cur_points"]

        print(f"  → {team_name} ({user_name})")

        try:
            cards = get_cards(user_guid)
        except Exception:
            cards = []

        try:
            detail     = get_team_detail(user_guid)
            budget     = detail["team_info"]["teamBal"]
            team_value = detail["team_info"]["teamVal"]
            gd_points  = detail["gdpoints"] or 0
            ovpoints   = detail["ovpoints"] or 0
            player_ids = detail["playerid"]
            penalties  = get_penalties(detail)
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else None
            if code in (401, 403):
                abort_cookie(f"HTTP {code} on team details")
            print(f"    ⚠ Team detail error: {e}")
            continue
        except Exception as e:
            print(f"    ⚠ Team detail error: {e}")
            continue

        picks = []
        for pick in sorted(player_ids, key=lambda x: x["playerpostion"]):
            pid      = pick["id"]
            is_cap   = pick["iscaptain"] == 1
            is_mgcap = pick["ismgcaptain"] == 1
            ptype    = "Constructor" if pid in CONSTRUCTOR_MAP else "Driver"
            name     = CONSTRUCTOR_MAP.get(pid) or DRIVER_MAP.get(pid) or f"Unknown (ID {pid})"
            pts, breakdown = get_player_stats(pid, lv)
            picks.append({
                "id":         pid,
                "name":       name,
                "type":       ptype,
                "is_captain": is_cap,
                "is_megacap": is_mgcap,
                "points_gd":  pts,
                "breakdown":  breakdown,
            })
            time.sleep(0.1)

        results.append({
            "race_number":      MATCHDAY,
            "race_name":        RACE_NAME,
            "race_flag":        RACE_FLAG,
            "race_date":        datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "rank":             api_rank,
            "overall_rank":     0,
            "live_gd_rank":     0,
            "rank_change":      0,
            "team_name":        team_name,
            "user_name":        user_name,
            "total_points":     ovpoints,
            "gd_points":        gd_points,
            "budget_remaining": budget,
            "team_value":       team_value,
            "cards_used":       cards,
            **penalties,
            "picks":            picks,
        })

    # Never save a race file with players missing — it would corrupt the history
    if len(results) < len(leaderboard):
        print(f"\n⛔ Only {len(results)} of {len(leaderboard)} teams fetched — nothing saved.")
        print("   Check the errors above (cookie expired? API down?) and run again.")
        sys.exit(1)

    print("[4/5] Recalculating live rankings...")
    results = recalculate_ranks(results)
    for t in results:
        arrow = rank_change_arrow(t["rank_change"])
        print(f"  #{t['overall_rank']} {t['team_name']} — "
              f"{t['total_points']}pts total | {t['gd_points']}pts this race {arrow}")

    return results


# ── Step 2: Save race JSON ────────────────────────────────────────────

def save_files(results):
    os.makedirs(SAVE_FOLDER, exist_ok=True)
    slug = f"race_{MATCHDAY:02d}_{slugify(RACE_NAME)}"

    # JSON backup
    json_path = os.path.join(SAVE_FOLDER, f"{slug}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"  ✅ JSON: {json_path}")



# ── Print terminal report ─────────────────────────────────────────

def print_report(results):
    print("\n" + "=" * 65)
    print(f"  {RACE_FLAG}  {RACE_NAME}  |  Race {MATCHDAY}")
    print(f"  League: {LEAGUE_NAME}")
    print(f"  Rankings recalculated from live data")
    print("=" * 65)

    for t in results:
        arrow = rank_change_arrow(t["rank_change"])
        print(f"\n{'─'*65}")
        print(f"  #{t['overall_rank']} (GD #{t['live_gd_rank']})  "
              f"{t['team_name']}  ({t['user_name']})  {arrow}")
        print(f"  Total pts : {t['total_points']}  |  This race: {t['gd_points']}")
        print(f"  Budget    : ${t['budget_remaining']:.1f}m  |  "
              f"Team value: ${t['team_value']:.1f}m")
        cards_str = ", ".join(t["cards_used"]) if t["cards_used"] else "None"
        print(f"  Cards used: {cards_str}")
        print(f"\n  {'Type':<13} {'Name':<28} {'GD Pts':>6}  Breakdown")
        print(f"  {'─'*13} {'─'*28} {'─'*6}  {'─'*28}")
        for p in t["picks"]:
            pts_str = str(p["points_gd"]) if p["points_gd"] is not None else "?"
            cap     = " ★CAP"  if p["is_captain"] else \
                      " ★MEGA" if p["is_megacap"]  else ""
            name    = p["name"] + cap
            bd      = " | ".join(p["breakdown"]) if p["breakdown"] else ""
            print(f"  {p['type']:<13} {name:<34} {pts_str:>4}  {bd}")


# ── Main ──────────────────────────────────────────────────────────

def main():
    print("=" * 65)
    print("  F1 FANTASY LEAGUE — FETCH STANDINGS")
    print("=" * 65)
    print(f"  Race     : {RACE_FLAG} {RACE_NAME} (Race {MATCHDAY})")
    print(f"  League   : {LEAGUE_NAME} ({LEAGUE_ID})")
    print(f"  Save to  : {SAVE_FOLDER}")

    if not COOKIE:
        abort_cookie("cookie.txt missing or empty")

    results = fetch_all()
    print_report(results)

    print("\n[5/5] Saving race file...")
    save_files(results)

    print("\n✅ All done!")


if __name__ == "__main__":
    main()


# Allow calling as module from run_all.py
run = main
