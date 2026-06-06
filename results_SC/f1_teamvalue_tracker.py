"""
F1 Fantasy — Team Value Tracker
=================================
Tracks team value, budget and picks for all league members across the season.

Features:
  ✅ Auto-detects current race from the API calendar — no manual settings needed
  ✅ Upserts into a master JSON (run multiple times safely, never duplicates)
  ✅ Detects transfers when next race leaderboard is available
  ✅ Tableau-ready: join on user_guid or team_name

Output: teamvalue_master.json in SAVE_FOLDER
  One row per team per race containing:
    - team identity (guid, name, owner)
    - race context (number, name, date)
    - team_value, budget, ov_points, gd_points
    - picks list + transfers in/out vs previous race

USAGE:
  - Just run it! No parameters to change each race.
  - python f1_teamvalue_tracker.py
"""

import requests
import json
import os
import time
from datetime import datetime, timezone
from urllib.parse import unquote

from f1_config import cfg, SCRIPT_DIR

# ── Settings — loaded from config.json ────────────────────────────
LEAGUE_ID   = cfg.get("league_id", "")
SAVE_FOLDER = cfg.get("save_folder", ".")
MASTER_FILE = os.path.join(SAVE_FOLDER, "teamvalue_master.json")

COOKIE_FILE = os.path.join(SCRIPT_DIR, "cookie.txt")

def load_cookie():
    if not os.path.exists(COOKIE_FILE):
        print(f"\n⚠️  ERROR: cookie.txt not found!")
        return None
    cookie = open(COOKIE_FILE, encoding="utf-8").read().strip()
    if not cookie:
        print(f"\n⚠️  ERROR: cookie.txt is empty!")
        return None
    return cookie

COOKIE = load_cookie() or ""

