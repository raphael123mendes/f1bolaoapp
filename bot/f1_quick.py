"""
F1 Fantasy Quick Standings — Automated WhatsApp Bot
=====================================================
- Auto-detects current race from the F1 Fantasy schedule feed
- Fetches per-team data from /services/user/opponentteam/ endpoints:
    opponentgamedayplayerteamget → picks with iscaptain, ismgcaptain, gdpoints
- Calculates accurate live GD points using component-level formula:
    points = sum(component * captain_mult * megacap_mult * no_neg_factor)
    where: captain_mult  = 2 if iscaptain,   else 1
           megacap_mult  = 3 if ismgcaptain, else 1  (stacks: both = 6x)
           no_neg_factor = 0 if No Negative card AND component < 0, else 1
- Sends WhatsApp standings only when scores change, or once 60min after session ends
- After each final (post-race) send, exports three JSON files to a data repo:
    picks.json      → one row per pick per team per race
    breakdowns.json → one row per scoring category per pick per team per race
    races.json      → full season calendar (from races.json config)
- Runs on GitHub Actions — no laptop needed

FILES NEEDED (same folder):
  f1_quick.py           <- this script
  f1_image.py           <- PNG card generator
  config.json           <- your settings
  cookie.txt            <- your F1 Fantasy session cookie
  f1_2026_schedule.json <- race calendar
  races.json            <- season calendar for JSON export
  last_standings.json   <- auto-created, tracks last sent standings

config.json format:
{
  "league_id":   "1692401",
  "league_name": "Bolão F1",
  "whatsapp": [
    {"phone": "+971565256000", "apikey": "6999974"}
  ],
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
WA_RECIPIENTS = CONFIG["whatsapp"]
NICKNAMES     = CONFIG.get("nicknames", {})
# DATA_REPO removed — using GITHUB_TOKEN + GITHUB_REPOSITORY (auto-set by Actions)

RACES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "races.json")

BASE_FEEDS    = "https://fantasy.formula1.com/feeds"
BASE_SERVICES = "https://fantasy.formula1.com/services/user"

# ── Meta WhatsApp API credentials (from GitHub Secrets / env vars) ─
META_PHONE_ID = os.environ.get("WA_PHONE_NUMBER_ID", "")
META_TOKEN    = os.environ.get("WA_ACCESS_TOKEN", "")
META_TO_PHONE = os.environ.get("WA_TO_PHONE", "")
META_API_URL  = "https://graph.facebook.com/v21.0"
IMG_PATH      = os.path.join(os.path.dirname(os.path.abspath(__file__)), "standings_card.png")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept":     "application/json, text/plain, */*",
    "Referer":    "https://fantasy.formula1.com/",
}

COOKIE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookie.txt")

def load_cookie():
    if os.path.exists(COOKIE_FILE):
        cookie = open(COOKIE_FILE, encoding="utf-8").read().strip()
        if cookie:
            print(f"  Cookie loaded ({len(cookie)} chars)")
            return cookie
    print("  WARNING: cookie.txt not found or empty — will use cur_points (delayed during race).")
    return None

COOKIE = load_cookie()
AUTH_HEADERS = {**HEADERS, "Cookie": COOKIE} if COOKIE else None

FLAG_MAP = {
    "Australia":      "🇦🇺", "China":          "🇨🇳", "Japan":          "🇯🇵",
    "Bahrain":        "🇧🇭", "Saudi Arabia":   "🇸🇦", "United States":  "🇺🇸",
    "Italy":          "🇮🇹", "Monaco":         "🇲🇨", "Canada":         "🇨🇦",
    "Spain":          "🇪🇸", "Austria":        "🇦🇹", "United Kingdom": "🇬🇧",
    "Hungary":        "🇭🇺", "Belgium":        "🇧🇪", "Netherlands":    "🇳🇱",
    "Azerbaijan":     "🇦🇿", "Singapore":      "🇸🇬", "Mexico":         "🇲🇽",
    "Brazil":         "🇧🇷", "UAE":            "🇦🇪", "Abu Dhabi":      "🇦🇪",
    "Qatar":          "🇶🇦", "Las Vegas":      "🇺🇸", "Miami":          "🇺🇸",
}

CARD_FIELDS = {
    "isnonigativetaken": "NoNeg",
    "isautopilottaken":  "Auto",
    "islimitlesstaken":  "Limitless",
    "iswildcardtaken":   "WC",
    "isextradrstaken":   "ExDRS",
    "isfinalfixtaken":   "FinFix",
}


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

def get_current_race():
    """
    Reads f1_2026_schedule.json and returns the most relevant
    is_competitive session (qualifying, sprint, race).
    Window: 30 min before session start → 2 hours after session start.
    Cancelled rounds are skipped automatically.
    """
    schedule_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "f1_2026_schedule.json")
    if not os.path.exists(schedule_file):
        print("  ERROR: f1_2026_schedule.json not found!")
        return None, None

    with open(schedule_file, encoding="utf-8") as f:
        schedule = json.load(f)

    now          = datetime.now(timezone.utc)
    SCORED_TYPES = ("qualifying", "sprint", "race")
    sessions     = []

    for r in schedule["rounds"]:
        if r.get("cancelled"):
            continue
        country = r.get("location", "")
        flag    = next((v for k, v in FLAG_MAP.items() if k.lower() in country.lower()), "🏁")
        for s in r["sessions"]:
            if s["session_type"] not in SCORED_TYPES:
                continue
            start_dt = datetime.fromisoformat(s["utc_start"].replace("Z", "+00:00"))
            sessions.append({
                "meeting_number": r["round"],
                "meeting_name":   r["name"],
                "session_type":   s["session_type"],
                "country":        country,
                "gameday_id":     r["round"],
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

    print("  Standings unchanged — skipping WhatsApp.")
    return False


# ── Step 4: Fetch team picks + cards ─────────────────────────────

def get_team_details(gameday_id, user_guid, team_no=1):
    if not AUTH_HEADERS:
        return None, [], {}, {}

    url = (f"{BASE_SERVICES}/opponentteam/opponentgamedayplayerteamget"
           f"/{gameday_id}/{user_guid}/{team_no}/{gameday_id}/{gameday_id}"
           f"?buster={buster()}")
    try:
        r = requests.get(url, headers=AUTH_HEADERS, timeout=15)
        r.raise_for_status()
        data      = r.json()["Data"]["Value"]
        user_team = data.get("userTeam", [])
        if not user_team:
            return None, [], {}, {}

        team_data  = user_team[0]
        picks      = team_data.get("playerid") or []
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

        team_info = {
            "usersubs":                        team_data.get("usersubs", 0),
            "subsallowed":                     team_data.get("subsallowed", 2),
            "extrasubscost":                   team_data.get("extrasubscost", 10),
            "inactive_driver_penality_points": team_data.get("inactive_driver_penality_points", 0),
        }
        return gdpoints, picks, cards, team_info

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

def get_standings(gameday_id, score=0):
    # Use finalised gdpoints when: forced run, or session ended >2h ago (7200s)
    force_flag = os.environ.get("FORCE_RUN", "0") == "1"
    use_gdpoints = force_flag or (score is not None and score < 0 and abs(score) >= 7200)
    if not AUTH_HEADERS:
        print("  FATAL: cookie.txt is missing or empty.")
        sys.exit(1)

    lv = get(f"{BASE_FEEDS}/live/mixapi.json?buster={buster()}")["Value"]["lv"]

    player_names     = {}   # pid -> full display name  (for JSON export + WhatsApp fallback)
    player_tlas      = {}   # pid -> TLA                 (for WhatsApp message display)
    player_skills    = {}   # pid -> 1 (Driver) | 2 (Constructor)
    try:
        drivers_data = get(f"{BASE_FEEDS}/drivers/1_en.json?buster={buster()}")
        for p in drivers_data["Data"]["Value"]:
            pid  = str(p.get("PlayerId", ""))
            tla  = p.get("DriverTLA") or p.get("DisplayName") or pid
            # DisplayName can come back abbreviated e.g. "C. Leclerc"
            # FirstName + LastName gives the full name when available
            first = p.get("FirstName", "")
            last  = p.get("LastName", "")
            if first and last:
                full = f"{first} {last}"
            else:
                full = p.get("DisplayName") or tla
            player_names[pid]  = full
            player_tlas[pid]   = tla
            player_skills[pid] = p.get("Skill", 1)
        print(f"  Player names loaded: {len(player_names)}")
    except Exception as e:
        print(f"  WARNING: could not load player names: {e}")

    leaderboard = get(
        f"{BASE_FEEDS}/leaderboard/privateleague/list_2_{LEAGUE_ID}_1_1.json"
        f"?buster={buster()}"
    )["Value"]["leaderboard"]

    if not leaderboard:
        print("  ERROR: leaderboard returned None or empty — check LEAGUE_ID and cookie.")
        return [], lv

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
        cap_id     = next((p["id"] for p in picks if p.get("iscaptain")),   None)
        megacap_id = next((p["id"] for p in picks if p.get("ismgcaptain")), None)

        print(f"    cap={cap_id} mega={megacap_id} no_neg={has_no_neg} "
              f"cards={list(cards.keys())} gdpoints={gdpoints}")

        if not picks:
            print(f"    ERROR: no cookie or cookie expired for {user_name} — skipping.")
            continue

        if use_gdpoints:
            total = float(gdpoints) if gdpoints is not None else 0.0
            print(f"    → Post-event: using gdpoints={total}")
        else:
            total = 0.0
            for p in picks:
                pid       = p["id"]
                cap_mult  = 2 if (cap_id     and pid == cap_id)     else 1
                mega_mult = 3 if (megacap_id and pid == megacap_id) else 1
                mult      = cap_mult * mega_mult
                # reuse components already fetched for pick_components above
                comps = get_player_components(pid, gameday_id, lv)
                for _cat, val in comps:
                    no_neg_factor = 0 if (has_no_neg and val < 0) else 1
                    total += val * mult * no_neg_factor
                time.sleep(0.05)

            usersubs    = team_data.get("usersubs", 0) or 0
            subsallowed = team_data.get("subsallowed", 2) or 2
            extra_subs  = max(0, usersubs - subsallowed)
            sub_penalty = extra_subs * -(team_data.get("extrasubscost", 10) or 10)
            inactive_pen = team_data.get("inactive_driver_penality_points", 0) or 0
            total += sub_penalty + inactive_pen

            if sub_penalty or inactive_pen:
                print(f"    Penalties: transfers={sub_penalty} inactive={inactive_pen}")
            print(f"    → Calculated: {total} (gdpoints={gdpoints})")

        # Compute per-pick points from components for every pick
        # This gives individual scores regardless of live vs post-race mode
        pick_components = {}
        for p in picks:
            pid       = p["id"]
            cap_mult  = 2 if (cap_id     and pid == cap_id)     else 1
            mega_mult = 3 if (megacap_id and pid == megacap_id) else 1
            mult      = cap_mult * mega_mult
            comps     = get_player_components(pid, gameday_id, lv)
            pts       = sum(
                val * mult * (0 if (has_no_neg and val < 0) else 1)
                for _cat, val in comps
            )
            pick_components[pid] = int(pts)
            time.sleep(0.05)

        pick_details = []
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
                "pick_pts":    pick_components.get(pid, 0),
            })

        results.append({
            "team_name":    team_name,
            "user_name":    user_name,
            "display_name": NICKNAMES.get(user_name, user_name),
            "points":       total,
            "cards":        cards,
            "pick_details": pick_details,
            "season_rank":  season_rank,
        })

    results.sort(key=lambda x: x["points"], reverse=True)

    for i, t in enumerate(results):
        if i == 0:
            t["rank"] = 1
        elif t["points"] == results[i - 1]["points"]:
            t["rank"] = results[i - 1]["rank"]
        else:
            t["rank"] = i + 1

    return results, lv


# ── Step 6: Build & send WhatsApp message ────────────────────────

def build_message(race, results, mode="live"):
    now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
    session_label = {"race": "Race", "qualifying": "Qualifying",
                     "sprint": "Sprint"}.get(race["session_type"], race["session_type"].title())
    status_tag = "🏁 Final" if mode == "post_race" else "⏱ Live"

    lines = [
        f"{race['flag']} {race['meeting_name']} — R{race['meeting_number']} {session_label}",
        f"{status_tag} | {now_str} | {LEAGUE_NAME}",
        "",
    ]

    for t in results:
        card_str = ""
        if t["cards"]:
            card_str = " | " + ", ".join(t["cards"].keys())
        rank_str = f"({t['rank']:02d})"

        picks_sorted = sorted(
            t.get("pick_details", []),
            key=lambda p: (0 if p["ismgcaptain"] else 1 if p["iscaptain"] else 2 if p["skill"] == 1 else 3)
        )
        picks_str = " ".join(
            (p["tla"] + "*MC" if p["ismgcaptain"] else
             p["tla"] + "*C"  if p["iscaptain"]   else
             p["tla"])
            for p in picks_sorted
        )

        display_name = t.get("display_name", t["user_name"])
        lines.append(
            f"{rank_str} {display_name} {int(t['points'])}pts"
            + (f" | {picks_str}" if picks_str else "")
            + card_str
        )

    footer = "🏁 Final standings." if mode == "post_race" else "Updates when scores change."
    lines += ["", footer]
    body = "\n".join(lines)
    return f"```\n{body}\n```"


def send_whatsapp(message):
    for recipient in WA_RECIPIENTS:
        phone  = recipient["phone"]
        apikey = recipient["apikey"]
        try:
            r = requests.get(
                "https://api.callmebot.com/whatsapp.php",
                params={"phone": phone, "text": message, "apikey": apikey},
                timeout=15
            )
            if r.status_code == 200:
                print(f"  WhatsApp sent to {phone}")
            else:
                print(f"  WhatsApp failed for {phone}: {r.text[:100]}")
            time.sleep(2)
        except Exception as e:
            print(f"  WhatsApp error for {phone}: {e}")


# ── Step 7: Generate PNG card ─────────────────────────────────────

def generate_image(race, results, mode="live"):
    """Generate standings PNG via f1_image.py. Returns path or None."""
    try:
        from f1_image import generate_standings_image, apply_nicknames
        results_with_names = apply_nicknames(results, NICKNAMES)
        is_live = (mode == "live")
        # Pass IMG_PATH explicitly so PNG always lands next to this script
        # regardless of the working directory the process was started from
        img_path = generate_standings_image(
            race, results_with_names, LEAGUE_NAME,
            is_live=is_live, output_path=IMG_PATH
        )
        print(f"  PNG generated: {img_path}")
        return img_path
    except Exception as e:
        print(f"  WARNING: could not generate PNG: {e}")
        return None


# ── Step 8: Send PNG via Meta WhatsApp API ────────────────────────

def upload_image_meta(img_path):
    """Upload PNG to Meta media endpoint. Returns media_id or None."""
    if not all([META_PHONE_ID, META_TOKEN, META_TO_PHONE]):
        print("  WARNING: Meta credentials missing — skipping image send.")
        return None
    if not os.path.exists(img_path):
        print(f"  WARNING: PNG not found at {img_path} — skipping image send.")
        return None
    print(f"  Uploading image ({os.path.getsize(img_path)} bytes)...")
    url = f"{META_API_URL}/{META_PHONE_ID}/media"
    try:
        with open(img_path, "rb") as f:
            r = requests.post(
                url,
                headers={"Authorization": f"Bearer {META_TOKEN}"},
                files={"file": ("standings_card.png", f, "image/png")},
                data={"messaging_product": "whatsapp"},
                timeout=30,
            )
        if r.status_code == 200:
            media_id = r.json().get("id")
            print(f"  Upload OK — media_id: {media_id}")
            return media_id
        print(f"  Upload failed ({r.status_code}): {r.text[:200]}")
        return None
    except Exception as e:
        print(f"  Upload error: {e}")
        return None


def send_image_meta(media_id, caption=""):
    """Send image message via Meta API using media_id."""
    url = f"{META_API_URL}/{META_PHONE_ID}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to":   META_TO_PHONE,
        "type": "image",
        "image": {"id": media_id, "caption": caption},
    }
    print(f"  Sending to: {META_TO_PHONE} via phone_id: {META_PHONE_ID}")
    try:
        r = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {META_TOKEN}",
                "Content-Type":  "application/json",
            },
            json=payload,
            timeout=15,
        )
        print(f"  Meta response ({r.status_code}): {r.text[:300]}")
        if r.status_code == 200:
            msg_id = r.json().get("messages", [{}])[0].get("id", "?")
            print(f"  Image sent via Meta — message_id: {msg_id}")
            return True
        print(f"  Image send failed ({r.status_code}): {r.text[:300]}")
        return False
    except Exception as e:
        print(f"  Image send error: {e}")
        return False


def send_whatsapp_image(img_path, caption="F1 Fantasy Standings"):
    """Full flow: upload PNG to Meta then send as WhatsApp image."""
    print("\n[Meta Image]")
    media_id = upload_image_meta(img_path)
    if media_id:
        send_image_meta(media_id, caption=caption)



# ── JSON export helpers ───────────────────────────────────────────

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
                "pick_id":          p["id"],
                "pick_name":        p["full_name"],
                "pick_type":        p["pick_type"],
                "is_captain":       bool(p["iscaptain"]),
                "is_megacap":       bool(p["ismgcaptain"]),
                "pick_points_gd":   p["pick_pts"],
                "price_this_race":  None,
                "price_next_race":  None,
            })
    return rows


def build_breakdowns_rows(race, results, lv, gameday_id):
    """Build the breakdowns.json rows for this race by re-fetching player components."""
    meta = _race_meta(race)
    rows = []
    for team in results:
        for p in team["pick_details"]:
            components = get_player_components(p["id"], gameday_id, lv)
            for category, points in components:
                rows.append({
                    **meta,
                    "season_rank":        team["season_rank"],
                    "race_rank":          team["rank"],
                    "team_name":          team["team_name"],
                    "user_name":          team["user_name"],
                    "pick_id":            p["id"],
                    "pick_name":          p["full_name"],
                    "pick_type":          p["pick_type"],
                    "is_captain":         bool(p["iscaptain"]),
                    "pick_points_gd":     p["pick_pts"],
                    "breakdown_category": category,
                    "breakdown_points":   points,
                })
            time.sleep(0.05)
    return rows


def load_races_json(token="", owner="", repo="", branch="main"):
    """
    Load races.json — tries local disk first, then the data repo, then races.json
    uploaded alongside the script (RACES_FILE). Returns [] if all fail.
    """
    # 1. Try local file next to script (fastest, works in dev)
    if os.path.exists(RACES_FILE):
        with open(RACES_FILE, encoding="utf-8") as f:
            return json.load(f)
    # 2. Try fetching from data repo if credentials available
    if all([token, owner, repo]):
        data = _fetch_json_from_repo(owner, repo, branch, "races.json", token)
        if data:
            return data
    print(f"  WARNING: races.json not found locally or in repo — races will be empty")
    return []


def push_json_to_repo(filename, data):
    """
    Upsert live/{filename} in the current repo via the GitHub Contents API.
    Uses GITHUB_TOKEN (auto-provided by Actions) — no PAT or secret needed.
    GITHUB_REPOSITORY and GITHUB_REF_NAME are set automatically by Actions.
    """
    token   = os.environ.get("GITHUB_TOKEN", "")
    gh_repo = os.environ.get("GITHUB_REPOSITORY", "")
    branch  = os.environ.get("GITHUB_REF_NAME", "main")

    if not all([token, gh_repo]):
        print(f"  WARNING: GITHUB_TOKEN/GITHUB_REPOSITORY missing — skipping {filename}")
        return False

    owner, repo = gh_repo.split("/", 1)
    api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/live/{filename}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept":        "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    # Check if file exists to get its SHA (required for updates)
    sha = None
    try:
        r = requests.get(api_url, headers=headers, params={"ref": branch}, timeout=15)
        if r.status_code == 200:
            sha = r.json().get("sha")
    except Exception as e:
        print(f"  WARNING: could not check existing file {filename}: {e}")

    import base64
    content_bytes = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    content_b64   = base64.b64encode(content_bytes).decode("ascii")

    payload = {
        "message": f"data update: live/{filename} [skip ci]",
        "content": content_b64,
        "branch":  branch,
    }
    if sha:
        payload["sha"] = sha

    try:
        r = requests.put(api_url, headers=headers, json=payload, timeout=30)
        if r.status_code in (200, 201):
            print(f"  Pushed live/{filename} to {owner}/{repo} ({r.status_code})")
            return True
        print(f"  ERROR pushing {filename}: {r.status_code} {r.text[:200]}")
        return False
    except Exception as e:
        print(f"  ERROR pushing {filename}: {e}")
        return False


def export_json_data(race, results, lv, gameday_id, last_state=None):
    """
    Build picks, breakdowns and races rows for this race, then
    load the existing JSON files from the data repo (if reachable),
    append the new rows, and push back.
    Runs every time the script sends — live and post-race alike.
    """
    print("\n[JSON Export] Building picks and breakdowns...")

    token   = os.environ.get("GITHUB_TOKEN", "")
    gh_repo = os.environ.get("GITHUB_REPOSITORY", "")
    branch  = os.environ.get("GITHUB_REF_NAME", "main")
    owner, repo = gh_repo.split("/", 1) if gh_repo else ("", "")

    # Diagnostics — helps catch empty results or missing cookie early
    print(f"  teams in results: {len(results)}")
    for t in results:
        print(f"    {t['user_name']} — {len(t.get('pick_details', []))} picks, {int(t['points'])}pts")

    new_picks      = build_picks_rows(race, results)
    new_breakdowns = build_breakdowns_rows(race, results, lv, gameday_id)
    new_results    = build_results_rows(race, results, last_state)
    races_data     = load_races_json(token, owner, repo, branch)

    print(f"  picks rows      : {len(new_picks)}")
    print(f"  breakdown rows  : {len(new_breakdowns)}")
    print(f"  results rows    : {len(new_results)}")
    print(f"  races rows      : {len(races_data)}")

    if not all([token, gh_repo]):
        print("  WARNING: GITHUB_TOKEN not available — saving JSON locally only.")
        _save_local("picks.json",      new_picks)
        _save_local("breakdowns.json", new_breakdowns)
        _save_local("results.json",    new_results)
        _save_local("races.json",      races_data)
        return

    # Always overwrite with current race data only — app expects latest race, not history
    push_json_to_repo("picks.json",      new_picks)
    push_json_to_repo("breakdowns.json", new_breakdowns)
    push_json_to_repo("results.json",    new_results)
    push_json_to_repo("races.json",      races_data)


def _fetch_json_from_repo(owner, repo, branch, filename, token):
    """Fetch and decode a JSON file from the data repo. Returns parsed list or None."""
    import base64
    api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/live/{filename}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept":        "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        r = requests.get(api_url, headers=headers, params={"ref": branch}, timeout=15)
        if r.status_code == 200:
            content = base64.b64decode(r.json()["content"]).decode("utf-8")
            return json.loads(content)
        if r.status_code == 404:
            return []   # file doesn't exist yet — that's fine
        print(f"  WARNING: could not fetch {filename}: {r.status_code}")
        return None
    except Exception as e:
        print(f"  WARNING: error fetching {filename}: {e}")
        return None


def _save_local(filename, data):
    """Fallback: write JSON file next to the script."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  Saved locally: {path}")


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
        print(f"  Gameday : {race['gameday_id']}")
        if race.get("start_dt"):
            print(f"  Start   : {race['start_dt'].strftime('%Y-%m-%d %H:%M')} UTC")
            print(f"  Window  : {race['window_start'].strftime('%H:%M')} - "
                  f"{race['window_end'].strftime('%H:%M')} UTC")
        status = "live" if score == 0 else "past" if score < 0 else "future"
        print(f"  Status  : {status} ({score:.0f}s)")

    force = os.environ.get("FORCE_RUN", "0") == "1"
    if force:
        print("  FORCE_RUN=1 — skipping all window and change checks.")

    ok, mode = should_run(race, score, force=force)
    if not ok:
        print("\nOutside race window. Exiting cleanly.")
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
    results, lv = get_standings(race["gameday_id"], score if score is not None else 0)
    for t in results:
        cards_str = f" [{', '.join(t['cards'].keys())}]" if t["cards"] else ""
        print(f"  {t['rank']}. {t['user_name']} ({t['team_name']}) — "
              f"{int(t['points'])}pts{cards_str}")

    # ── 4. Check whether to send ──────────────────────────────────
    print("\n[4/5] Checking for changes...")
    should_send = force or mode == "post_race" or scores_changed(race, results, last_state)

    if not should_send:
        print("  No changes — skipping WhatsApp and PNG generation.")
        return

    # ── 5. Generate PNG ──────────────────────────────────────────
    print("\n[5/6] Generating standings image...")
    img_path = generate_image(race, results, mode=mode)

    # ── 6. Send WhatsApp text + image ────────────────────────────
    print("\n[6/6] Sending WhatsApp...")
    message = build_message(race, results, mode=mode)
    print("\nMESSAGE PREVIEW:")
    print("-" * 40)
    print(message)
    print("-" * 40)
    send_whatsapp(message)

    if img_path:
        caption = f"F1 Fantasy Standings — {race['meeting_name']} {race['session_type'].title()}"
        send_whatsapp_image(img_path, caption=caption)
    else:
        print("  No PNG — skipping image send.")

    # Save state — mark post_race_sent if this was the final send
    save_state(race, results, post_race_sent=(mode == "post_race"))

    # ── 7. Export JSON data to data repo (live + final) ─────────
    print("\n[7/7] Exporting JSON data to data repo...")
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
