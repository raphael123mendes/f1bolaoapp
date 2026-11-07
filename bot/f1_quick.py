"""
F1 Fantasy Quick Standings — live results bot
==============================================
- Auto-detects current race from the F1 Fantasy schedule feed
- Fetches per-team data from /services/user/opponentteam/ endpoints:
    opponentgamedayplayerteamget → picks with iscaptain, ismgcaptain, gdpoints
- Calculates accurate live GD points using component-level formula:
    points = sum(component * captain_mult * megacap_mult * no_neg_factor)
    where: captain_mult  = 2 if iscaptain,   else 1
           megacap_mult  = 3 if ismgcaptain, else 1  (stacks: both = 6x)
           no_neg_factor = 0 if No Negative card AND component < 0, else 1
- Publishes live/*.json for the website only when scores change, and once more
  after the session ends (final):
    picks.json      → one row per pick per team per race
    breakdowns.json → one row per scoring category per pick per team per race
    (races.json for the website is generated from calendar/<season>.json)
- Runs on GitHub Actions — no laptop needed

FILES NEEDED (same folder):
  f1_quick.py           <- this script
  config.json           <- your settings
  cookie.txt            <- your F1 Fantasy session cookie
  ../calendar/<season>.json <- master race calendar (season set in ../config.json)
  last_standings.json   <- auto-created, tracks last sent standings

config.json format:
{
  "league_id":   "1692401",
  "league_name": "Bolão F1",
  "data_repo": {
    "owner": "your-github-username",
    "repo":  "your-data-repo-name",
    "branch": "main"
  }
}
"""

import hashlib
import requests
import json
import time
import os
import sys
from datetime import datetime, timezone, timedelta
from urllib.parse import unquote

# ── Load config ───────────────────────────────────────────────────

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

def load_config():
    if not os.path.exists(CONFIG_FILE):
        print("ERROR: config.json not found!")
        sys.exit(1)
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return json.load(f)

CONFIG        = load_config()
LEAGUE_ID     = CONFIG["league_id"]
LEAGUE_NAME   = CONFIG["league_name"]
NICKNAMES     = CONFIG.get("nicknames", {})

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import season   # master calendar: calendar/<season>.json (season set in config.json)

