"""
f1_image_card.py — F1 Fantasy Standings Image Generator
=========================================================
Generates a WhatsApp-ready standings card after each race.

Flow:
  1. Reads config.json  → save_folder, league_name
  2. Reads player_names.txt → 3-letter display codes
  3. Calls f1_calendar.py → auto-detects current race & next race
  4. Finds race_{NN}_*.json in save_folder matching current matchday
  5. Loads prices_master.json → budget values + price changes
  6. Renders HTML → PNG via Playwright (headless Chromium)
  7. Saves → save_folder/f1_standings_latest.png  (Drive auto-sync)
           → SCRIPT_DIR/standings_preview.png      (local preview)

ONE-TIME SETUP:
  pip install playwright
  python -m playwright install chromium

USAGE:
  python f1_image_card.py            # generate PNG
  python f1_image_card.py --html     # HTML preview (no Playwright needed)
  python f1_image_card.py --live     # show LIVE dot in header

Or import from pipeline:
  from f1_image_card import run
  run()

TIE RULES (podium):
  - Always show the top 3 players by list position (slots 0, 1, 2)
  - Each card is colored by the player's actual rank number
    (two players at rank 1 → both gold; next player at rank 3 → bronze)
  - "Tied" badge appears on every podium card sharing its rank
  - A tie note below the podium names the skipped rank
"""

import os
import json
import glob
import html as html_lib
import tempfile
from datetime import datetime, timezone

from f1_config import cfg, SCRIPT_DIR
from f1_calendar import fetch_calendar, detect_current_race, detect_last_completed_race, print_calendar


# ══════════════════════════════════════════════════════════════════
#  CONSTANTS
# ══════════════════════════════════════════════════════════════════

CARD_WIDTH = 520  # px — viewport width for Playwright

CARD_ALIAS = {
    "No Negative": "NoNeg",    "no negative": "NoNeg",    "noneg":    "NoNeg",
    "Autopilot":   "Auto",     "autopilot":   "Auto",
    "Limitless":   "Limitless","limitless":   "Limitless",
    "Wildcard":    "WC",       "wildcard":    "WC",
    "Extra DRS":   "ExDRS",    "extra drs":   "ExDRS",    "extradrs": "ExDRS",
    "Final Fix":   "FinFix",   "final fix":   "FinFix",   "finalfix": "FinFix",
}

CARD_LABEL = {
    "NoNeg": "No Neg", "Auto": "Autopilot", "Limitless": "Limitless",
    "WC": "Wildcard",  "ExDRS": "Extra DRS", "FinFix": "Final Fix",
}

CARD_CSS = {
    "NoNeg": "chip-noneg", "Auto": "chip-auto", "Limitless": "chip-limit",
    "WC": "chip-wc",       "ExDRS": "chip-exdrs", "FinFix": "chip-finfx",
}

FLAG_MAP = {
    "Australia": "🇦🇺", "China": "🇨🇳", "Japan": "🇯🇵",
    "Bahrain": "🇧🇭", "Saudi Arabia": "🇸🇦", "United States": "🇺🇸",
    "Italy": "🇮🇹", "Monaco": "🇲🇨", "Canada": "🇨🇦",
    "Spain": "🇪🇸", "Austria": "🇦🇹", "United Kingdom": "🇬🇧",
    "Hungary": "🇭🇺", "Belgium": "🇧🇪", "Netherlands": "🇳🇱",
    "Azerbaijan": "🇦🇿", "Singapore": "🇸🇬", "Mexico": "🇲🇽",
    "Brazil": "🇧🇷", "UAE": "🇦🇪", "Abu Dhabi": "🇦🇪",
    "Qatar": "🇶🇦", "Las Vegas": "🇺🇸", "Miami": "🇺🇸",
}

PODIUM_MEDAL = {1: "🥇", 2: "🥈", 3: "🥉"}
PODIUM_CSS   = {1: "p1", 2: "p2", 3: "p3"}
PODIUM_LABEL = {1: "1st", 2: "2nd", 3: "3rd"}


