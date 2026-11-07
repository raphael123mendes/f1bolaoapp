"""
validate.py — compare the published data with F1 Fantasy's own league tables
============================================================================
Run after build_fantasy_f1.py (the results workflow does this automatically):

    python validate.py              # checks ../results.json

Checks
  1. Race points + race rank of every player in every race
     vs the game's race leaderboard for that gameday.
  2. Season total + season rank after the latest race
     vs the game's current league table.

Uses public F1 Fantasy feeds (no cookie needed). Only WARNS — it never blocks
publishing. Results are printed and, on GitHub Actions, added to the run summary.
"""

import json
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT))
import season  # noqa: E402

BASE_FEED = "https://fantasy.formula1.com/feeds/leaderboard/privateleague"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://fantasy.formula1.com/"}


def league_id():
    with open(Path(__file__).parent / "config.json", encoding="utf-8") as f:
        return json.load(f)["league_id"]


def fetch_board(name):
    """name: 'list_1_<league>_0_1' (season) or 'list_2_<league>_<gd>_1' (race)."""
    url = f"{BASE_FEED}/{name}.json?buster={int(time.time())}"
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()["Value"]["leaderboard"]


def board_map(rows):
    return {r["user_name"]: (float(r["cur_points"]), int(r["cur_rank"])) for r in rows}


def validate(results, calendar, fetch, league):
    """Return (problems, checked) — problems is a list of strings."""
    gd_of_round = {r["round"]: r["gameday_id"] for r in calendar["rounds"]}
    races = sorted({r["race_number"] for r in results})
    problems, checked = [], 0

    # 1. Race points + race rank, every race
    for rn in races:
        gd = gd_of_round.get(rn, rn)
        try:
            official = board_map(fetch(f"list_2_{league}_{gd}_1"))
        except Exception as e:
            problems.append(f"R{rn}: could not load the game's race leaderboard ({e})")
            continue
        for r in (x for x in results if x["race_number"] == rn):
            off = official.get(r["user_name"])
            if off is None:
                problems.append(f"R{rn} {r['user_name']}: not in the game's race leaderboard")
                continue
            checked += 1
            if abs(off[0] - r["race_points"]) > 0.01:
                problems.append(f"R{rn} {r['user_name']}: race points {r['race_points']:.0f}, game says {off[0]:.0f}")
            if off[1] != r["race_rank"]:
                problems.append(f"R{rn} {r['user_name']}: race rank P{r['race_rank']}, game says P{off[1]}")

    # 2. Season total + rank after the latest race
    last = races[-1]
    try:
        official = board_map(fetch(f"list_1_{league}_0_1"))
        for r in (x for x in results if x["race_number"] == last):
            off = official.get(r["user_name"])
            if off is None:
                problems.append(f"Season {r['user_name']}: not in the game's league table")
                continue
            checked += 1
            if abs(off[0] - r["season_points"]) > 0.01:
                problems.append(f"Season {r['user_name']}: total {r['season_points']:.0f}, game says {off[0]:.0f}"
                                " (normal during a race weekend — the game already counts live points)")
            if off[1] != r["season_rank"]:
                problems.append(f"Season {r['user_name']}: rank P{r['season_rank']}, game says P{off[1]}")
    except Exception as e:
        problems.append(f"Season: could not load the game's league table ({e})")

    return problems, checked


def report(problems, checked, races):
    if problems:
        head = f"⚠ Data check: {len(problems)} difference(s) vs F1 Fantasy ({checked} values checked, {races} races)"
        lines = [head] + [f"  - {p}" for p in problems]
    else:
        head = f"✅ Data check: all {checked} values match F1 Fantasy ({races} races)"
        lines = [head]
    print("\n".join(lines))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"### {head}\n")
            for p in problems:
                f.write(f"- {p}\n")


def main():
    results_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "results.json"
    with open(results_path, encoding="utf-8") as f:
        results = json.load(f)
    problems, checked = validate(results, season.load_calendar(), fetch_board, league_id())
    report(problems, checked, len({r["race_number"] for r in results}))


if __name__ == "__main__":
    main()
