"""
F1 Fantasy — Price Tracker
===========================
Tracks driver & constructor price changes across the full season.

Features:
  ✅ Auto-detects current race from the API calendar — no manual settings needed
  ✅ Upserts into a master JSON (run multiple times safely, never duplicates)
  ✅ Reads next race prices from GamedayId N+1 in playerstats
  ✅ Tableau-ready: join on player_id OR player_name

Output: prices_master.json in SAVE_FOLDER
  One row per player per race containing:
    - player identity (id, name, type)
    - race context (number, name, date)
    - price_this_race, price_next_race, price_change, price_change_pct
    - gameday_points   : fantasy points scored this race weekend (from StatsWise Total)
    - total_points     : cumulative season fantasy points (sum of all completed races)
    - season_high      : highest price seen so far this season
    - season_low       : lowest price seen so far this season
    - pct_vs_high      : price_this_race vs season high  (e.g. -8.3%)
    - pct_vs_low       : price_this_race vs season low   (e.g. +12.5%)

USAGE:
  - Just run it after each race! No parameters to change.
  - python f1_price_tracker.py
"""

import requests
import json
import os
import time
from datetime import datetime, timezone

from f1_config import cfg, SCRIPT_DIR

# ── Settings — loaded from config.json ────────────────────────────
SAVE_FOLDER = cfg.get("save_folder", ".")
MASTER_FILE = os.path.join(SAVE_FOLDER, "prices_master.json")

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

# ── Helpers ───────────────────────────────────────────────────────

def buster_str():
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")

def get(url):
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()

# ── Calendar ──────────────────────────────────────────────────────

def fetch_calendar():
    from f1_calendar import fetch_calendar as _fetch, detect_current_race, detect_last_completed_race, print_calendar
    calendar      = _fetch(HEADERS)
    active, nxt   = detect_current_race(calendar)
    last_complete = detect_last_completed_race(calendar)
    # Price tracker needs: last completed race as "current", active race as "next"
    current = last_complete
    nxt     = active if active and (last_complete is None or active["matchday"] != last_complete["matchday"]) else nxt
    # print_calendar called from main() after --race resolved, so markers are correct
    return calendar, current, nxt, print_calendar

# ── Fetch prices ──────────────────────────────────────────────────

def fetch_player_stats(player_id):
    url = f"{BASE_FEED}/popup/playerstats_{player_id}.json?buster={buster_str()}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        def _total_pts(gd):
            """Extract the Total points value from StatsWise array."""
            for s in gd.get("StatsWise", []):
                if s.get("Event") == "Total":
                    return s.get("Value")
            return None

        rows = {}
        cumulative = 0
        for gd in sorted(data["Value"]["GamedayWiseStats"], key=lambda x: x["GamedayId"]):
            pts = _total_pts(gd) if gd.get("IsPlayed") else None
            if pts is not None:
                cumulative += pts
            rows[gd["GamedayId"]] = {
                "player_value":     gd["PlayerValue"],
                "old_player_value": gd["OldPlayerValue"],
                "is_played":        gd["IsPlayed"],
                "gameday_points":   pts,
                "total_points":     cumulative if gd.get("IsPlayed") else None,
            }
        return rows
    except Exception as e:
        print(f"error: {e}")
        return None

