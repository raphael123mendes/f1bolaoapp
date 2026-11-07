"""
simulate_session_mode.py — dry-run session mode against scripted sessions
=========================================================================
No network, no publishing: a fake clock steps one minute at a time and a
scripted timeline plays the part of the F1 Fantasy API
(session status + how many qualifying parts have ended).

    python bot/simulate_session_mode.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import session_mode  # noqa: E402

START = datetime(2026, 11, 8, 17, 0, tzinfo=timezone.utc)


def timeline(*segments):
    """segments: (from_minute, status, q_parts_done). Returns signals at minute m."""
    def at(m):
        sig = {"status": "upcoming", "q_parts_done": 0}
        for frm, status, parts in segments:
            if m >= frm:
                sig = {"status": status, "q_parts_done": parts}
        return sig
    return at


SCENARIOS = {
    "Qualifying — normal": ("qualifying", timeline(
        (0, "live", 0), (19, "live", 1), (41, "live", 2), (62, "complete", 2))),
    "Qualifying — red flag in Q1 (like Brazil), everything 25 min late": ("qualifying", timeline(
        (0, "live", 0), (44, "live", 1), (66, "live", 2), (88, "complete", 2))),
    "Qualifying — API unreachable": ("qualifying", lambda m: {"status": None, "q_parts_done": None}),
    "Qualifying — Q2 bonus never appears (only complete)": ("qualifying", timeline(
        (0, "live", 0), (62, "complete", 0))),
    "Sprint — normal": ("sprint", timeline((0, "live", None), (34, "complete", None))),
    "Race — normal": ("race", timeline((0, "live", None), (97, "complete", None))),
    "Race — 40 min red flag (status stays live)": ("race", timeline(
        (0, "live", None), (137, "complete", None))),
    "Race — API unreachable": ("race", lambda m: {"status": None, "q_parts_done": None}),
}

LATE_OR_EARLY = {
    "Race — started by hand 2 h early": ("race", SCENARIOS["Race — normal"][1], -120),
    "Race — started late at +50 min": ("race", SCENARIOS["Race — normal"][1], 50),
    "Race — started after it finished (+110 min)": ("race", SCENARIOS["Race — normal"][1], 110),
    "Qualifying — started during Q2 (+30 min)": ("qualifying", SCENARIOS["Qualifying — normal"][1], 30),
}


def run_scenario(name, kind, signals_at, begin=-30):
    clock = {"t": START + timedelta(minutes=begin)}
    published = []

    def minute():
        return int((clock["t"] - START).total_seconds() // 60)

    session_mode.run(
        kind, START,
        publish=lambda reasons, final, phase: published.append((minute(), reasons, final, phase)),
        signals_fn=lambda: signals_at(minute()),
        now_fn=lambda: clock["t"],
        sleep_fn=lambda s: clock.__setitem__("t", clock["t"] + timedelta(seconds=s)),
        log=lambda *_: None)
    print(f"\n=== {name} ===")
    for m, reasons, final, phase in published:
        print(f"  +{m:3d} min  [{phase:>9}]  {' + '.join(reasons)}")
    print(f"  → {len(published)} updates, bot stops at +{minute()} min")
    return published


if __name__ == "__main__":
    for name, (kind, tl) in SCENARIOS.items():
        run_scenario(name, kind, tl)
    for name, (kind, tl, begin) in LATE_OR_EARLY.items():
        run_scenario(name, kind, tl, begin=begin)
