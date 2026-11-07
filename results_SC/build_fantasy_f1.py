"""
Fantasy F1 – dataset builder
============================
Builds the website JSON files from the per-race files in results_SC/data/.

Usage:
  python build_fantasy_f1.py [data_folder] [output_folder]
      data_folder   default: results_SC/data   (race_NN_*.json, prices_master.json)
      output_folder default: repo root         (the files the website reads)

Outputs (written straight to the repo root — no second copy is kept):
  races.json        (generated from ../calendar/<season>.json)
  prices.json
  results.json
  picks.json
  breakdowns.json
"""

import json, re, sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))
import season   # master calendar: calendar/<season>.json (season set in config.json)

FOLDER       = Path(__file__).resolve().parent / "data"     # inputs
OUT_DIR      = Path(__file__).resolve().parent.parent       # repo root: website files

def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

BREAKDOWN_RE = re.compile(r'^(.+?):\s*([+-]?\d+)$')

PRIZES = {1: 19.25, 2: 11.55, 3: 7.70}

def calc_prizes(results_for_race):
    from collections import defaultdict
    by_rank = defaultdict(list)
    for r in results_for_race:
        by_rank[r["race_rank"]].append(r["user_name"])
    out = {}
    for rank in [1, 2, 3]:
        users = by_rank[rank]
        if not users:
            continue
        pool = sum(PRIZES[p] for p in range(rank, rank + len(users)) if p in PRIZES)
        share = round(pool / len(users), 4)
        for user in users:
            out[user] = share
    return out