def fetch_all_prices(current_race, next_race, existing_index=None):
    matchday      = current_race["matchday"]
    next_matchday = next_race["matchday"] if next_race else matchday + 1

    # Build season high/low per player from all previously saved rows
    season_prices = {}  # pid -> list of prices across all saved races
    if existing_index:
        for (pid, _), row in existing_index.items():
            p = row.get("price_this_race")
            if p is not None:
                season_prices.setdefault(pid, []).append(p)

    all_ids = (
        [(pid, "Driver",      name) for pid, name in DRIVER_MAP.items()] +
        [(pid, "Constructor", name) for pid, name in CONSTRUCTOR_MAP.items()]
    )
    print(f"\n  Fetching prices for {len(all_ids)} players...\n")

    rows = []
    for pid, ptype, name in all_ids:
        print(f"  → {ptype:<12} {name:<25}", end=" ", flush=True)
        gd_data = fetch_player_stats(pid)

        if gd_data is None:
            print("⚠ skipped")
            continue

        cur  = gd_data.get(matchday, {})
        nxt  = gd_data.get(next_matchday, {})

        price_this     = cur.get("player_value")
        price_prev     = cur.get("old_player_value")
        price_next     = nxt.get("player_value")
        price_next_old = nxt.get("old_player_value")
        gameday_pts    = cur.get("gameday_points")
        total_pts      = cur.get("total_points")

        change = round(price_next - price_next_old, 2) \
                 if price_next is not None and price_next_old is not None else 0.0
        pct    = round((change / price_next_old * 100), 2) if price_next_old else 0.0
        arrow  = f"▲ +{change:.1f}m" if change > 0 else (f"▼ {change:.1f}m" if change < 0 else "─")

        # Season high / low — include today's price in the window
        all_prices = season_prices.get(pid, [])
        if price_this is not None:
            all_prices = all_prices + [price_this]
        season_high = round(max(all_prices), 2) if all_prices else price_this
        season_low  = round(min(all_prices), 2) if all_prices else price_this
        pct_vs_high = round((price_this - season_high) / season_high * 100, 2) \
                      if price_this is not None and season_high else None
        pct_vs_low  = round((price_this - season_low)  / season_low  * 100, 2) \
                      if price_this is not None and season_low  else None
        print(f"${price_this}m → ${price_next}m  {arrow}  |  pts: {gameday_pts}")

        rows.append({
            "player_id":        pid,
            "player_name":      name,
            "type":             ptype,
            "race_number":      matchday,
            "race_name":        current_race["race_name"],
            "race_flag":        current_race["race_flag"],
            "race_date":        current_race["race_date"],
            "next_race_number": next_matchday,
            "next_race_name":   next_race["race_name"] if next_race else None,
            "price_this_race":  price_this,
            "price_prev_race":  price_prev,
            "price_next_race":  price_next,
            "price_change":     change,
            "price_change_pct": pct,
            "gameday_points":   gameday_pts,
            "total_points":     total_pts,
            "season_high":      season_high,
            "season_low":       season_low,
            "pct_vs_high":      pct_vs_high,
            "pct_vs_low":       pct_vs_low,
            "fetched_at":       datetime.now(timezone.utc).isoformat(),
        })
        time.sleep(0.15)

    return rows

# ── Master file ───────────────────────────────────────────────────