BASE_SVC  = "https://fantasy.formula1.com/services/user"
BASE_FEED = "https://fantasy.formula1.com/feeds"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept":     "application/json, text/plain, */*",
    "Referer":    "https://fantasy.formula1.com/",
    "Cookie":     COOKIE,
}

DRIVER_MAP = {
    "11":    "Alexander Albon",
    "11149": "Arvid Lindblad",
    "125":   "Carlos Sainz",
    "115":   "Charles Leclerc",
    "118":   "Esteban Ocon",
    "12":    "Fernando Alonso",
    "11059": "Franco Colapinto",
    "11051": "Gabriel Bortoleto",
    "124":   "George Russell",
    "11032": "Isack Hadjar",
    "11161": "Kimi Antonelli",
    "129":   "Lance Stroll",
    "117":   "Lando Norris",
    "110":   "Lewis Hamilton",
    "114":   "Liam Lawson",
    "131":   "Max Verstappen",
    "111":   "Nico Hulkenberg",
    "11031": "Oliver Bearman",
    "1982":  "Oscar Piastri",
    "18":    "Pierre Gasly",
    "121":   "Sergio Perez",
    "13":    "Valtteri Bottas",
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

def player_name(pid):
    return DRIVER_MAP.get(pid) or CONSTRUCTOR_MAP.get(pid) or f"Unknown ({pid})"

# ── Helpers ───────────────────────────────────────────────────────

def buster_epoch():
    return int(time.time() * 1000)

def buster_str():
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")

def get(url):
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()

# ── Calendar (auto-detect current race) ───────────────────────────

def fetch_calendar():
    """Fetch race calendar from FixtureWiseStats. Returns sorted list of races."""
    from f1_calendar import fetch_calendar as _fetch, detect_current_race, detect_last_completed_race, print_calendar
    calendar = _fetch(HEADERS)
    current = detect_last_completed_race(calendar)
    _, nxt  = detect_current_race(calendar)
    print_calendar(calendar, current, nxt)
    return calendar, current, nxt

# ── API calls ─────────────────────────────────────────────────────

def get_leaderboard(matchday):
    url = f"{BASE_FEED}/leaderboard/privateleague/list_2_{LEAGUE_ID}_{matchday}_1.json?buster={buster_str()}"
    try:
        data = get(url)
        return data["Value"]["leaderboard"]
    except Exception as e:
        print(f"  ⚠ Leaderboard GD{matchday} unavailable: {e}")
        return []

def get_opponent_team(user_guid, matchday):
    url = (f"{BASE_SVC}/opponentteam/opponentgamedayplayerteamget"
           f"/1/{user_guid}/1/{matchday}/1?buster={buster_epoch()}")
    try:
        data = get(url)
        team = data["Data"]["Value"]["userTeam"][0]
        return {
            "team_value": team.get("teamval") or team.get("team_info", {}).get("teamVal"),
            "budget":     team.get("teambal") or team.get("team_info", {}).get("teamBal"),
            "ov_points":  team.get("ovpoints"),
            "gd_points":  team.get("gdpoints"),
            "gd_rank":    team.get("gdrank"),
            "ov_rank":    team.get("ovrank"),
        }
    except Exception as e:
        return {}

# ── Master file ───────────────────────────────────────────────────

def load_master():
    """Load master JSON, indexed by (user_guid, race_number)."""
    if not os.path.exists(MASTER_FILE):
        return {}
    try:
        with open(MASTER_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {
            (row["user_guid"], row["race_number"]): row
            for row in data.get("teams", [])
        }
    except Exception as e:
        print(f"  ⚠ Could not load master: {e}")
        return {}

def save_master(index):
    rows = sorted(
        index.values(),
        key=lambda r: (r["race_number"], r["ov_rank"] or 99)
    )
    out = {
        "updated_at":  datetime.now(timezone.utc).isoformat(),
        "total_rows":  len(rows),
        "races_covered": sorted(set(r["race_number"] for r in rows)),
        "teams":       rows,
    }
    os.makedirs(SAVE_FOLDER, exist_ok=True)
    with open(MASTER_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    return rows

# ── Fetch all team values ─────────────────────────────────────────

def fetch_all(current_race, next_race):
    matchday      = current_race["matchday"]
    next_matchday = next_race["matchday"] if next_race else matchday + 1

    print(f"\n  Fetching GD{matchday} leaderboard...")
    lb_current = get_leaderboard(matchday)
    print(f"  → {len(lb_current)} teams found")

    # Try next GD leaderboard for transfer detection (403 = window not open yet)
    print(f"  Fetching GD{next_matchday} leaderboard (for transfers)...")
    lb_next = get_leaderboard(next_matchday)
    if lb_next:
        print(f"  → {len(lb_next)} teams found — transfer detection available ✅")
    else:
        print(f"  → Not available yet — transfer window not open ⏳")

    lb_next_idx = {e["user_guid"]: e for e in lb_next}

    print(f"\n  Fetching team values...\n")

    rows = []
    for entry in lb_current:
        user_guid = entry["user_guid"]
        team_name = unquote(entry["team_name"])
        user_name = entry["user_name"]
        social_id = entry.get("social_id", "")

        picks_cur  = entry.get("user_team", [])
        picks_next = lb_next_idx.get(user_guid, {}).get("user_team", [])
        pts_cur    = entry.get("cur_points", 0)

        print(f"  → {team_name} ({user_name})", end=" ", flush=True)

        tv = get_opponent_team(user_guid, matchday)
        time.sleep(0.2)

        # Transfer detection
        if picks_next:
            out = list(set(picks_cur) - set(picks_next))
            ins = list(set(picks_next) - set(picks_cur))
        else:
            out, ins = [], []

        out_names = [player_name(p) for p in out]
        in_names  = [player_name(p) for p in ins]

        tv_val = tv.get("team_value")
        bud    = tv.get("budget")
        print(f"val=${tv_val}m  bud=${bud}m  pts={pts_cur}"
              + (f"  🔄 OUT:{out_names} IN:{in_names}" if out else ""))

        rows.append({
            # ── Identity ──
            "user_guid":        user_guid,
            "social_id":        social_id,
            "team_name":        team_name,
            "user_name":        user_name,
            # ── Race context ──
            "race_number":      matchday,
            "race_name":        current_race["race_name"],
            "race_flag":        current_race["race_flag"],
            "race_date":        current_race["race_date"],
            # ── Financials ──
            "team_value":       tv_val,
            "budget":           bud,
            # ── Points & rank ──
            "ov_points":        tv.get("ov_points") or pts_cur,
            "gd_points":        tv.get("gd_points"),
            "ov_rank":          tv.get("ov_rank"),
            "gd_rank":          tv.get("gd_rank"),
            # ── Picks ──
            "picks":            picks_cur,
            "picks_names":      [player_name(p) for p in picks_cur],
            # ── Transfers vs next race ──
            "transfers_out":    out,
            "transfers_out_names": out_names,
            "transfers_in":     ins,
            "transfers_in_names":  in_names,
            "transfer_window_open": bool(picks_next),
            # ── Meta ──
            "fetched_at":       datetime.now(timezone.utc).isoformat(),
        })

        time.sleep(0.2)

    return rows

# ── Report ────────────────────────────────────────────────────────

def print_report(rows, current_race):
    print(f"\n\n{'═'*72}")
    print(f"  TEAM VALUE REPORT — {current_race['race_flag']} {current_race['race_name']}")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'═'*72}")
    print(f"  {'#':<4} {'Team':<22} {'Owner':<18} {'Value':>8}  {'Budget':>8}  {'OvPts':>7}  {'GdPts':>7}")
    print(f"  {'─'*4} {'─'*22} {'─'*18} {'─'*8}  {'─'*8}  {'─'*7}  {'─'*7}")

    for r in sorted(rows, key=lambda x: x["ov_rank"] or 99):
        tv  = f"${r['team_value']:.1f}m" if isinstance(r['team_value'], (int,float)) else "?"
        bud = f"${r['budget']:.1f}m"     if isinstance(r['budget'],     (int,float)) else "?"
        ovp = str(int(r['ov_points']))   if r['ov_points'] is not None else "?"
        gdp = str(int(r['gd_points']))   if r['gd_points'] is not None else "?"
        rnk = r['ov_rank'] or "?"
        print(f"  {rnk:<4} {r['team_name']:<22} {r['user_name']:<18} {tv:>8}  {bud:>8}  {ovp:>7}  {gdp:>7}")

    transfers = [r for r in rows if r["transfers_out"]]
    if transfers:
        print(f"\n  🔄 Transfers detected:")
        for r in transfers:
            print(f"     {r['team_name']}: OUT {r['transfers_out_names']} → IN {r['transfers_in_names']}")
    elif rows and rows[0]["transfer_window_open"]:
        print(f"\n  ✅ No transfers made by anyone")
    else:
        print(f"\n  ⏳ Transfer window not open yet — run again closer to Race {rows[0]['race_number']+1}")

# ── Main ──────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="F1 Fantasy — Fetch team values, budgets and transfers",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog="""
OUTPUT:
  G:/My Drive/FantasyF1-26/teamvalue_master.json
  One row per team per race — safe to run multiple times (upserts)
  Also detects transfers if next race window is open

EXAMPLES:
  python f1_teamvalue_tracker.py       fetch team values for current race
        """
    )
    parser.parse_known_args()  # enables --help; ignores unknown args (e.g. --race from run_all.py)

    print("=" * 72)
    print("  F1 FANTASY — TEAM VALUE TRACKER")
    print("=" * 72)

    if not COOKIE:
        print("\n⚠️  No cookie loaded. Check cookie.txt.")
        return

    # Auto-detect current race
    print("\n  Fetching calendar...")
    calendar, current_race, next_race = fetch_calendar()

    if not current_race:
        print("\n⚠️  No race has been played yet. Run again after the first race.")
        return

    print(f"\n  ✅ Current race: GD{current_race['matchday']} — {current_race['race_name']} ({current_race['race_date']})")
    if next_race:
        print(f"  ✅ Next race:    GD{next_race['matchday']} — {next_race['race_name']} ({next_race['race_date']})")

    # Load master
    index = load_master()
    existing = sum(1 for (_, r) in index if r == current_race["matchday"])
    if existing:
        print(f"\n  ℹ️  {existing} existing rows for GD{current_race['matchday']} will be replaced")

    # Fetch
    rows = fetch_all(current_race, next_race)
    if not rows:
        print("\n⚠️  Nothing fetched.")
        return

    # Upsert
    for row in rows:
        key = (row["user_guid"], row["race_number"])
        index[key] = row

    # Save
    all_rows = save_master(index)
    print(f"\n  ✅ Saved: {MASTER_FILE}")
    print(f"     Rows: {len(all_rows)}  |  Races: {sorted(set(r['race_number'] for r in all_rows))}")

    print_report(rows, current_race)

    print(f"\n✅ Done!")
    print(f"   Tableau: connect to {MASTER_FILE}")
    print(f"   Join on: user_guid  OR  team_name")


if __name__ == "__main__":
    main()


# Allow calling as module from run_all.py
run = main