BASE_FEEDS    = "https://fantasy.formula1.com/feeds"
BASE_SERVICES = "https://fantasy.formula1.com/services/user"


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept":     "application/json, text/plain, */*",
    "Referer":    "https://fantasy.formula1.com/",
}

COOKIE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookie.txt")

def load_cookie():
    cookie = os.environ.get("F1_COOKIE", "").strip()
    if cookie:
        print(f"  Cookie loaded from F1_COOKIE secret ({len(cookie)} chars)")
        return cookie
    # Fallback to file for local dev
    if os.path.exists(COOKIE_FILE):
        cookie = open(COOKIE_FILE, encoding="utf-8").read().strip()
        if cookie:
            print(f"  Cookie loaded from file ({len(cookie)} chars)")
            return cookie
    print("  WARNING: no cookie found (set F1_COOKIE secret or cookie.txt).")
    return None

COOKIE = load_cookie()
AUTH_HEADERS = {**HEADERS, "Cookie": COOKIE} if COOKIE else None


class CookieExpired(Exception):
    """F1 Fantasy rejected the session cookie (HTTP 401/403)."""


def cookie_expired_exit(detail=""):
    print("\n" + "!" * 60)
    print("  F1 FANTASY COOKIE EXPIRED OR INVALID" + (f" ({detail})" if detail else ""))
    print("  Nothing was published. Refresh the F1_COOKIE secret")
    print("  (Actions -> F1 Refresh Cookie, or paste a new cookie manually).")
    print("!" * 60)
    sys.exit(2)

FLAG_MAP = {
    "Australia":      "🇦🇺", "China":          "🇨🇳", "Japan":          "🇯🇵",
    "Bahrain":        "🇧🇭", "Saudi Arabia":   "🇸🇦", "United States":  "🇺🇸",
    "Italy":          "🇮🇹", "Monaco":         "🇲🇨", "Canada":         "🇨🇦",
    "Spain":          "🇪🇸", "Austria":        "🇦🇹", "United Kingdom": "🇬🇧",
    "Hungary":        "🇭🇺", "Belgium":        "🇧🇪", "Netherlands":    "🇳🇱",
    "Azerbaijan":     "🇦🇿", "Singapore":      "🇸🇬", "Mexico":         "🇲🇽",
    "Brazil":         "🇧🇷", "UAE":            "🇦🇪", "Abu Dhabi":      "🇦🇪",
    "Qatar":          "🇶🇦", "Las Vegas":      "🇺🇸", "Miami":          "🇺🇸",
    "Malaysia":       "🇲🇾",
}

CARD_FIELDS = {
    "isnonigativetaken": "NoNeg",
    "isautopilottaken":  "Auto",
    "islimitlesstaken":  "Limitless",
    "iswildcardtaken":   "WC",
    "isextradrstaken":   "ExDRS",
    "isfinalfixtaken":   "FinFix",
}

RACE_PHASE_CATEGORIES = {
    'race position', 'race position gained', 'race position lost',
    'race fastest lap', 'race not classified', 'race overtake bonus',
    'driver of day', 'fastest pitstop', '2nd fastest pitstop',
}

def _is_race_cat(cat: str) -> bool:
    return cat.strip().lower() in RACE_PHASE_CATEGORIES

def _cap_mult(pid, cap_id):
    return 2 if (cap_id and pid == cap_id) else 1

def _mega_mult(pid, megacap_id):
    return 3 if (megacap_id and pid == megacap_id) else 1

def _finfx_mult(picks, finalfix_in_pid, cap_id, megacap_id):
    in_pick = next((p for p in picks if str(p["id"]) == finalfix_in_pid), None)
    if in_pick is None:
        return 1
    return _cap_mult(in_pick["id"], cap_id) * _mega_mult(in_pick["id"], megacap_id)


# ── Helpers ───────────────────────────────────────────────────────

def buster():
    return int(time.time() * 1000)

def get(url):
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()

def parse_iso(dt_str):
    try:
        dt_str     = dt_str.replace("Z", "+00:00").strip()
        base       = dt_str[:19]
        offset_str = dt_str[19:]
        dt_naive   = datetime.strptime(base, "%Y-%m-%dT%H:%M:%S")
        if offset_str and offset_str[0] in ("+", "-"):
            sign  = 1 if offset_str[0] == "+" else -1
            parts = offset_str[1:].split(":")
            oh, om = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
            tz = timezone(timedelta(hours=sign * oh, minutes=sign * om))
            return dt_naive.replace(tzinfo=tz).astimezone(timezone.utc)
        return dt_naive.replace(tzinfo=timezone.utc)
    except Exception:
        return None


# ── Step 1: Auto-detect current race ─────────────────────────────

def get_current_race(upcoming_within_hours=None):
    """
    Reads the master calendar (calendar/<season>.json) and returns the most relevant
    is_competitive session (qualifying, sprint, race).
    Window: 30 min before session start → 2 hours after session start.
    Cancelled rounds are skipped automatically.
    """
    try:
        schedule = season.load_calendar()
    except Exception as e:
        print(f"  ERROR: {e}")
        return None, None

    now          = datetime.now(timezone.utc)
    SCORED_TYPES = ("qualifying", "sprint", "race")
    sessions     = []

    for r in schedule["rounds"]:
        if r.get("cancelled"):
            continue
        country = r.get("country") or r.get("location", "")
        flag    = next((v for k, v in FLAG_MAP.items() if k.lower() in country.lower()), "🏁")

        # ── GAMEDAY ID FIX ────────────────────────────────────────────────
        # F1 Fantasy assigns gameday_id sequentially across ONLY races that
        # actually happen. When races are cancelled, the sequential IDs shift
        # (e.g. Miami was GD6 but after 2 cancellations it became GD4).
        # We read "gameday_id" from the schedule JSON if present; otherwise
        # fall back to "round" (old behaviour — will be wrong after cancellations).
        gameday_id = r.get("gameday_id", r["round"])
        if "gameday_id" not in r:
            print(f"  WARNING: round {r['round']} ({r.get('name','?')}) has no "
                  f"'gameday_id' field — using round number as fallback. "
                  f"Update calendar/{schedule.get('season')}.json if races were cancelled!")

        for s in r["sessions"]:
            if s["session_type"] not in SCORED_TYPES:
                continue
            start_dt = datetime.fromisoformat(s["utc_start"].replace("Z", "+00:00"))
            sessions.append({
                "meeting_number": r["round"],
                "meeting_name":   r["name"],
                "session_type":   s["session_type"],
                "country":        country,
                "gameday_id":     gameday_id,   # ← now decoupled from round number
                "start_dt":       start_dt,
                "window_start":   start_dt - timedelta(minutes=30),
                "window_end":     start_dt + timedelta(hours=2),
                "flag":           flag,
            })

    # Priority 0: FORCE_ROUND override — pick a specific round by number
    force_round = os.environ.get("FORCE_ROUND", "").strip()
    if force_round:
        matched = [s for s in sessions if str(s["meeting_number"]) == force_round]
        if matched:
            # Prefer race session, then qualifying, then sprint
            order = {"race": 0, "qualifying": 1, "sprint": 2}
            s = min(matched, key=lambda x: order.get(x["session_type"], 9))
            print(f"  FORCE_ROUND={force_round} → {s['meeting_name']} {s['session_type']}")
            return s, -999999   # treat as far past so use_gdpoints=True
        print(f"  WARNING: FORCE_ROUND={force_round} not found in schedule")

    # Priority 1: live right now
    for s in sessions:
        if s["window_start"] <= now <= s["window_end"]:
            return s, 0

    # Manual early start: the next session if it starts within N hours
    if upcoming_within_hours:
        soon = [s for s in sessions
                if now < s["start_dt"] <= now + timedelta(hours=upcoming_within_hours)]
        if soon:
            s = min(soon, key=lambda x: x["start_dt"])
            return s, (s["window_start"] - now).total_seconds()
        return None, None

    # Priority 2: most recently completed
    completed = [s for s in sessions if s["start_dt"] < now]
    if completed:
        s     = max(completed, key=lambda x: x["start_dt"])
        score = -(now - s["window_end"]).total_seconds()
        return s, score

    # Priority 3: next upcoming (skipped when FORCE_RUN=1 — no data available yet)
    if os.environ.get("FORCE_RUN", "0") != "1":
        upcoming = [s for s in sessions if s["start_dt"] >= now]
        if upcoming:
            s     = min(upcoming, key=lambda x: x["start_dt"])
            score = (s["window_start"] - now).total_seconds()
            return s, score

    return None, None


# ── Step 1b: Validate / auto-correct gameday_id from live API ────
# The F1 Fantasy API exposes the current active gameday in mixapi.json.
# We compare it against what the schedule file says. If they differ
# we warn loudly and auto-correct so the run still works.

def validate_gameday_id(race):
    """
    Cross-check race['gameday_id'] against the live F1 Fantasy API.

    mixapi.json contains:
      'gd'  = active/current gameday id (0 if between events)
      'lgd' = last completed gameday id

    Returns the (possibly corrected) int gameday_id and prints a warning
    if the schedule file appears to be out of date after cancellations.
    """
    if race is None:
        return None

    try:
        data  = get(f"{BASE_FEEDS}/live/mixapi.json?buster={buster()}")
        value = data.get("Value", {})
        live_gd = int(value.get("gd",  0) or 0)
        last_gd = int(value.get("lgd", 0) or 0)
        sched_gd = int(race["gameday_id"])

        candidates = [g for g in [live_gd, last_gd] if g > 0]
        if not candidates:
            print("  [validate_gameday_id] No active gameday in API — cannot cross-check.")
            return sched_gd

        if sched_gd in candidates:
            print(f"  [validate_gameday_id] ✓ gameday_id={sched_gd} confirmed by API.")
            return sched_gd

        # Mismatch — auto-correct to nearest API value
        nearest = min(candidates, key=lambda g: abs(g - sched_gd))
        diff    = nearest - sched_gd
        print(f"\n  ⚠️  GAMEDAY ID MISMATCH DETECTED!")
        print(f"     Schedule file says gameday_id={sched_gd} for '{race['meeting_name']}'")
        print(f"     F1 Fantasy API reports live_gd={live_gd}, last_gd={last_gd}")
        print(f"     Difference: {diff:+d}  (likely {abs(diff)} cancelled race(s) shifted IDs)")
        print(f"     → Auto-correcting to gameday_id={nearest} for this run")
        print(f"     → ACTION REQUIRED: update 'gameday_id' in calendar/<season>.json\n")
        return nearest

    except Exception as e:
        print(f"  [validate_gameday_id] Could not reach API ({e}) — using schedule value.")
        return int(race["gameday_id"])


# ── Step 2: Check if we should run and what mode ──────────────────

def should_run(race, score, force=False):
    """
    Returns (ok: bool, mode: str)
      mode = "live"       → inside active window (start-30min to start+2h)
      mode = "post_race"  → inside post-race window (start+2h to start+3h)
      mode = "skip"       → outside all windows
    """
    if race is None:
        print("No race found in schedule.")
        return False, "skip"

    if force:
        print(f"FORCED: {race['meeting_name']} {race['session_type']} — skipping window check.")
        return True, "live"

    now = datetime.now(timezone.utc)

    # Active window: start-30min → start+2h
    if race["window_start"] <= now <= race["window_end"]:
        print(f"LIVE: {race['meeting_name']} {race['session_type']} — in active window.")
        return True, "live"

    # Post-race window: start+2h → start+3h
    post_race_end = race["window_end"] + timedelta(hours=1)
    if race["window_end"] < now <= post_race_end:
        mins_after = int((now - race["window_end"]).total_seconds() / 60)
        print(f"POST-RACE: {race['meeting_name']} {race['session_type']} "
              f"— {mins_after}min after window closed.")
        return True, "post_race"

    print(f"Outside all windows for {race['meeting_name']} {race['session_type']}.")
    print(f"  Active    : {race['window_start'].strftime('%Y-%m-%d %H:%M')} - "
          f"{race['window_end'].strftime('%H:%M')} UTC")
    print(f"  Post-race : {race['window_end'].strftime('%H:%M')} - "
          f"{post_race_end.strftime('%H:%M')} UTC")
    return False, "skip"


# ── Step 3: State management (change detection) ───────────────────

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "last_standings.json")


def load_last_state():
    """Load previously saved standings state from repo."""
    if not os.path.exists(STATE_FILE):
        return None
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"  WARNING: could not read last_standings.json: {e}")
        return None


def _hash_results(results):
    """MD5 of ranked user:points — catches any reorder or point change."""
    raw = "|".join(f"{r['user_name']}:{int(r['points'])}" for r in results)
    return hashlib.md5(raw.encode()).hexdigest()


def save_state(race, results, post_race_sent=False):
    """Persist current standings so next run can detect changes."""
    state = {
        "gameday_id":     str(race["gameday_id"]),
        "session_type":   race["session_type"],
        "ranking_hash":   _hash_results(results),
        "scores":         {r["user_name"]: int(r["points"]) for r in results},
        "post_race_sent": post_race_sent,
        "saved_at":       datetime.now(timezone.utc).isoformat(),
    }
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    print(f"  State saved → {STATE_FILE}")


def scores_changed(race, results, last_state):
    """
    Returns True if standings changed since last run.
    New session → always True.
    Same session → compare ranking hash.
    """
    if last_state is None:
        print("  No previous state — first run, will send.")
        return True

    if (str(last_state.get("gameday_id")) != str(race["gameday_id"]) or
            last_state.get("session_type") != race["session_type"]):
        print(f"  New session detected "
              f"({last_state.get('session_type')}@gd{last_state.get('gameday_id')} → "
              f"{race['session_type']}@gd{race['gameday_id']}) — will send.")
        return True

    if _hash_results(results) != last_state.get("ranking_hash"):
        print("  Standings changed — will send.")
        return True

    print("  Standings unchanged — nothing to publish.")
    return False


# ── Step 4: Fetch team picks + cards ─────────────────────────────

def get_team_details(gameday_id, user_guid, team_no=1):
    if not AUTH_HEADERS:
        return None, [], {}, {}

    url = (f"{BASE_SERVICES}/opponentteam/opponentgamedayplayerteamget"
           f"/1/{user_guid}/1/{gameday_id}/1"
           f"?buster={buster()}")
    try:
        r = requests.get(url, headers=AUTH_HEADERS, timeout=15)
        r.raise_for_status()
        data      = r.json()["Data"]["Value"]
        user_team = data.get("userTeam", [])
        if not user_team:
            return None, [], {}, {}

        team_data  = user_team[0]
        picks      = team_data.get("playerid", [])
        gdpoints   = team_data.get("gdpoints")

        mgcap_pid = str(team_data.get("mgcapplayerid") or "")
        if mgcap_pid:
            for p in picks:
                if str(p["id"]) == mgcap_pid:
                    p["ismgcaptain"] = 1

        cards = {}
        for field, label in CARD_FIELDS.items():
            gd_field = field[2:].replace("taken", "takengd")
            gd_used  = team_data.get(gd_field)
            if gd_used is not None and int(gd_used) == int(gameday_id):
                cards[label] = gd_used

        finalfix_out_pid = str(
            team_data.get("finalfixreplacedplayerid") or
            team_data.get("finalfixoutpid") or
            team_data.get("replacedplayerid") or
            ""
        )
        finalfix_in_pid = str(
            team_data.get("finalfixplayerid") or
            team_data.get("finalfixinpid") or
            next((str(p["id"]) for p in picks
                  if p.get("isfinalfix") or p.get("is_final_fix")), "") or
            ""
        )

        team_info = {
            "usersubs":                        team_data.get("usersubs", 0),
            "usersubsleft":                    team_data.get("usersubsleft"),
            "isaccounting":                    team_data.get("isaccounting"),
            "subsallowed":                     team_data.get("subsallowed", 2),
            "extrasubscost":                   team_data.get("extrasubscost", 10),
            "inactive_driver_penality_points": team_data.get("inactive_driver_penality_points", 0),
            "finalfix_out_pid":               finalfix_out_pid,
            "finalfix_in_pid":                finalfix_in_pid,
        }
        return gdpoints, picks, cards, team_info

    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else None
        if code in (401, 403):
            raise CookieExpired(f"HTTP {code} on team details")
        print(f"    WARNING: could not fetch team details for {user_guid}: {e}")
        return None, [], {}, {}
    except Exception as e:
        print(f"    WARNING: could not fetch team details for {user_guid}: {e}")
        return None, [], {}, {}


def get_player_components(pid, gameday_id, lv):
    """Returns list of (category, value) tuples for a player in a gameday.
    Skips the 'Total' row — that is a sum, not a component.
    """
    try:
        data = get(f"{BASE_FEEDS}/popup/playerstats_{pid}.json?buster={lv}")
        for gd in data["Value"]["GamedayWiseStats"]:
            if int(gd["GamedayId"]) == int(gameday_id):
                components = []
                for s in gd["StatsWise"]:
                    if s["Event"] == "Total":
                        continue
                    try:
                        components.append((s["Event"], int(s["Value"])))
                    except Exception:
                        pass
                return components
    except Exception:
        pass
    return []


# ── Step 5: Build standings ───────────────────────────────────────

# Inactive-driver deduction per session, as the game charges it to the
# overall score (matches final values: -25 normal weekend, -35 sprint weekend).
INACTIVE_SESSION_PENALTY = {"qualifying": -5, "sprint": -10, "race": -20}


def sessions_started_for(race):
    """Scored session types of this round that have already started (from the schedule)."""
    if not race:
        return []
    try:
        rounds = season.load_calendar()["rounds"]
        rnd = next(r for r in rounds if r["round"] == race["meeting_number"])
    except Exception:
        return [race.get("session_type", "race")]
    now = datetime.now(timezone.utc)
    return [s["session_type"] for s in rnd["sessions"]
            if s["session_type"] in INACTIVE_SESSION_PENALTY
            and datetime.fromisoformat(s["utc_start"].replace("Z", "+00:00")) <= now]


def get_inactive_ids(gameday_id):
    """Driver ids the game itself flags as inactive (IsActive == "0") for this gameday."""
    try:
        data = get(f"{BASE_FEEDS}/drivers/{gameday_id}_en.json?buster={buster()}")
        return {str(p["PlayerId"]) for p in data["Data"]["Value"]
                if str(p.get("IsActive", "1")) == "0" and p.get("Skill", 1) == 1}
    except Exception as e:
        print(f"  WARNING: could not load inactive flags for GD{gameday_id}: {e}")
        return set()


def live_penalties(team_data, picks, inactive_ids, sessions_started):
    """Penalties for a LIVE (not yet finalised) gameday.

    Transfers: the game's own count (usersubsleft < 0 = transfers over the limit).
    Inactive driver: the game's own value when it is already published; before the
    gameday is finalised the API still returns 0, so we use the game's inactive flag
    on the picked driver and the per-session deduction for sessions already started.
    """
    cost      = team_data.get("extrasubscost", 10) or 10
    subs_left = team_data.get("usersubsleft")
    if subs_left is None:      # field missing → fall back to made vs allowed
        extra = max(0, (team_data.get("usersubs") or 0) - (team_data.get("subsallowed") or 0))
    else:
        extra = max(0, -int(subs_left))
    transfer_pen = -extra * cost

    inactive_pen = int(team_data.get("inactive_driver_penality_points") or 0)
    source = "game"
    if not inactive_pen:
        n_inactive = sum(1 for p in picks if str(p["id"]) in inactive_ids)
        if n_inactive:
            inactive_pen = n_inactive * sum(INACTIVE_SESSION_PENALTY.get(t, 0)
                                            for t in sessions_started)
            source = f"provisional: {n_inactive} inactive x {sessions_started}"
    return transfer_pen, inactive_pen, source


def get_standings(gameday_id, score=0, race=None):
    # Use finalised gdpoints when: forced run, or session ended >2h ago (7200s)
    force_flag = os.environ.get("FORCE_RUN", "0") == "1"
    # gdpoints is the game's final result (penalties already included), but
    # only once the race itself is done. After qualifying/sprint the game has
    # not applied penalties yet, so keep calculating live until then.
    is_race_session = (race or {}).get("session_type", "race") == "race"
    use_gdpoints = force_flag or (is_race_session and score is not None
                                  and score < 0 and abs(score) >= 7200)
    sessions_started = sessions_started_for(race)
    print(f"  Mode: {'final gdpoints' if use_gdpoints else 'live calculation'} | "
          f"sessions started: {sessions_started}")
    if not AUTH_HEADERS:
        print("  FATAL: cookie.txt is missing or empty.")
        sys.exit(1)

    lv = get(f"{BASE_FEEDS}/live/mixapi.json?buster={buster()}")["Value"]["lv"]

    player_names     = {}   # pid -> full display name  (for JSON export)
    player_tlas      = {}   # pid -> TLA                 (short driver code)
    player_skills    = {}   # pid -> 1 (Driver) | 2 (Constructor)
    try:
        drivers_data = get(f"{BASE_FEEDS}/drivers/1_en.json?buster={buster()}")
        for p in drivers_data["Data"]["Value"]:
            pid  = str(p.get("PlayerId", ""))
            full = p.get("DisplayName") or p.get("DriverTLA") or pid
            tla  = p.get("DriverTLA")  or p.get("DisplayName") or pid
            player_names[pid]  = full
            player_tlas[pid]   = tla
            player_skills[pid] = p.get("Skill", 1)
        print(f"  Player names loaded: {len(player_names)}")
    except Exception as e:
        print(f"  WARNING: could not load player names: {e}")

    inactive_ids = get_inactive_ids(gameday_id)
    if inactive_ids:
        print(f"  Inactive drivers (game flag): {sorted(inactive_ids)}")

    leaderboard = get(
        f"{BASE_FEEDS}/leaderboard/privateleague/list_2_{LEAGUE_ID}_1_1.json"
        f"?buster={buster()}"
    )["Value"]["leaderboard"]

    results = []

    for season_rank_0, entry in enumerate(leaderboard):
        team_name   = unquote(entry["team_name"])
        user_name   = entry["user_name"]
        user_guid   = entry["user_guid"]
        team_no     = entry.get("team_no", 1)
        season_rank = season_rank_0 + 1   # leaderboard is already sorted by season total

        print(f"  Fetching: {user_name} ({team_name})")

        gdpoints, picks, cards, team_data = get_team_details(gameday_id, user_guid, team_no)
        time.sleep(0.15)

        no_neg_gd  = cards.get("NoNeg")
        has_no_neg = (no_neg_gd is not None and int(no_neg_gd) == int(gameday_id))
        finfx_gd      = cards.get("FinFix")
        has_final_fix = (finfx_gd is not None and int(finfx_gd) == int(gameday_id))
        cap_id     = next((p["id"] for p in picks if p.get("iscaptain")),   None)
        megacap_id = next((p["id"] for p in picks if p.get("ismgcaptain")), None)

        finalfix_out_pid = str(team_data.get("finalfix_out_pid") or "")
        finalfix_in_pid  = str(team_data.get("finalfix_in_pid")  or "")

        print(f"    cap={cap_id} mega={megacap_id} no_neg={has_no_neg} "
              f"final_fix={has_final_fix} "
              f"ff_out={finalfix_out_pid or '?'} ff_in={finalfix_in_pid or '?'} "
              f"cards={list(cards.keys())} gdpoints={gdpoints}")

        if not picks:
            print(f"    ERROR: no cookie or cookie expired for {user_name} — skipping.")
            continue

        def score_components(components, mult, race_only=False, pre_race_only=False):
            sub = 0.0
            for cat, val in components:
                if race_only     and not _is_race_cat(cat): continue
                if pre_race_only and     _is_race_cat(cat): continue
                factor = 0 if (has_no_neg and val < 0) else 1
                sub += val * mult * factor
            return sub

        pick_scores = {str(p["id"]): 0.0 for p in picks}
        smart_finfx = has_final_fix and bool(finalfix_out_pid)

        if use_gdpoints:
            total = float(gdpoints) if gdpoints is not None else 0.0
            # Final: gdpoints already include penalties — read them for display only
            sub_penalty, inactive_pen, _ = live_penalties(team_data, picks, set(), [])
            print(f"    → Post-event: using gdpoints={total}")

            if smart_finfx:
                out_mult  = _finfx_mult(picks, finalfix_in_pid, cap_id, megacap_id)
                out_comps = get_player_components(int(finalfix_out_pid), gameday_id, lv)
                pick_scores[finalfix_out_pid] = score_components(out_comps, out_mult, pre_race_only=True)
                print(f"    FF out-driver ({finalfix_out_pid}) pre-race pts: "
                      f"{pick_scores[finalfix_out_pid]:.0f}")
                time.sleep(0.05)

            for p in picks:
                pid       = str(p["id"])
                comps     = get_player_components(p["id"], gameday_id, lv)
                mult      = _cap_mult(p["id"], cap_id) * _mega_mult(p["id"], megacap_id)
                race_only = smart_finfx and pid == finalfix_in_pid
                pick_scores[pid] = score_components(comps, mult, race_only=race_only)
                time.sleep(0.05)

        else:
            total = 0.0

            if smart_finfx:
                out_mult  = _finfx_mult(picks, finalfix_in_pid, cap_id, megacap_id)
                out_comps = get_player_components(int(finalfix_out_pid), gameday_id, lv)
                out_sub   = score_components(out_comps, out_mult, pre_race_only=True)
                pick_scores[finalfix_out_pid] = out_sub
                total += out_sub
                print(f"    FF out-driver ({finalfix_out_pid}) pre-race pts: {out_sub:.0f}")
                time.sleep(0.05)

            for p in picks:
                pid       = str(p["id"])
                comps     = get_player_components(p["id"], gameday_id, lv)
                mult      = _cap_mult(p["id"], cap_id) * _mega_mult(p["id"], megacap_id)
                if mult == 6:
                    print(f"    WARNING: {pid} is both captain AND mega captain — applying 6×")
                race_only = smart_finfx and pid == finalfix_in_pid
                pick_sub  = score_components(comps, mult, race_only=race_only)
                pick_scores[pid] = pick_sub
                total += pick_sub
                time.sleep(0.05)

            sub_penalty, inactive_pen, pen_source = live_penalties(
                team_data, picks, inactive_ids, sessions_started)
            total += sub_penalty + inactive_pen

            print(f"    Penalties: transfers={sub_penalty} inactive={inactive_pen} ({pen_source}) "
                  f"| raw usersubs={team_data.get('usersubs')} allowed={team_data.get('subsallowed')} "
                  f"left={team_data.get('usersubsleft')} "
                  f"inactive_api={team_data.get('inactive_driver_penality_points')} "
                  f"accounting={team_data.get('isaccounting')}")

            if has_final_fix and not smart_finfx and gdpoints is not None:
                print(f"    → Final Fix fallback: component total={total:.0f}, "
                      f"using gdpoints={float(gdpoints):.0f} (out-driver ID unknown)")
                total = float(gdpoints)
            else:
                print(f"    → Calculated: {total:.0f} (gdpoints={gdpoints})")

        pick_details = []

        if smart_finfx and finalfix_out_pid in pick_scores:
            out_id   = int(finalfix_out_pid)
            out_mult = _finfx_mult(picks, finalfix_in_pid, cap_id, megacap_id)
            pick_details.append({
                "id":              out_id,
                "full_name":       player_names.get(out_id, f"#{out_id}"),
                "tla":             player_tlas.get(out_id, f"#{out_id}"),
                "pick_type":       "Constructor" if player_skills.get(out_id, 1) == 2 else "Driver",
                "skill":           player_skills.get(out_id, 1),
                "iscaptain":       1 if out_mult >= 2 and out_mult < 3 else 0,
                "ismgcaptain":     1 if out_mult == 3 or out_mult == 6 else 0,
                "pick_score":      pick_scores[finalfix_out_pid],
                "is_finalfix_out": True,
            })

        for p in picks:
            pid       = p["id"]
            full_name = player_names.get(pid, f"#{pid}")
            tla       = player_tlas.get(pid, full_name)
            skill     = player_skills.get(pid, 1)
            pick_type = "Constructor" if skill == 2 else "Driver"
            pick_details.append({
                "id":          pid,
                "full_name":   full_name,
                "tla":         tla,
                "pick_type":   pick_type,
                "skill":       skill,
                "iscaptain":   p.get("iscaptain", 0),
                "ismgcaptain": p.get("ismgcaptain", 0),
                "pick_score":  pick_scores.get(str(pid), 0.0),
            })

        results.append({
            "team_name":    team_name,
            "user_name":    user_name,
            "display_name": NICKNAMES.get(user_name, user_name),
            "points":       total,
            "transfer_penalty": sub_penalty,
            "inactive_penalty": inactive_pen,
            "cards":        cards,
            "pick_details": pick_details,
            "season_rank":  season_rank,
        })

    empty = [t["user_name"] for t in results if not t["pick_details"]]
    if results and len(empty) == len(results):
        cookie_expired_exit("no team data returned for any player")
    if empty:
        print(f"  WARNING: no team data for: {', '.join(empty)} - publishing the others")

    results.sort(key=lambda x: x["points"], reverse=True)

    for i, t in enumerate(results):
        if i == 0:
            t["rank"] = 1
        elif t["points"] == results[i - 1]["points"]:
            t["rank"] = results[i - 1]["rank"]
        else:
            t["rank"] = i + 1

    return results, lv


# Card codes → names shown on the website (e.g. "Extra DRS (GD13)")
CARD_LABEL_MAP = {
    "NoNeg":     "No Negative",
    "Auto":      "Autopilot",
    "Limitless": "Limitless",
    "WC":        "Wildcard",
    "ExDRS":     "Extra DRS",
    "FinFix":    "Final Fix",
}


def _format_cards_used(cards):
    """Convert cards dict {'NoNeg': 1, 'ExDRS': 2} → 'No Negative (GD1), Extra DRS (GD2)'."""
    if not cards:
        return ""
    return ", ".join(
        f"{CARD_LABEL_MAP.get(k, k)} (GD{v})" for k, v in cards.items()
    )


def _race_meta(race):
    """Common race-level fields shared by every row in picks/breakdowns."""
    return {
        "race_number": race["meeting_number"],
        "race_name":   race["meeting_name"],
        "race_flag":   race["flag"],
        "race_date":   race["start_dt"].strftime("%Y-%m-%d") if race.get("start_dt") else "",
    }



def build_results_rows(race, results, last_state):
    """
    Build results.json rows — one row per team per race.
    season_points comes from last_standings.json scores (saved after every run).
    All financial/prize fields are null — not available from API.
    """
    meta = _race_meta(race)
    # Season points saved in last_state.scores after the previous run.
    # During a live run these reflect the state at last save, which is
    # close enough for a live feed. Post-race they are exact.
    season_scores = (last_state or {}).get("scores", {})
    rows = []
    for team in results:
        cards_str = _format_cards_used(team["cards"])
        rows.append({
            **meta,
            "season_rank":      team["season_rank"],
            "race_rank":        team["rank"],
            "rank_change":      None,
            "team_name":        team["team_name"],
            "user_name":        team["user_name"],
            "season_points":    season_scores.get(team["user_name"], None),
            "race_points":      int(team["points"]),
            "transfer_penalty": team.get("transfer_penalty", 0),
            "inactive_penalty": team.get("inactive_penalty", 0),
            "penalty_points":   team.get("transfer_penalty", 0) + team.get("inactive_penalty", 0),
            "budget_remaining": None,
            "team_value":       None,
            "cards_used":       cards_str,
            "current_budget":   None,
            "new_budget":       None,
            "race_prize":       None,
        })
    return rows

def build_picks_rows(race, results):
    """Build the picks.json rows for this race from the results list."""
    meta = _race_meta(race)
    rows = []
    for team in results:
        cards_str = _format_cards_used(team["cards"])
        for p in team["pick_details"]:
            rows.append({
                **meta,
                "season_rank":      team["season_rank"],
                "race_rank":        team["rank"],
                "team_name":        team["team_name"],
                "user_name":        team["user_name"],
                "cards_used":       cards_str,
                "pick_id":            p["id"],
                "pick_name":          p["full_name"],
                "pick_tla":           p["tla"],
                "pick_type":          p["pick_type"],
                "is_captain":         bool(p["iscaptain"]),
                "is_megacap":         bool(p["ismgcaptain"]),
                "is_finalfix_out":    bool(p.get("is_finalfix_out", False)),
                "pick_points_gd":     round(p.get("pick_score", 0.0), 1),
                "team_points_gd":     int(team["points"]),
                "price_this_race":    None,
                "price_next_race":    None,
            })
    return rows


def build_breakdowns_rows(race, results, lv, gameday_id):
    """
    Build the breakdowns.json rows for this race by re-fetching player components.

    breakdown_points always stores the RAW component value from the API —
    no multipliers or card effects are applied. Context columns are included
    so any downstream tool can reconstruct the effective points:

      effective = 0                          if no_neg_applied and breakdown_points < 0
      effective = breakdown_points * cap_multiplier   otherwise

    Context columns:
      • is_megacap      — this pick is the mega captain (3× role)
      • cap_multiplier  — combined multiplier for this pick: 1 / 2 / 3 / 6
      • no_neg_applied  — True if this pick's team has NoNeg active this gameday
                          (consumer must zero negatives when True)
    """
    meta = _race_meta(race)
    rows = []

    for team in results:
        has_no_neg = "NoNeg" in team["cards"]
        cap_id     = next((p["id"] for p in team["pick_details"] if p.get("iscaptain")),   None)
        megacap_id = next((p["id"] for p in team["pick_details"] if p.get("ismgcaptain")), None)

        for p in team["pick_details"]:
            pid        = p["id"]
            cap_mult   = 2 if (cap_id     and pid == cap_id)     else 1
            mega_mult  = 3 if (megacap_id and pid == megacap_id) else 1
            multiplier = cap_mult * mega_mult   # 1, 2, 3, or 6

            components = get_player_components(pid, gameday_id, lv)
            for category, raw_points in components:
                rows.append({
                    **meta,
                    "season_rank":        team["season_rank"],
                    "race_rank":          team["rank"],
                    "team_name":          team["team_name"],
                    "user_name":          team["user_name"],
                    "pick_id":            pid,
                    "pick_name":          p["full_name"],
                    "pick_type":          p["pick_type"],
                    "is_captain":         bool(p["iscaptain"]),
                    "is_megacap":         bool(p["ismgcaptain"]),
                    "cap_multiplier":     multiplier,
                    "no_neg_applied":     has_no_neg,
                    "breakdown_category": category,
                    "breakdown_points":   raw_points,   # always raw — apply context cols to reconstruct
                    "pick_points_gd":     int(team["points"]),
                })
            time.sleep(0.05)
    return rows


LIVE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "live")


def _read_live(filename):
    path = os.path.join(LIVE_DIR, filename)
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"  WARNING: could not read {path}: {e} - starting fresh")
        return []


def _write_live(filename, rows):
    os.makedirs(LIVE_DIR, exist_ok=True)
    path = os.path.join(LIVE_DIR, filename)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)   # atomic: never leaves a half-written file
    print(f"  wrote live/{filename} ({len(rows)} rows)")


def export_json_data(race, results, lv, gameday_id, last_state=None):
    """
    Build results / picks / breakdowns rows for this race and write them to
    live/*.json (replacing earlier rows of the same race). The workflow then
    commits all live files together in ONE commit.
    """
    print("\n[JSON Export] Building live JSON files...")
    print(f"  teams in results: {len(results)}")
    for t in results:
        print(f"    {t['user_name']} - {len(t.get('pick_details', []))} picks, {int(t['points'])}pts")

    new_picks      = build_picks_rows(race, results)
    new_breakdowns = build_breakdowns_rows(race, results, lv, gameday_id)
    new_results    = build_results_rows(race, results, last_state)

    race_num = race["meeting_number"]
    for filename, new_rows in (("results.json",    new_results),
                               ("picks.json",      new_picks),
                               ("breakdowns.json", new_breakdowns)):
        kept = [r for r in _read_live(filename) if r.get("race_number") != race_num]
        _write_live(filename, kept + new_rows)


def _score_now(race):
    """Same meaning as get_current_race()'s score, recomputed for 'now'."""
    now = datetime.now(timezone.utc)
    if race["window_start"] <= now <= race["window_end"]:
        return 0
    return -(now - race["window_end"]).total_seconds()


def publish_update(race, force_send=False, final=False):
    """Fetch standings once and publish live/*.json if anything changed."""
    last_state = load_last_state()
    try:
        results, lv = get_standings(race["gameday_id"], _score_now(race), race)
    except CookieExpired as e:
        cookie_expired_exit(str(e))
    for t in results:
        print(f"    {t['rank']}. {t['user_name']} — {int(t['points'])}pts")
    if not (force_send or scores_changed(race, results, last_state)):
        print("  No changes — nothing to publish.")
        return False
    save_state(race, results, post_race_sent=final)
    export_json_data(race, results, lv, race["gameday_id"], last_state=last_state)
    return True


def commit_live(message):
    """Session mode: push each checkpoint right away (only on GitHub Actions)."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        print("  (local run — not committing)")
        return
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    def git(*args):
        return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    git("config", "user.name", "F1 Bot")
    git("config", "user.email", "f1bot@github.com")
    git("add", "live", os.path.join("bot", "last_standings.json"))
    if git("diff", "--staged", "--quiet").returncode == 0:
        return
    git("commit", "-m", f"{message} [skip ci]")
    for attempt in range(3):
        if git("pull", "--rebase").returncode == 0 and git("push").returncode == 0:
            print("  pushed")
            return
        time.sleep(5)
    print("  WARNING: push failed — will retry at the next checkpoint")


FANTASY_SESSION_TYPES = {"qualifying": "Qualifying", "sprint": "Sprint", "race": "Race"}
MATCH_STATUS = {"1": "upcoming", "2": "live", "3": "complete", "4": "complete"}


def _schedule_sessions(node):
    """All session entries in F1 Fantasy's schedule feed (nested structure)."""
    if isinstance(node, list):
        for x in node:
            yield from _schedule_sessions(x)
    elif isinstance(node, dict):
        if "GamedayId" in node and "SessionType" in node:
            yield node
        else:
            for v in node.values():
                yield from _schedule_sessions(v)


def fantasy_signals_fn(race):
    """Build the signals function for session mode — F1 Fantasy API only:
      status        schedule MatchStatus (1 upcoming, 2 live, 4 complete)
      q_parts_done  0/1/2 — constructor bonus "...Q2" appears after Q1, "...Q3" after Q2
    """
    gd   = int(race["gameday_id"])
    kind = race["session_type"]
    want = FANTASY_SESSION_TYPES.get(kind, "Race")
    cache = {"lv": "unset", "parts": 0, "cons": None}

    def constructor_ids():
        if cache["cons"] is None:
            data = get(f"{BASE_FEEDS}/drivers/{gd}_en.json?buster={buster()}")["Data"]["Value"]
            cache["cons"] = [str(p["PlayerId"]) for p in data
                             if p.get("Skill") == 2 or p.get("PositionName", "").upper() != "DRIVER"]
        return cache["cons"]

    def signals():
        sig = {"status": None, "q_parts_done": None}
        try:
            sched = get(f"{BASE_FEEDS}/v2/schedule/raceday_en.json?buster={buster()}")
            entry = next((e for e in _schedule_sessions(sched)
                          if int(e.get("GamedayId", 0)) == gd and e.get("SessionType") == want), None)
            if entry:
                sig["status"] = MATCH_STATUS.get(str(entry.get("MatchStatus")))
        except Exception as e:
            print(f"    (schedule feed unavailable: {e})")
        if kind == "qualifying":
            try:
                lv = get(f"{BASE_FEEDS}/live/mixapi.json?buster={buster()}")["Value"].get("lv")
                if lv != cache["lv"]:                     # new F1 Fantasy batch → re-check bonuses
                    cache["lv"] = lv
                    events = set()
                    for pid in constructor_ids():
                        stats = get(f"{BASE_FEEDS}/popup/playerstats_{pid}.json?buster={lv or buster()}")
                        for g in stats["Value"]["GamedayWiseStats"]:
                            if int(g["GamedayId"]) == gd:
                                events |= {st["Event"] for st in g["StatsWise"]}
                        time.sleep(0.05)
                    parts = 2 if any("Q3" in ev for ev in events) else \
                            1 if any("Q2" in ev for ev in events) else 0
                    cache["parts"] = max(cache["parts"], parts)
                sig["q_parts_done"] = cache["parts"]
            except Exception as e:
                print(f"    (qualifying bonus check failed: {e})")
        return sig

    return signals


def run_session_mode(race):
    """Stay alive for the whole session and publish only at checkpoints."""
    import session_mode
    label = f"live: {race['meeting_name']} {race['session_type']}"

    def publish(reasons, final):
        print(f"\n[checkpoint] {', '.join(reasons)}")
        if publish_update(race, force_send=final, final=final):
            commit_live(f"{label} — {reasons[-1]}")

    session_mode.run(race["session_type"], race["start_dt"], publish, fantasy_signals_fn(race))


# ── Main ──────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*50}")
    print(f"F1 Quick Standings | {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*50}")

    # ── 1. Detect race & window ───────────────────────────────────
    print("\n[1/5] Detecting current race...")
    race, score = get_current_race()

    if race:
        print(f"  Race    : {race['meeting_name']} (#{race['meeting_number']})")
        print(f"  Session : {race['session_type']}")
        print(f"  Gameday : {race['gameday_id']} (from schedule)")
        if race.get("start_dt"):
            print(f"  Start   : {race['start_dt'].strftime('%Y-%m-%d %H:%M')} UTC")
            print(f"  Window  : {race['window_start'].strftime('%H:%M')} - "
                  f"{race['window_end'].strftime('%H:%M')} UTC")
        status = "live" if score == 0 else "past" if score < 0 else "future"
        print(f"  Status  : {status} ({score:.0f}s)")

        # ── Validate gameday_id against live API ──────────────────
        print("\n[1b] Validating gameday_id against F1 Fantasy API...")
        orig_gd      = int(race["gameday_id"])
        corrected_gd = validate_gameday_id(race)
        if corrected_gd != orig_gd:
            race = {**race, "gameday_id": corrected_gd}
            print(f"  Gameday : {corrected_gd} (auto-corrected ← was {orig_gd})")
        else:
            print(f"  Gameday : {corrected_gd} ✓")

    force = os.environ.get("FORCE_RUN", "0") == "1"
    if force:
        print("  FORCE_RUN=1 — skipping all window and change checks.")

    ok, mode = should_run(race, score, force=force)
    if not ok and not force and os.environ.get("SESSION_MODE", "1") == "1":
        # Started by hand ahead of time? Take the next session if it's within 2 h.
        soon, _ = get_current_race(upcoming_within_hours=2)
        if soon:
            soon = {**soon, "gameday_id": validate_gameday_id(soon)}
            print(f"\nNext session {soon['meeting_name']} {soon['session_type']} starts "
                  f"{soon['start_dt']:%H:%M} UTC — session mode will wait for it.")
            race, ok, mode = soon, True, "live"
    if not ok:
        print("\nOutside race window. Exiting cleanly.")
        return

    # Session mode (default): one run covers the whole session, publishing at
    # Q1/Q2/Q3 ends or every ~10 laps. SESSION_MODE=0 → old one-shot behaviour.
    if mode == "live" and not force and os.environ.get("SESSION_MODE", "1") == "1":
        last_state = load_last_state()
        same = (last_state and str(last_state.get("gameday_id")) == str(race["gameday_id"])
                and last_state.get("session_type") == race["session_type"])
        if same and last_state.get("post_race_sent"):
            print("\nThis session is already final. Exiting.")
            return
        run_session_mode(race)
        print("\nDone!")
        return

    # ── 2. Load last state ────────────────────────────────────────
    print("\n[2/5] Loading last state...")
    last_state = load_last_state()
    if last_state:
        print(f"  Last run       : {last_state.get('saved_at', 'unknown')}")
        print(f"  Last session   : {last_state.get('session_type')}@gd{last_state.get('gameday_id')}")
        print(f"  Post-race sent : {last_state.get('post_race_sent', False)}")
    else:
        print("  No previous state found.")

    # Post-race: only send once per session
    if mode == "post_race" and not force:
        same_session = (
            last_state and
            str(last_state.get("gameday_id")) == str(race["gameday_id"]) and
            last_state.get("session_type") == race["session_type"]
        )
        if same_session and last_state.get("post_race_sent"):
            print("\n  Post-race message already sent for this session. Exiting.")
            return
        print("  Post-race final send — will send regardless of score changes.")

    # ── 3. Fetch standings ────────────────────────────────────────
    print("\n[3/5] Fetching standings...")
    try:
        results, lv = get_standings(race["gameday_id"], score, race)
    except CookieExpired as e:
        cookie_expired_exit(str(e))
    for t in results:
        cards_str = f" [{', '.join(t['cards'].keys())}]" if t["cards"] else ""
        print(f"  {t['rank']}. {t['user_name']} ({t['team_name']}) — "
              f"{int(t['points'])}pts{cards_str}")

    # ── 4. Check whether to send ──────────────────────────────────
    print("\n[4/5] Checking for changes...")
    should_send = force or mode == "post_race" or scores_changed(race, results, last_state)

    if not should_send:
        print("  No changes — nothing to publish.")
        return

    # Save state — mark post_race_sent if this was the final send
    save_state(race, results, post_race_sent=(mode == "post_race"))

    # ── 7. Export JSON data to data repo (live + final) ─────────
    print("\n[5/5] Exporting JSON data for the website...")
    export_json_data(race, results, lv, race["gameday_id"], last_state=last_state)

    print("\nDone!")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        print(f"\nFATAL ERROR: {e}")
        traceback.print_exc()
        sys.exit(1)
