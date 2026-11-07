"""One-off: add game-reported penalties to existing race_XX_*.json files.

Usage:  python backfill_penalties.py data
Reads data/penalties_backfill.json (captured from the F1 Fantasy API) and
writes the same penalty fields that f1_fantasy_league.get_penalties() now
saves for every new race.
"""
import glob, json, os, sys

folder = sys.argv[1] if len(sys.argv) > 1 else "data"
src = json.load(open(os.path.join(folder, "penalties_backfill.json"), encoding="utf-8"))["teams"]

for path in sorted(glob.glob(os.path.join(folder, "race_*.json"))):
    entries = json.load(open(path, encoding="utf-8"))
    changed = 0
    for e in entries:
        row = src.get(e["user_name"], {}).get(str(e["race_number"]))
        if not row:
            continue
        subs, allowed, left, cost, inactive = row
        extra = -left if left < 0 else 0
        e.update({
            "transfers_made": subs, "transfers_allowed": allowed,
            "extra_transfers": extra, "extra_transfer_cost": cost,
            "transfer_penalty": -extra * cost, "inactive_penalty": inactive,
            "penalty_points": -extra * cost + inactive,
        })
        changed += 1
        if e["penalty_points"]:
            print(f"  R{e['race_number']:>2} {e['user_name']:<22} "
                  f"transfers {e['transfer_penalty']:>4}  inactive {inactive:>4}")
    json.dump(entries, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"{os.path.basename(path)}: {changed} teams updated")