# ══════════════════════════════════════════════════════════════════
#  CSS
# ══════════════════════════════════════════════════════════════════

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;700;800;900&family=Barlow:wght@400;500&display=swap');

* { margin:0; padding:0; box-sizing:border-box; }

body { background:#0a0a0a; width:520px; font-family:'Barlow',sans-serif; }

.f1card {
  width:520px; background:#0f0f0f; border-radius:12px;
  overflow:hidden; font-family:'Barlow',sans-serif;
}

/* ── HEADER ── */
.header {
  background:linear-gradient(135deg,#c00 0%,#880000 45%,#1a0000 100%);
  padding:18px 22px 16px; position:relative; overflow:hidden;
}
.header::before {
  content:'F1'; position:absolute; right:-8px; top:-18px;
  font-family:'Barlow Condensed',sans-serif;
  font-size:130px; font-weight:900; color:rgba(255,255,255,0.04);
  line-height:1; letter-spacing:-8px;
}
.header::after {
  content:''; position:absolute; bottom:0; left:0; right:0; height:3px;
  background:linear-gradient(90deg,#c00,#ff3333,#c00);
}
.header-top { display:flex; align-items:center; gap:10px; margin-bottom:6px; }
.header-flag { font-size:24px; line-height:1; }
.race-name {
  font-family:'Barlow Condensed',sans-serif;
  font-size:20px; font-weight:800; color:#fff;
  text-transform:uppercase; letter-spacing:1px;
}
.header-sub {
  font-family:'Barlow Condensed',sans-serif; font-size:12px; font-weight:500;
  color:rgba(255,255,255,0.5); text-transform:uppercase; letter-spacing:2px;
  display:flex; align-items:center; gap:10px; margin-bottom:4px;
}
.league-badge {
  background:rgba(255,255,255,0.1); border:1px solid rgba(255,255,255,0.15);
  border-radius:3px; padding:1px 7px; font-size:10px; font-weight:700;
  color:rgba(255,255,255,0.65); letter-spacing:1.5px;
}
.final-label {
  font-family:'Barlow Condensed',sans-serif; font-size:11px;
  color:rgba(255,255,255,0.4); text-transform:uppercase; letter-spacing:1.5px;
}
.live-dot {
  display:inline-flex; align-items:center; gap:5px; color:#ff6b6b;
}
.dot { width:7px; height:7px; background:#ff4444; border-radius:50%; }

/* ── PODIUM ── */
.podium {
  display:grid; grid-template-columns:1fr 1fr 1fr; gap:2px;
  background:#0a0a0a; padding:14px 12px 10px;
}
.podium-item {
  display:flex; flex-direction:column; align-items:center;
  padding:12px 8px 10px; border-radius:8px; position:relative;
}
.p1 { background:linear-gradient(160deg,rgba(255,215,0,0.12),rgba(255,170,0,0.06)); border:1px solid rgba(255,215,0,0.2); }
.p2 { background:linear-gradient(160deg,rgba(192,192,192,0.10),rgba(140,140,140,0.04)); border:1px solid rgba(192,192,192,0.15); }
.p3 { background:linear-gradient(160deg,rgba(205,127,50,0.10),rgba(150,90,30,0.04)); border:1px solid rgba(205,127,50,0.15); }

.tie-banner {
  position:absolute; top:5px; right:5px;
  font-family:'Barlow Condensed',sans-serif; font-size:8px; font-weight:800;
  letter-spacing:1px; text-transform:uppercase;
  background:rgba(255,215,0,0.15); color:#ffd700;
  border:1px solid rgba(255,215,0,0.3); border-radius:3px; padding:1px 4px;
}
.podium-rank {
  font-family:'Barlow Condensed',sans-serif; font-size:10px; font-weight:700;
  text-transform:uppercase; letter-spacing:2px; margin-bottom:6px;
}
.p1 .podium-rank { color:#ffd700; }
.p2 .podium-rank { color:#c0c0c0; }
.p3 .podium-rank { color:#cd7f32; }
.podium-name {
  font-family:'Barlow Condensed',sans-serif; font-size:22px; font-weight:900;
  color:#fff; text-transform:uppercase; letter-spacing:1px; line-height:1; margin-bottom:2px;
}
.podium-pts {
  font-family:'Barlow Condensed',sans-serif; font-size:36px; font-weight:900;
  line-height:1; letter-spacing:-1px; margin-bottom:1px;
}
.p1 .podium-pts { color:#ffd700; }
.p2 .podium-pts { color:#d0d0d0; }
.p3 .podium-pts { color:#cd7f32; }
.podium-pts-label {
  font-size:9px; font-weight:500; color:rgba(255,255,255,0.35);
  text-transform:uppercase; letter-spacing:1px; margin-bottom:8px;
}
.podium-meta { display:flex; flex-direction:column; align-items:center; gap:3px; width:100%; }
.podium-total  { font-size:11px; color:rgba(255,255,255,0.4); font-family:'Barlow Condensed',sans-serif; }
.podium-budget { font-family:'Barlow Condensed',sans-serif; font-size:12px; font-weight:700; color:rgba(255,255,255,0.7); }
.podium-chg    { font-family:'Barlow Condensed',sans-serif; font-size:11px; font-weight:600; padding:1px 6px; border-radius:3px; }

/* ── SHARED COLOUR CLASSES ── */
.chg-pos { color:#4ade80; background:rgba(74,222,128,0.1); }
.chg-neg { color:#f87171; background:rgba(248,113,113,0.1); }
.chg-neu { color:#888;    background:rgba(136,136,136,0.08); }

/* ── CARD CHIPS ── */
.card-chip {
  font-family:'Barlow',sans-serif; font-size:8px; font-weight:600;
  text-transform:uppercase; letter-spacing:0.5px;
  padding:2px 5px; border-radius:3px; margin-top:4px;
}
.chip-noneg { color:#4ecdc4; background:rgba(78,205,196,0.12);  border:1px solid rgba(78,205,196,0.2); }
.chip-auto  { color:#60a5fa; background:rgba(96,165,250,0.12);  border:1px solid rgba(96,165,250,0.2); }
.chip-limit { color:#a78bfa; background:rgba(167,139,250,0.12); border:1px solid rgba(167,139,250,0.2); }
.chip-wc    { color:#fbbf24; background:rgba(251,191,36,0.12);  border:1px solid rgba(251,191,36,0.2); }
.chip-exdrs { color:#34d399; background:rgba(52,211,153,0.12);  border:1px solid rgba(52,211,153,0.2); }
.chip-finfx { color:#f87171; background:rgba(248,113,113,0.12); border:1px solid rgba(248,113,113,0.2); }

/* ── TIE NOTE ── */
.tie-note {
  text-align:center; font-family:'Barlow Condensed',sans-serif;
  font-size:10px; color:rgba(255,215,0,0.45); letter-spacing:1px;
  text-transform:uppercase; padding:0 12px 8px; background:#0a0a0a;
}

/* ── LIST COLUMN HEADER ── */
.col-header {
  display:grid; grid-template-columns:30px 1fr 50px 72px 54px;
  padding:6px 14px 5px 10px; gap:6px;
  border-bottom:1px solid rgba(255,255,255,0.06);
  background:rgba(255,255,255,0.02);
}
.col-lbl {
  font-family:'Barlow Condensed',sans-serif; font-size:9px; font-weight:700;
  color:rgba(255,255,255,0.22); text-transform:uppercase; letter-spacing:1px;
}
.col-lbl.r { text-align:right; }

/* ── LIST ROWS ── */
.list { padding:0 0 4px; }
.divider { height:1px; background:rgba(255,255,255,0.04); margin:0 12px; }
.row {
  display:grid; grid-template-columns:30px 1fr 50px 72px 54px;
  align-items:center; padding:9px 14px 9px 10px; gap:6px;
}
.row:hover { background:rgba(255,255,255,0.02); }

.row-rank {
  font-family:'Barlow Condensed',sans-serif; font-size:13px; font-weight:700;
  color:rgba(255,255,255,0.3); text-align:center;
}
.row-name {
  font-family:'Barlow Condensed',sans-serif; font-size:16px; font-weight:700;
  color:#e8e8e8; text-transform:uppercase; letter-spacing:0.5px;
  display:flex; flex-direction:column; gap:2px;
}
.row-pts-wrap { text-align:right; }
.row-pts {
  font-family:'Barlow Condensed',sans-serif; font-size:20px; font-weight:900;
  color:#fff; letter-spacing:-0.5px; display:block;
}
.row-pts-label {
  font-size:8px; color:rgba(255,255,255,0.25); text-transform:uppercase;
  letter-spacing:1px; display:block; margin-top:-2px;
}
.row-budget { text-align:right; font-family:'Barlow Condensed',sans-serif; }
.row-budget-val { font-size:12px; font-weight:700; color:rgba(255,255,255,0.55); display:block; }
.row-budget-chg { font-size:11px; font-weight:600; display:block; }
.row-total { text-align:right; font-family:'Barlow Condensed',sans-serif; }
.row-total-val   { font-size:13px; font-weight:600; color:rgba(255,255,255,0.35); display:block; }
.row-total-label { font-size:8px;  color:rgba(255,255,255,0.2); text-transform:uppercase; letter-spacing:1px; display:block; }

/* ── PRICE CHANGES SECTION ── */
.price-section {
  background:#0a0a0a; border-top:1px solid rgba(255,255,255,0.06);
  padding:12px 14px 12px;
}
.price-section-title {
  font-family:'Barlow Condensed',sans-serif; font-size:10px; font-weight:700;
  color:rgba(255,255,255,0.25); text-transform:uppercase; letter-spacing:2px;
  margin-bottom:8px;
}
.price-cols { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
.price-group-label {
  font-family:'Barlow Condensed',sans-serif; font-size:9px; font-weight:700;
  color:rgba(255,255,255,0.2); text-transform:uppercase; letter-spacing:1.5px;
  margin-bottom:5px; border-bottom:1px solid rgba(255,255,255,0.04); padding-bottom:3px;
}
.price-row { display:flex; justify-content:space-between; align-items:center; padding:3px 0; }
.price-name {
  font-family:'Barlow Condensed',sans-serif; font-size:12px; font-weight:600;
  color:rgba(255,255,255,0.6); white-space:nowrap; overflow:hidden;
  text-overflow:ellipsis; max-width:110px;
}
.price-val { font-family:'Barlow Condensed',sans-serif; font-size:12px; font-weight:700; }
.price-sep { height:1px; background:rgba(255,255,255,0.04); margin:5px 0; }

/* ── FOOTER ── */
.footer {
  padding:8px 16px; display:flex; justify-content:space-between; align-items:center;
  border-top:1px solid rgba(255,255,255,0.04);
}
.footer-l { font-size:9px; color:rgba(255,255,255,0.18); font-family:'Barlow',sans-serif; }
.footer-r {
  font-size:9px; color:rgba(255,255,255,0.18);
  font-family:'Barlow Condensed',sans-serif; text-transform:uppercase; letter-spacing:1px;
}
"""


# ══════════════════════════════════════════════════════════════════
#  HTML HELPERS
# ══════════════════════════════════════════════════════════════════

def _flag(race_name, race_flag=""):
    if race_flag:
        return race_flag
    for key, emoji in FLAG_MAP.items():
        if key.lower() in race_name.lower():
            return emoji
    return "🏁"


def _chg_class(val):
    if val > 0:   return "chg-pos"
    if val < 0:   return "chg-neg"
    return "chg-neu"


def _fmt_chg(val):
    if val > 0:   return f"+{val:.1f}m"
    if val < 0:   return f"−{abs(val):.1f}m"
    return "0.0m"


def _chip_html(cards):
    """Render card chip badges for a player (first card only shown on card)."""
    parts = []
    for key in cards:
        css   = CARD_CSS.get(key, "chip-noneg")
        label = html_lib.escape(CARD_LABEL.get(key, key))
        parts.append(f'<span class="card-chip {css}">{label}</span>')
    return "".join(parts)


def _build_podium_item(player, is_tied):
    rank      = player["rank"]
    p_css     = PODIUM_CSS.get(rank, "p3")
    medal     = PODIUM_MEDAL.get(rank, "")
    label     = PODIUM_LABEL.get(rank, f"{rank}th")
    name      = html_lib.escape(player["display_name"])
    pts       = int(player["points"])
    total     = int(player["total_points"])
    budget    = player["next_val"]
    chg       = player["price_chg"]
    chg_cls   = _chg_class(chg)
    chg_str   = _fmt_chg(chg)
    chips     = _chip_html(player["cards"])
    tie_badge = '<div class="tie-banner">Tied</div>' if is_tied else ""

    return f"""
    <div class="podium-item {p_css}">
      {tie_badge}
      <div class="podium-rank">{medal} {label}</div>
      <div class="podium-name">{name}</div>
      <div class="podium-pts">{pts}</div>
      <div class="podium-pts-label">race pts</div>
      <div class="podium-meta">
        <div class="podium-total">{total} total</div>
        <div class="podium-budget">${budget:.1f}m</div>
        <div class="podium-chg {chg_cls}">{chg_str}</div>
        {chips}
      </div>
    </div>"""


def _build_list_row(player):
    rank    = player["rank"]
    name    = html_lib.escape(player["display_name"])
    pts     = int(player["points"])
    total   = int(player["total_points"])
    budget  = player["next_val"]
    chg     = player["price_chg"]
    chg_cls = _chg_class(chg)
    chg_str = _fmt_chg(chg)
    chips   = _chip_html(player["cards"])

    return f"""
    <div class="row">
      <div class="row-rank">{rank}</div>
      <div class="row-name">{name}{chips}</div>
      <div class="row-pts-wrap">
        <span class="row-pts">{pts}</span>
        <span class="row-pts-label">pts</span>
      </div>
      <div class="row-budget">
        <span class="row-budget-val">${budget:.1f}m</span>
        <span class="row-budget-chg {chg_cls}">{chg_str}</span>
      </div>
      <div class="row-total">
        <span class="row-total-val">{total}</span>
        <span class="row-total-label">total</span>
      </div>
    </div>"""


def _build_price_rows(items):
    """Render a list of price-change rows (gainers above sep, losers below)."""
    gainers = [p for p in items if p["price_change"] > 0]
    losers  = [p for p in items if p["price_change"] < 0]
    rows    = []
    for p in gainers:
        name = html_lib.escape(p["player_name"])
        rows.append(
            f'<div class="price-row">'
            f'<span class="price-name">{name}</span>'
            f'<span class="price-val chg-pos">{_fmt_chg(p["price_change"])}</span>'
            f'</div>'
        )
    if gainers and losers:
        rows.append('<div class="price-sep"></div>')
    for p in losers:
        name = html_lib.escape(p["player_name"])
        rows.append(
            f'<div class="price-row">'
            f'<span class="price-name">{name}</span>'
            f'<span class="price-val chg-neg">{_fmt_chg(p["price_change"])}</span>'
            f'</div>'
        )
    return "\n".join(rows)


def build_html(race_info, results, race_prices, league_name, is_live=False):
    """
    Build the full standings card HTML.

    Args:
        race_info   : dict with matchday, race_name, race_flag, race_date,
                      session_type, next_race_name
        results     : list of enriched player dicts sorted by live_gd_rank,
                      each having: rank, display_name, points, total_points,
                      next_val, price_chg, cards
        race_prices : list of price dicts from prices_master for this matchday
        league_name : string shown in header badge
        is_live     : show pulsing LIVE dot in header
    """
    flag      = _flag(race_info["race_name"], race_info.get("race_flag", ""))
    now_str   = datetime.now(timezone.utc).strftime("%H:%M UTC")
    session   = race_info.get("session_type", "race").title()
    round_no  = race_info["matchday"]
    gp_name   = html_lib.escape(race_info["race_name"])
    league    = html_lib.escape(league_name)
    next_name = html_lib.escape(race_info.get("next_race_name", "next race"))

    live_html = (
        '<span class="live-dot"><span class="dot"></span> Live</span>'
        if is_live else ""
    )

    # ── Podium (always top 3 by list position) ──────────────────────
    podium_players = results[:3]
    # Detect ties: which ranks appear more than once among podium players
    from collections import Counter
    rank_counts = Counter(p["rank"] for p in podium_players)
    tied_ranks  = {r for r, c in rank_counts.items() if c > 1}

    podium_html = "\n".join(
        _build_podium_item(p, p["rank"] in tied_ranks)
        for p in podium_players
    )

    # Tie note: figure out which rank is skipped
    tie_note_html = ""
    if tied_ranks:
        for tied_rank in sorted(tied_ranks):
            count       = rank_counts[tied_rank]
            tied_names  = " &amp; ".join(
                html_lib.escape(p["display_name"])
                for p in podium_players if p["rank"] == tied_rank
            )
            skipped     = tied_rank + 1
            # only note the skip if that rank doesn't appear in the podium
            podium_ranks = [p["rank"] for p in podium_players]
            if skipped not in podium_ranks:
                tie_note_html = (
                    f'<div class="tie-note">'
                    f'★ {tied_names} tied · no {PODIUM_LABEL.get(skipped, f"{skipped}th")} place awarded'
                    f'</div>'
                )

    # ── Rest of list (position 4 onward) ────────────────────────────
    list_rows = []
    for i, player in enumerate(results[3:]):
        if i > 0:
            list_rows.append('<div class="divider"></div>')
        list_rows.append(_build_list_row(player))
    list_html = "\n".join(list_rows)

    # ── Price changes ────────────────────────────────────────────────
    # Keep only entries with non-zero price change, sort biggest move first
    drivers_chg = sorted(
        [p for p in race_prices if p["type"] == "Driver"      and p["price_change"] != 0],
        key=lambda x: -abs(x["price_change"])
    )
    constrs_chg = sorted(
        [p for p in race_prices if p["type"] == "Constructor" and p["price_change"] != 0],
        key=lambda x: -abs(x["price_change"])
    )
    drv_rows = _build_price_rows(drivers_chg)
    con_rows = _build_price_rows(constrs_chg)

    price_section = ""
    if drv_rows or con_rows:
        price_section = f"""
  <div class="price-section">
    <div class="price-section-title">Price changes &middot; into {next_name}</div>
    <div class="price-cols">
      <div>
        <div class="price-group-label">Drivers</div>
        {drv_rows}
      </div>
      <div>
        <div class="price-group-label">Constructors</div>
        {con_rows}
      </div>
    </div>
  </div>"""

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>{CSS}</style>
</head>
<body>
<div class="f1card">

  <div class="header">
    <div class="header-top">
      <span class="header-flag">{flag}</span>
      <div class="race-name">{gp_name}</div>
    </div>
    <div class="header-sub">
      <span>Round {round_no} &middot; {session}</span>
      {live_html}
      <span>{now_str}</span>
      <span class="league-badge">{league}</span>
    </div>
    <div class="final-label">Final Results</div>
  </div>

  <div class="podium">
    {podium_html}
  </div>

  {tie_note_html}

  <div class="col-header">
    <div class="col-lbl"></div>
    <div class="col-lbl">Player</div>
    <div class="col-lbl r">Pts</div>
    <div class="col-lbl r">Budget</div>
    <div class="col-lbl r">Total</div>
  </div>

  <div class="list">
    {list_html}
  </div>

  {price_section}

  <div class="footer">
    <div class="footer-l">Updates every 10 min</div>
    <div class="footer-r">fantasy.formula1.com</div>
  </div>

</div>
</body>
</html>"""


# ══════════════════════════════════════════════════════════════════
#  PNG via Playwright
# ══════════════════════════════════════════════════════════════════

def render_png(html_content, output_path):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise ImportError(
            "Playwright not installed.\n"
            "  Run:  pip install playwright\n"
            "        python -m playwright install chromium"
        )

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".html", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(html_content)
        tmp_path = tmp.name

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                args=["--no-sandbox", "--disable-setuid-sandbox"]
            )
            page = browser.new_page(
                viewport={"width": CARD_WIDTH, "height": 800},
                device_scale_factor=2,   # 2x → crisp retina PNG
            )
            page.goto(f"file:///{tmp_path.replace(os.sep, '/')}")
            page.wait_for_load_state("networkidle", timeout=10_000)
            page.locator(".f1card").screenshot(path=output_path)
            browser.close()
    finally:
        os.unlink(tmp_path)

    return os.path.abspath(output_path)


# ══════════════════════════════════════════════════════════════════
#  DATA LOADERS
# ══════════════════════════════════════════════════════════════════

def _load_name_codes():
    path  = os.path.join(SCRIPT_DIR, "player_names.txt")
    codes = {}
    if not os.path.exists(path):
        print("  ⚠  player_names.txt not found — using first 3 letters of username")
        return codes
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                name, code = line.split("=", 1)
                codes[name.strip()] = code.strip()
    print(f"  Name codes : {len(codes)} entries")
    return codes


def _build_headers():
    cookie_path = os.path.join(SCRIPT_DIR, "cookie.txt")
    cookie = (
        open(cookie_path, encoding="utf-8").read().strip()
        if os.path.exists(cookie_path) else ""
    )
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept":     "application/json, text/plain, */*",
        "Referer":    "https://fantasy.formula1.com/",
        "Cookie":     cookie,
    }


def _load_race(save_folder, matchday):
    for pattern in [
        os.path.join(save_folder, f"race_{matchday:02d}_*.json"),
        os.path.join(save_folder, f"race_{matchday}_*.json"),
    ]:
        files = [f for f in glob.glob(pattern) if "master" not in f]
        if files:
            print(f"  Race file  : {os.path.basename(files[0])}")
            with open(files[0], encoding="utf-8") as f:
                return json.load(f)
    raise FileNotFoundError(
        f"No race JSON for matchday {matchday} in {save_folder}\n"
        f"  Run f1_fantasy_league.py first."
    )


def _load_prices(save_folder, matchday):
    """Load prices_master.json. Returns (player_lookup, race_prices_list)."""
    path = os.path.join(save_folder, "prices_master.json")
    if not os.path.exists(path):
        print("  ⚠  prices_master.json not found — budget values will show 0")
        return {}, []
    with open(path, encoding="utf-8") as f:
        pm = json.load(f)
    all_prices  = pm.get("prices", [])
    # lookup: player_id → price_change for this matchday
    lookup      = {
        p["player_id"]: p["price_change"]
        for p in all_prices if p["race_number"] == matchday
    }
    race_prices = [p for p in all_prices if p["race_number"] == matchday]
    print(f"  Prices     : {len(race_prices)} entries for race {matchday}")
    return lookup, race_prices


# ══════════════════════════════════════════════════════════════════
#  DATA ADAPTER
# ══════════════════════════════════════════════════════════════════

def _adapt_results(race_data, price_lookup, name_codes):
    """
    Enrich and sort race data rows into HTML-ready dicts.
    Computes next_val = team_value + budget_remaining + sum(price_changes).
    """
    results = []
    for t in sorted(race_data, key=lambda x: x["live_gd_rank"]):
        user_name = t["user_name"]
        display   = name_codes.get(user_name, user_name[:3].upper())

        # Sum price changes for all picks this player holds
        price_chg = round(
            sum(price_lookup.get(p["id"], 0.0) for p in t.get("picks", [])), 2
        )
        next_val  = round(
            t.get("team_value", 0.0) + t.get("budget_remaining", 0.0) + price_chg, 2
        )

        # Parse cards_used strings → {NormalizedKey: count}
        cards = {}
        for card_str in t.get("cards_used", []):
            raw = card_str.split("(")[0].strip()
            key = CARD_ALIAS.get(raw) or CARD_ALIAS.get(raw.lower())
            if key:
                try:
                    count = int(card_str.split("used")[1].split("time")[0].strip())
                except Exception:
                    count = 1
                cards[key] = count

        results.append({
            "rank":         t["live_gd_rank"],
            "user_name":    user_name,
            "display_name": display,
            "points":       int(t.get("gd_points", 0)),
            "total_points": int(t.get("total_points", 0)),
            "next_val":     next_val,
            "price_chg":    price_chg,
            "cards":        cards,
        })
    return results


# ══════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════

def run(html_only=False, is_live=False):
    print("=" * 62)
    print("  F1 FANTASY — IMAGE CARD GENERATOR")
    print("=" * 62)

    save_folder = cfg.get("save_folder")
    league_name = cfg.get("league_name", "F1 Fantasy")
    print(f"  Save folder : {save_folder}")
    print(f"  League      : {league_name}")

    # 1. Player name codes
    print("\n[1/5] Loading player name codes...")
    name_codes = _load_name_codes()

    # 2. Auto-detect current race
    print("\n[2/5] Detecting current race from API calendar...")
    current_race = None
    nxt          = None
    try:
        headers           = _build_headers()
        calendar     = fetch_calendar(headers)
        current_race = detect_last_completed_race(calendar)
        _, nxt       = detect_current_race(calendar)
        print_calendar(calendar, current_race, nxt)
    except Exception as e:
        print(f"  ⚠  Calendar fetch failed: {e}")

    if current_race:
        matchday  = current_race["matchday"]
        race_info = {
            "matchday":       matchday,
            "race_name":      current_race["race_name"],
            "race_flag":      current_race.get("race_flag", ""),
            "race_date":      current_race.get("race_date", ""),
            "session_type":   current_race.get("session_type", "race"),
            "next_race_name": nxt["race_name"] if nxt else "",
        }
    else:
        print("  ⚠  No current race detected — falling back to latest race file")
        files = sorted([
            f for f in glob.glob(os.path.join(save_folder, "race_*.json"))
            if "master" not in f
        ])
        if not files:
            print(f"  ❌ No race files found in {save_folder}")
            return
        with open(files[-1], encoding="utf-8") as f:
            fallback = json.load(f)
        matchday  = fallback[0]["race_number"]
        race_info = {
            "matchday":       matchday,
            "race_name":      fallback[0].get("race_name", f"Race {matchday}"),
            "race_flag":      fallback[0].get("race_flag", ""),
            "race_date":      fallback[0].get("race_date", ""),
            "session_type":   fallback[0].get("session_type", "race"),
            "next_race_name": "",
        }

    print(f"\n  → Race {race_info['matchday']}: {race_info.get('race_flag','')} "
          f"{race_info['race_name']} ({race_info.get('race_date','')})")

    # 3. Load race JSON
    print(f"\n[3/5] Loading race data...")
    try:
        race_data = _load_race(save_folder, matchday)
    except FileNotFoundError as e:
        print(f"  ❌ {e}")
        return

    # 4. Load prices
    print(f"\n[4/5] Loading price data...")
    price_lookup, race_prices = _load_prices(save_folder, matchday)

    # 5. Build HTML & render
    print(f"\n[5/5] Generating standings image...")
    results      = _adapt_results(race_data, price_lookup, name_codes)
    html_content = build_html(race_info, results, race_prices, league_name, is_live=is_live)

    if html_only:
        html_path = os.path.join(SCRIPT_DIR, "standings_preview.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        print(f"  ✅ HTML preview : {html_path}")
        return html_path

    drive_path = os.path.join(save_folder, "f1_standings_latest.png")
    local_path = os.path.join(SCRIPT_DIR, "standings_preview.png")

    try:
        render_png(html_content, drive_path)
        import shutil
        shutil.copy2(drive_path, local_path)
        print(f"  ✅ Drive   : {drive_path}")
        print(f"  ✅ Preview : {local_path}")
        print(f"\n✅ Done — Google Drive will sync automatically.")
        return drive_path

    except ImportError as e:
        print(f"\n  ⚠  {e}")
        print("  Falling back to HTML preview...")
        html_path = os.path.join(SCRIPT_DIR, "standings_preview.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        print(f"  ✅ HTML preview : {html_path}")
        return html_path


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--html", action="store_true",
                        help="HTML preview only (no Playwright needed)")
    parser.add_argument("--live", action="store_true",
                        help="Show LIVE dot in header")
    args = parser.parse_args()
    run(html_only=args.html, is_live=args.live)