# ── Data builder ───────────────────────────────────────────────────────────────
def build_data(schedule, prices_data, race_files):
    """Build all four datasets from source files. Returns dict of lists."""

    # ── Races: generated from the master calendar (calendar/<season>.json) ───
    races = season.races_for_site(schedule)

    # ── Prices: one row per player × race ─────────────────────────────────────
    prices = prices_data["prices"]

    # ── Price lookup for budget calc ──────────────────────────────────────────
    price_lookup = {
        (str(p["race_number"]), str(p["player_id"])): p
        for p in prices
    }

    # ── Results, Picks, Breakdowns from race files ─────────────────────────────
    results    = []
    picks      = []
    breakdowns = []

    # First pass: collect all race entries grouped by race_number
    # so we can compute rank_change and prizes per race
    all_races_entries = {}   # race_number -> list of raw entries
    for path in sorted(race_files):
        data = load_json(path)
        if data:
            all_races_entries[data[0]["race_number"]] = data

    # ── Season points & rank: validate the snapshot taken at each race ────────
    # The API's overall total is a "right now" value, so a race fetched late
    # (after the next weekend started) carries later totals — e.g. R4, R6.
    # Rule: keep the snapshot when it matches previous total + this race's GD
    # points within SNAPSHOT_TOLERANCE (small steward corrections, like Gasly
    # at Monaco); otherwise use previous total + GD points and flag the race.
    SNAPSHOT_TOLERANCE = 10
    prev_total = {}
    for race_number in sorted(all_races_entries):
        entries = all_races_entries[race_number]
        replaced = []
        for e in entries:
            expected = prev_total.get(e["user_name"], 0) + (e["gd_points"] or 0)
            snap = e.get("total_points")
            if snap is None or abs(snap - expected) > SNAPSHOT_TOLERANCE:
                replaced.append(f'{e["user_name"]} {snap}->{expected}')
                e["total_points"] = expected
        if replaced:
            # Totals changed → rank from the corrected totals (ties share a rank)
            ordered = sorted(entries, key=lambda e: -e["total_points"])
            for i, e in enumerate(ordered):
                tied = i > 0 and e["total_points"] == ordered[i - 1]["total_points"]
                e["rank"] = ordered[i - 1]["rank"] if tied else i + 1
            print(f"  ⚠ R{race_number}: season snapshot taken late — rebuilt from GD "
                  f"points for {len(replaced)} team(s): {', '.join(replaced)}")
        for e in entries:
            prev_total[e["user_name"]] = e["total_points"]

    for race_number in sorted(all_races_entries):
        race_data    = all_races_entries[race_number]
        prev_number  = race_number - 1
        prev_data    = all_races_entries.get(prev_number, [])
        prev_rank    = {e["user_name"]: e["rank"] for e in prev_data}

        # Build partial results list for prize calc (uses race_rank = live_gd_rank)
        partial = [{"user_name": e["user_name"], "race_rank": e["live_gd_rank"]} for e in race_data]
        prizes  = calc_prizes(partial)

        for e in race_data:
            budget_remaining = e["budget_remaining"]
            team_value       = e["team_value"]
            current_budget   = round(team_value + budget_remaining, 2)

            # Compute total new budget across all picks
            new_budget = current_budget
            for pick in e.get("picks", []):
                key = (str(e["race_number"]), str(pick["id"]))
                pdata = price_lookup.get(key, {})
                pt = pdata.get("price_this_race")
                pn = pdata.get("price_next_race")
                if pt is not None and pn is not None:
                    new_budget += (pn - pt)
            new_budget = round(new_budget, 2)

            # rank_change: positive = moved up, negative = dropped
            # Race 1 has no previous so 0
            if e["user_name"] in prev_rank:
                rank_change = prev_rank[e["user_name"]] - e["rank"]
            else:
                rank_change = 0

            # ── Results row (one per user × race) ─────────────────────────────
            results.append({
                "race_number":      e["race_number"],
                "race_name":        e["race_name"],
                "race_flag":        e.get("race_flag", ""),
                "race_date":        e["race_date"],
                "season_rank":      e["rank"],
                "race_rank":        e["live_gd_rank"],
                "rank_change":      rank_change,
                "team_name":        e["team_name"],
                "user_name":        e["user_name"],
                "season_points":    e["total_points"],
                "race_points":      e["gd_points"],
                "budget_remaining": budget_remaining,
                "team_value":       team_value,
                "cards_used":       "; ".join(e.get("cards_used", [])),
                "current_budget":   current_budget,
                "new_budget":       new_budget,
                "race_prize":       prizes.get(e["user_name"], 0),
                # Penalties as shown on the F1 Fantasy team screen.
                # Informational only: race_points (final gdpoints) already include them.
                "extra_transfers":     e.get("extra_transfers", 0),
                "transfer_penalty":    e.get("transfer_penalty", 0),
                "inactive_penalty":    e.get("inactive_penalty", 0),
                "penalty_points":      e.get("penalty_points", 0),
            })

            # ── Picks rows (one per pick) ──────────────────────────────────────
            for pick in e.get("picks", []):
                key   = (str(e["race_number"]), str(pick["id"]))
                pdata = price_lookup.get(key, {})
                picks.append({
                    "race_number":    e["race_number"],
                    "race_name":      e["race_name"],
                    "race_flag":      e.get("race_flag", ""),
                    "race_date":      e["race_date"],
                    "season_rank":    e["rank"],
                    "race_rank":      e["live_gd_rank"],
                    "team_name":      e["team_name"],
                    "user_name":      e["user_name"],
                    "cards_used":     "; ".join(e.get("cards_used", [])),
                    "pick_id":        pick["id"],
                    "pick_name":      pick["name"],
                    "pick_type":      pick["type"],
                    "is_captain":     pick["is_captain"],
                    "is_megacap":     pick.get("is_megacap", False),
                    "pick_points_gd": pick["points_gd"],
                    "price_this_race": pdata.get("price_this_race"),
                    "price_next_race": pdata.get("price_next_race"),
                })

                # ── Breakdowns rows (one per breakdown line) ───────────────────
                for bd in pick.get("breakdown", []):
                    m = BREAKDOWN_RE.match(bd.strip())
                    breakdowns.append({
                        "race_number":      e["race_number"],
                        "race_name":        e["race_name"],
                        "race_flag":        e.get("race_flag", ""),
                        "race_date":        e["race_date"],
                        "season_rank":      e["rank"],
                        "race_rank":        e["live_gd_rank"],
                        "team_name":        e["team_name"],
                        "user_name":        e["user_name"],
                        "pick_id":          pick["id"],
                        "pick_name":        pick["name"],
                        "pick_type":        pick["type"],
                        "is_captain":       pick["is_captain"],
                        "pick_points_gd":   pick["points_gd"],
                        "breakdown_category": m.group(1).strip() if m else bd.strip(),
                        "breakdown_points":   int(m.group(2)) if m else None,
                    })

    return {"races": races, "prices": prices, "results": results,
            "picks": picks, "breakdowns": breakdowns}


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    folder  = Path(sys.argv[1]) if len(sys.argv) > 1 else FOLDER
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else OUT_DIR
    if not folder.exists():
        print(f"ERROR: Folder not found: {folder}")
        sys.exit(1)

    prices_path   = folder / "prices_master.json"
    race_files    = sorted(folder.glob("race_*.json"))
    schedule      = season.load_calendar()
    print(f"Calendar: {season.calendar_path()}")

    for p in [prices_path]:
        if not p.exists():
            print(f"ERROR: Missing file: {p.name}")
            sys.exit(1)

    print(f"Fantasy F1 Dataset Builder  |  folder: {folder}")
    print(f"Race files: {len(race_files)}")

    data = build_data(schedule, load_json(prices_path), race_files)

    # ── JSON exports ───────────────────────────────────────────────────────────
    print(f"Exporting JSON files to {out_dir} ...")
    for name, rows in data.items():
        save_json(out_dir / f"{name}.json", rows)

    print("\nDone! Outputs:")
    for name, rows in data.items():
        print(f"  {name+'.json':<20} {len(rows):>4} rows")


if __name__ == "__main__":
    main()