def load_master():
    if not os.path.exists(MASTER_FILE):
        return {}
    try:
        with open(MASTER_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {(r["player_id"], r["race_number"]): r for r in data.get("prices", [])}
    except Exception as e:
        print(f"  ⚠ Could not load master: {e}")
        return {}

def save_master(index):
    rows = sorted(index.values(), key=lambda r: (r["race_number"], r["type"], r["player_name"]))
    out  = {
        "updated_at":    datetime.now(timezone.utc).isoformat(),
        "total_rows":    len(rows),
        "races_covered": sorted(set(r["race_number"] for r in rows)),
        "prices":        rows,
    }
    os.makedirs(SAVE_FOLDER, exist_ok=True)
    with open(MASTER_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    return rows

# ── Report ────────────────────────────────────────────────────────

def print_report(rows, current_race, next_race):
    matchday      = current_race["matchday"]
    next_matchday = next_race["matchday"] if next_race else matchday + 1
    next_name     = next_race["race_name"] if next_race else f"Race {next_matchday}"

    def section(title, items):
        print(f"\n{'═'*90}")
        print(f"  {title}")
        print(f"{'═'*90}")
        print(f"  {'Name':<25}  {'Race {}'.format(matchday):>8}  {'Race {}'.format(next_matchday):>8}  {'Change':>12}  {'Chg%':>6}  {'GD Pts':>7}  {'Season':>7}  {'vs High':>8}  {'vs Low':>8}")
        print(f"  {'─'*25}  {'─'*8}  {'─'*8}  {'─'*12}  {'─'*6}  {'─'*7}  {'─'*7}  {'─'*8}  {'─'*8}")
        for r in sorted(items, key=lambda x: -(x["price_next_race"] or 0)):
            cs   = f"${r['price_this_race']:.1f}m"  if r["price_this_race"]  is not None else "?"
            ns   = f"${r['price_next_race']:.1f}m"  if r["price_next_race"]  is not None else "?"
            chg  = r["price_change"]
            chs  = f"▲ +{chg:.1f}m" if chg > 0 else (f"▼ {chg:.1f}m" if chg < 0 else "─")
            pct  = f"{r['price_change_pct']:+.1f}%" if chg != 0 else ""
            gpts = f"{r['gameday_points']:.0f}"     if r.get("gameday_points") is not None else "─"
            tot  = f"{r['total_points']:.0f}"       if r.get("total_points")   is not None else "─"
            vh   = f"{r['pct_vs_high']:+.1f}%"      if r.get("pct_vs_high")    is not None else "─"
            vl   = f"{r['pct_vs_low']:+.1f}%"       if r.get("pct_vs_low")     is not None else "─"
            print(f"  {r['player_name']:<25}  {cs:>8}  {ns:>8}  {chs:>12}  {pct:>6}  {gpts:>7}  {tot:>7}  {vh:>8}  {vl:>8}")

    drivers      = [r for r in rows if r["type"] == "Driver"]
    constructors = [r for r in rows if r["type"] == "Constructor"]

    print(f"\n\n{'═'*90}")
    print(f"  PRICE REPORT — After {current_race['race_flag']} {current_race['race_name']}")
    print(f"  Prices heading into {next_name}")
    print(f"{'═'*90}")

    section("DRIVERS", drivers)
    section("CONSTRUCTORS", constructors)

    changed = [r for r in rows if r["price_change"] != 0]
    rises   = sorted([r for r in changed if r["price_change"] > 0], key=lambda x: -x["price_change"])
    drops   = sorted([r for r in changed if r["price_change"] < 0], key=lambda x:  x["price_change"])

    print(f"\n{'═'*90}")
    print(f"  SUMMARY — {len(changed)} change(s) into {next_name}")
    print(f"{'═'*90}")
    if rises:
        print(f"\n  📈 Rises ({len(rises)}):")
        for r in rises:
            print(f"     ▲ {r['player_name']:<25}  ${r['price_this_race']:.1f}m → ${r['price_next_race']:.1f}m  (+{r['price_change']:.1f}m)")
    if drops:
        print(f"\n  📉 Drops ({len(drops)}):")
        for r in drops:
            print(f"     ▼ {r['player_name']:<25}  ${r['price_this_race']:.1f}m → ${r['price_next_race']:.1f}m  ({r['price_change']:.1f}m)")
    if not changed:
        print("  No changes — prices may not have updated yet after the race.")

# ── Main ──────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="F1 Fantasy — Fetch driver & constructor price changes",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog="""
OUTPUT:
  prices_master.json  (in your configured save_folder)
  One row per player per race — safe to run multiple times (upserts)

EXAMPLES:
  python f1_price_tracker.py             auto-detect latest completed race
  python f1_price_tracker.py --race 1    fetch (or re-fetch) Race 1
  python f1_price_tracker.py -r 3        short form
        """
    )
    parser.add_argument(
        "--race", "-r",
        type=int,
        default=None,
        metavar="N",
        help="Race number to fetch (e.g. --race 1). Defaults to latest completed race.",
    )
    args, _ = parser.parse_known_args()

    print("=" * 68)
    print("  F1 FANTASY — PRICE TRACKER")
    print("=" * 68)

    if not COOKIE:
        print("\n⚠️  No cookie loaded. Check cookie.txt.")
        return

    print("\n  Fetching calendar...")
    calendar, auto_current, auto_next, print_calendar = fetch_calendar()

    # ── Race selection ──────────────────────────────────────────
    if args.race is not None:
        race_lookup = {r["matchday"]: r for r in calendar}
        if args.race not in race_lookup:
            print_calendar(calendar, auto_current, auto_next)
            available = sorted(race_lookup.keys())
            print(f"\n⚠️  Race {args.race} not found in calendar.")
            print(f"   Available race numbers: {available}")
            return
        current_race = race_lookup[args.race]
        next_race    = race_lookup.get(args.race + 1)
        print_calendar(calendar, current_race, next_race)
        print(f"\n  ℹ️  Manual override — fetching GD{args.race}: {current_race['race_name']}")
    else:
        current_race = auto_current
        next_race    = auto_next
        print_calendar(calendar, current_race, next_race)

    if not current_race:
        print("\n⚠️  No race played yet. Run after the first race.")
        return

    print(f"\n  ✅ Current: GD{current_race['matchday']} — {current_race['race_name']}")
    if next_race:
        print(f"  ✅ Next:    GD{next_race['matchday']} — {next_race['race_name']}")

    index    = load_master()
    existing = sum(1 for (_, r) in index if r == current_race["matchday"])
    if existing:
        print(f"\n  ℹ️  {existing} existing rows for GD{current_race['matchday']} will be replaced")

    rows = fetch_all_prices(current_race, next_race, existing_index=index)
    if not rows:
        print("\n⚠️  Nothing fetched.")
        return

    for row in rows:
        index[(row["player_id"], row["race_number"])] = row

    all_rows = save_master(index)
    print(f"\n  ✅ Saved: {MASTER_FILE}")
    print(f"     Rows: {len(all_rows)}  |  Races: {sorted(set(r['race_number'] for r in all_rows))}")

    print_report(rows, current_race, next_race)
    print(f"\n✅ Done! Tableau join: player_id OR player_name")


if __name__ == "__main__":
    main()


# Allow calling as module from run_all.py
run = main
