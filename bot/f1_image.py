"""
f1_image.py  —  F1 Fantasy Standings Card Generator
=====================================================
Generates a polished PNG standings card from live race data.

Renders an HTML template using Playwright (headless Chromium).
Playwright is installed automatically on first run via:
  pip install playwright
  playwright install chromium

GitHub Actions usage:
  - pip install playwright is in requirements.txt
  - playwright install --with-deps chromium  is a workflow step (see README)

Output:
  standings_card.png  (480×auto px, dark F1 theme)

Called from f1_quick.py:
  from f1_image import generate_standings_image
  img_path = generate_standings_image(race, results, league_name)
"""

import os
import sys
import json
import textwrap
import tempfile
import html as html_lib
from datetime import datetime, timezone

# ── Card dimensions ───────────────────────────────────────────────
CARD_WIDTH = 480   # px — matches the design viewport

# ── Card icons SVG paths (inline, no external deps) ───────────────
CARD_SVG = {
    "NoNeg":     '<svg viewBox="0 0 10 10"><path d="M2 5h6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><circle cx="5" cy="5" r="4" stroke="currentColor" stroke-width="1" fill="none"/></svg>',
    "Auto":      '<svg viewBox="0 0 10 10"><circle cx="5" cy="5" r="3" stroke="currentColor" stroke-width="1" fill="none"/><circle cx="5" cy="5" r="1.2" fill="currentColor"/></svg>',
    "Limitless": '<svg viewBox="0 0 10 10"><path d="M1 5 Q2.5 2 5 5 Q7.5 8 9 5 Q7.5 2 5 5 Q2.5 8 1 5Z" stroke="currentColor" stroke-width="1" fill="none"/></svg>',
    "WC":        '<svg viewBox="0 0 10 10"><path d="M5 1l1.2 2.5L9 4.1 6.8 6.3l.5 3.2L5 8l-2.3 1.5.5-3.2L1 4.1l2.8-.6L5 1z" stroke="currentColor" stroke-width="1" fill="none"/></svg>',
    "ExDRS":     '<svg viewBox="0 0 10 10"><path d="M2 7l3-4 3 4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" fill="none"/></svg>',
    "FinFix":    '<svg viewBox="0 0 10 10"><path d="M2 5h2l1.5-3 2 6 1.5-3H9" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round" fill="none"/></svg>',
}

CARD_CSS = {
    "NoNeg":     "card-noneg",
    "Auto":      "card-auto",
    "Limitless": "card-limitless",
    "WC":        "card-wc",
    "ExDRS":     "card-exdrs",
    "FinFix":    "card-finfx",
}

CARD_LABEL = {
    "NoNeg":     "No Neg",
    "Auto":      "Autopilot",
    "Limitless": "Limitless",
    "WC":        "Wildcard",
    "ExDRS":     "Extra DRS",
    "FinFix":    "Final Fix",
}

FLAG_MAP = {
    "Australia":      "🇦🇺", "China":          "🇨🇳", "Japan":          "🇯🇵",
    "Bahrain":        "🇧🇭", "Saudi Arabia":   "🇸🇦", "United States":  "🇺🇸",
    "Italy":          "🇮🇹", "Monaco":         "🇲🇨", "Canada":         "🇨🇦",
    "Spain":          "🇪🇸", "Austria":        "🇦🇹", "United Kingdom": "🇬🇧",
    "Hungary":        "🇭🇺", "Belgium":        "🇧🇪", "Netherlands":    "🇳🇱",
    "Azerbaijan":     "🇦🇿", "Singapore":      "🇸🇬", "Mexico":         "🇲🇽",
    "Brazil":         "🇧🇷", "UAE":            "🇦🇪", "Abu Dhabi":      "🇦🇪",
    "Qatar":          "🇶🇦", "Las Vegas":      "🇺🇸", "Miami":          "🇺🇸",
    "Melbourne":      "🇦🇺", "Suzuka":         "🇯🇵", "Shanghai":       "🇨🇳",
    "Imola":          "🇮🇹", "Silverstone":    "🇬🇧", "Spa":            "🇧🇪",
}


# ── HTML template ─────────────────────────────────────────────────

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@400;600;700;800;900&family=Barlow:wght@400;500;600&display=swap');

* { margin: 0; padding: 0; box-sizing: border-box; }

body {
  background: #0a0a0a;
  width: 480px;
  font-family: 'Barlow', sans-serif;
}

.card {
  width: 480px;
  background: #111111;
  overflow: hidden;
  position: relative;
}

.card::before {
  content: '';
  position: absolute;
  inset: 0;
  background-image:
    repeating-linear-gradient(45deg, transparent, transparent 2px, rgba(255,255,255,0.012) 2px, rgba(255,255,255,0.012) 4px),
    repeating-linear-gradient(-45deg, transparent, transparent 2px, rgba(255,255,255,0.008) 2px, rgba(255,255,255,0.008) 4px);
  pointer-events: none;
  z-index: 0;
}

.header {
  background: linear-gradient(135deg, #cc0000 0%, #990000 40%, #1a0000 100%);
  padding: 20px 24px 18px;
  position: relative;
  overflow: hidden;
}

.header::before {
  content: 'F1';
  position: absolute;
  right: -10px;
  top: -20px;
  font-family: 'Barlow Condensed', sans-serif;
  font-size: 140px;
  font-weight: 900;
  color: rgba(255,255,255,0.04);
  line-height: 1;
  letter-spacing: -8px;
}

.header::after {
  content: '';
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: linear-gradient(90deg, #cc0000, #ff3333, #cc0000);
}

.header-top {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
  position: relative;
  z-index: 1;
}

.flag-icon { font-size: 26px; line-height: 1; }

.race-name {
  font-family: 'Barlow Condensed', sans-serif;
  font-size: 22px;
  font-weight: 800;
  color: #ffffff;
  text-transform: uppercase;
  letter-spacing: 1px;
  line-height: 1;
}

.race-sub {
  font-family: 'Barlow Condensed', sans-serif;
  font-size: 13px;
  font-weight: 600;
  color: rgba(255,255,255,0.55);
  text-transform: uppercase;
  letter-spacing: 2px;
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 4px;
}

.live-dot {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: #ff6b6b;
}

.dot {
  width: 7px;
  height: 7px;
  background: #ff4444;
  border-radius: 50%;
}

.league-badge {
  background: rgba(255,255,255,0.12);
  border: 1px solid rgba(255,255,255,0.15);
  border-radius: 4px;
  padding: 2px 8px;
  font-family: 'Barlow Condensed', sans-serif;
  font-size: 11px;
  font-weight: 700;
  color: rgba(255,255,255,0.7);
  letter-spacing: 1.5px;
  text-transform: uppercase;
}

.standings-list { padding: 0; position: relative; z-index: 1; }

.row {
  display: grid;
  grid-template-columns: 40px 1fr auto;
  align-items: center;
  padding: 11px 20px 11px 16px;
  border-bottom: 1px solid rgba(255,255,255,0.05);
  position: relative;
  gap: 10px;
}

.row:last-child { border-bottom: none; }

.row.top1 { background: linear-gradient(90deg, rgba(255,215,0,0.08) 0%, transparent 60%); }
.row.top1::before { content:''; position:absolute; left:0; top:0; bottom:0; width:3px; background:linear-gradient(180deg,#ffd700,#ffaa00); border-radius:0 2px 2px 0; }

.row.top2 { background: linear-gradient(90deg, rgba(192,192,192,0.07) 0%, transparent 60%); }
.row.top2::before { content:''; position:absolute; left:0; top:0; bottom:0; width:3px; background:linear-gradient(180deg,#c0c0c0,#999999); border-radius:0 2px 2px 0; }

.row.top3 { background: linear-gradient(90deg, rgba(205,127,50,0.07) 0%, transparent 60%); }
.row.top3::before { content:''; position:absolute; left:0; top:0; bottom:0; width:3px; background:linear-gradient(180deg,#cd7f32,#a0622a); border-radius:0 2px 2px 0; }

.rank-col { text-align: center; }

.rank-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border-radius: 50%;
  font-family: 'Barlow Condensed', sans-serif;
  font-size: 14px;
  font-weight: 900;
  color: #888;
  background: rgba(255,255,255,0.05);
  border: 1px solid rgba(255,255,255,0.08);
}

.top1 .rank-badge { color:#ffd700; background:rgba(255,215,0,0.12); border-color:rgba(255,215,0,0.3); }
.top2 .rank-badge { color:#c0c0c0; background:rgba(192,192,192,0.1); border-color:rgba(192,192,192,0.25); }
.top3 .rank-badge { color:#cd7f32; background:rgba(205,127,50,0.1); border-color:rgba(205,127,50,0.25); }

.player-col { display: flex; flex-direction: column; gap: 4px; }

.player-name {
  font-family: 'Barlow Condensed', sans-serif;
  font-size: 18px;
  font-weight: 700;
  color: #f0f0f0;
  text-transform: uppercase;
  letter-spacing: 1px;
  line-height: 1;
}

.picks-row { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }

.pick {
  font-family: 'Barlow Condensed', sans-serif;
  font-size: 10px;
  font-weight: 600;
  color: #888;
  background: rgba(255,255,255,0.06);
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: 3px;
  padding: 1px 5px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.pick.captain   { color:#ffd700; background:rgba(255,215,0,0.10); border-color:rgba(255,215,0,0.25); }
.pick.megacap   { color:#ff6b35; background:rgba(255,107,53,0.12); border-color:rgba(255,107,53,0.30); }
.pick.constructor { color:#9ca3af; background:rgba(156,163,175,0.08); border-color:rgba(156,163,175,0.15); }

.cards-row { display:flex; gap:4px; margin-top:2px; flex-wrap:wrap; }

.card-icon {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-family: 'Barlow', sans-serif;
  font-size: 9px;
  font-weight: 600;
  border-radius: 3px;
  padding: 2px 5px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.card-icon svg { width:10px; height:10px; flex-shrink:0; }

.card-noneg     { color:#4ecdc4; background:rgba(78,205,196,0.10); border:1px solid rgba(78,205,196,0.20); }
.card-limitless { color:#a78bfa; background:rgba(167,139,250,0.10); border:1px solid rgba(167,139,250,0.20); }
.card-wc        { color:#fbbf24; background:rgba(251,191,36,0.10); border:1px solid rgba(251,191,36,0.20); }
.card-exdrs     { color:#34d399; background:rgba(52,211,153,0.10); border:1px solid rgba(52,211,153,0.20); }
.card-auto      { color:#60a5fa; background:rgba(96,165,250,0.10); border:1px solid rgba(96,165,250,0.20); }
.card-finfx     { color:#f87171; background:rgba(248,113,113,0.10); border:1px solid rgba(248,113,113,0.20); }

.points-col {
  text-align: right;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 2px;
}

.points-val {
  font-family: 'Barlow Condensed', sans-serif;
  font-size: 24px;
  font-weight: 900;
  color: #ffffff;
  line-height: 1;
  letter-spacing: -0.5px;
}

.top1 .points-val { color: #ffd700; }
.top2 .points-val { color: #d0d0d0; }
.top3 .points-val { color: #cd7f32; }

.points-label {
  font-family: 'Barlow', sans-serif;
  font-size: 9px;
  font-weight: 500;
  color: #555;
  text-transform: uppercase;
  letter-spacing: 1px;
}

.section-divider {
  height: 1px;
  background: linear-gradient(90deg, transparent, rgba(204,0,0,0.3), transparent);
  margin: 0 20px;
}

.footer {
  background: rgba(0,0,0,0.4);
  padding: 10px 20px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-top: 1px solid rgba(255,255,255,0.06);
  position: relative;
  z-index: 1;
}

.footer-left {
  font-family: 'Barlow', sans-serif;
  font-size: 10px;
  color: #444;
  display: flex;
  align-items: center;
  gap: 6px;
}

.footer-right {
  font-family: 'Barlow Condensed', sans-serif;
  font-size: 10px;
  color: #444;
  text-transform: uppercase;
  letter-spacing: 1.5px;
}
"""


def _flag_for_race(race: dict) -> str:
    country = race.get("country", "")
    for key, flag in FLAG_MAP.items():
        if key.lower() in country.lower():
            return flag
    return race.get("flag", "🏁")


def _session_label(race: dict) -> str:
    stype = race.get("session_type", "race").lower()
    return {"race": "Race", "qualifying": "Qualifying", "sprint": "Sprint"}.get(stype, stype.title())


def _build_pick_chips(pick_details: list) -> str:
    """Sort and render pick chips: MC → C → drivers → constructors."""
    sorted_picks = sorted(
        pick_details,
        key=lambda p: (
            0 if p.get("ismgcaptain") else
            1 if p.get("iscaptain")   else
            2 if p.get("skill", 1) == 1 else 3
        )
    )
    chips = []
    for p in sorted_picks:
        tla = html_lib.escape(p.get("tla", "?"))
        if p.get("ismgcaptain"):
            chips.append(f'<span class="pick megacap">{tla} ★★</span>')
        elif p.get("iscaptain"):
            chips.append(f'<span class="pick captain">{tla} ★</span>')
        elif p.get("skill", 1) == 2:
            chips.append(f'<span class="pick constructor">{tla}</span>')
        else:
            chips.append(f'<span class="pick">{tla}</span>')
    return "".join(chips)


def _build_card_badges(cards: dict) -> str:
    """Render fantasy card badges."""
    badges = []
    for label in cards:
        css  = CARD_CSS.get(label, "card-noneg")
        svg  = CARD_SVG.get(label, "")
        text = CARD_LABEL.get(label, label)
        badges.append(f'<span class="card-icon {css}">{svg}{html_lib.escape(text)}</span>')
    return "".join(badges)


def _build_row(entry: dict) -> str:
    rank   = entry["rank"]
    name   = html_lib.escape(entry.get("display_name", entry.get("user_name", "?")))
    points = int(entry["points"])
    picks  = entry.get("pick_details", [])
    cards  = entry.get("cards", {})

    row_cls = ""
    if rank == 1: row_cls = "top1"
    elif rank == 2: row_cls = "top2"
    elif rank == 3: row_cls = "top3"

    picks_html = _build_pick_chips(picks)
    cards_html = _build_card_badges(cards)
    cards_block = f'<div class="cards-row">{cards_html}</div>' if cards_html else ""

    return f"""
    <div class="row {row_cls}">
      <div class="rank-col"><span class="rank-badge">{rank}</span></div>
      <div class="player-col">
        <div class="player-name">{name}</div>
        <div class="picks-row">{picks_html}</div>
        {cards_block}
      </div>
      <div class="points-col">
        <span class="points-val">{points}</span>
        <span class="points-label">pts</span>
      </div>
    </div>"""


def build_html(race: dict, results: list, league_name: str, is_live: bool = False) -> str:
    """
    Build the full standings card HTML string.
    Can be used standalone (save as .html) or fed into Playwright for PNG.
    """
    flag     = _flag_for_race(race)
    now_str  = datetime.now(timezone.utc).strftime("%H:%M UTC")
    session  = _session_label(race)
    round_no = race.get("meeting_number", race.get("gameday_id", "?"))
    gp_name  = html_lib.escape(race.get("meeting_name", "Grand Prix"))
    league   = html_lib.escape(league_name)

    live_html = (
        '<span class="live-dot"><span class="dot"></span> Live</span>'
        if is_live else ""
    )

    rows_html = "\n".join(_build_row(e) for e in results)

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>{CSS}</style>
</head>
<body>
<div class="card">

  <div class="header">
    <div class="header-top">
      <span class="flag-icon">{flag}</span>
      <div><div class="race-name">{gp_name}</div></div>
    </div>
    <div class="race-sub">
      <span>Round {round_no} · {session}</span>
      {live_html}
      <span>{now_str}</span>
      <span class="league-badge">{league}</span>
    </div>
  </div>

  <div class="standings-list">
    {rows_html}
  </div>

  <div class="footer">
    <div class="footer-left">
      <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
        <circle cx="6" cy="6" r="5" stroke="#444" stroke-width="1"/>
        <path d="M6 3v3.5l2 1.2" stroke="#444" stroke-width="1" stroke-linecap="round"/>
      </svg>
      Updates every 10 min
    </div>
    <div class="footer-right">fantasy.formula1.com</div>
  </div>

</div>
</body>
</html>"""


# ── PNG generation via Playwright ────────────────────────────────

def generate_standings_image(
    race: dict,
    results: list,
    league_name: str,
    output_path: str = "standings_card.png",
    is_live: bool = False,
) -> str:
    """
    Render the standings card to a PNG file using Playwright headless Chromium.

    Args:
        race         : race dict from get_current_race()
        results      : list of result dicts from get_standings()
                       Each must have: rank, user_name, points,
                                       pick_details, cards, display_name (optional)
        league_name  : string shown in header badge
        output_path  : where to save the PNG (default: standings_card.png)
        is_live      : show pulsing LIVE dot in header

    Returns:
        Absolute path to the saved PNG.

    Raises:
        ImportError  : if playwright is not installed
        RuntimeError : if Chromium is not installed
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERROR: playwright not installed.")
        print("  Run:  pip install playwright && playwright install chromium")
        raise

    html_content = build_html(race, results, league_name, is_live=is_live)

    # Write HTML to a temp file so Playwright can load local fonts properly
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
                device_scale_factor=2,   # 2x for crisp retina-quality PNG
            )

            page.goto(f"file://{tmp_path}")

            # Wait for Google Fonts to load
            page.wait_for_load_state("networkidle", timeout=10_000)

            # Grab the card element only (no body padding)
            card = page.locator(".card")
            card.screenshot(path=output_path)

            browser.close()

        abs_path = os.path.abspath(output_path)
        print(f"  Image saved: {abs_path}")
        return abs_path

    finally:
        os.unlink(tmp_path)


# ── Convenience: save HTML preview (no Playwright needed) ─────────

def save_html_preview(
    race: dict,
    results: list,
    league_name: str,
    output_path: str = "standings_preview.html",
    is_live: bool = False,
) -> str:
    """Save the card as a standalone HTML file for browser preview."""
    html_content = build_html(race, results, league_name, is_live=is_live)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    abs_path = os.path.abspath(output_path)
    print(f"  HTML preview saved: {abs_path}")
    return abs_path


# ── Inject display_name into results using nicknames map ─────────

def apply_nicknames(results: list, nicknames: dict) -> list:
    """
    Add display_name to each result using the nicknames config.
    Falls back to user_name if not found.
    """
    for r in results:
        r["display_name"] = nicknames.get(r["user_name"], r["user_name"])
    return results


# ── CLI: quick test with dummy data ──────────────────────────────

def _make_test_data():
    race = {
        "meeting_name":   "Japanese Grand Prix",
        "meeting_number": 3,
        "gameday_id":     3,
        "session_type":   "race",
        "country":        "Suzuka, Japan",
        "flag":           "🇯🇵",
    }
    results = [
        {"rank": 1, "user_name": "Fabio Mucci",        "display_name": "FAB", "points": 312,
         "pick_details": [{"tla":"VER","skill":1,"iscaptain":0,"ismgcaptain":1},
                          {"tla":"HAM","skill":1,"iscaptain":1,"ismgcaptain":0},
                          {"tla":"LEC","skill":1,"iscaptain":0,"ismgcaptain":0},
                          {"tla":"NOR","skill":1,"iscaptain":0,"ismgcaptain":0},
                          {"tla":"ALO","skill":1,"iscaptain":0,"ismgcaptain":0},
                          {"tla":"RBR","skill":2,"iscaptain":0,"ismgcaptain":0}],
         "cards": {"NoNeg": 3, "Limitless": 3}},
        {"rank": 2, "user_name": "Danilo Iglesias",    "display_name": "DAN", "points": 287,
         "pick_details": [{"tla":"HAM","skill":1,"iscaptain":1,"ismgcaptain":0},
                          {"tla":"VER","skill":1,"iscaptain":0,"ismgcaptain":0},
                          {"tla":"LEC","skill":1,"iscaptain":0,"ismgcaptain":0},
                          {"tla":"SAI","skill":1,"iscaptain":0,"ismgcaptain":0},
                          {"tla":"PIA","skill":1,"iscaptain":0,"ismgcaptain":0},
                          {"tla":"FER","skill":2,"iscaptain":0,"ismgcaptain":1}],
         "cards": {"ExDRS": 3}},
        {"rank": 3, "user_name": "Ricardo Mucci",      "display_name": "RIC", "points": 264,
         "pick_details": [{"tla":"NOR","skill":1,"iscaptain":1,"ismgcaptain":0},
                          {"tla":"PIA","skill":1,"iscaptain":0,"ismgcaptain":0},
                          {"tla":"HAM","skill":1,"iscaptain":0,"ismgcaptain":0},
                          {"tla":"ANT","skill":1,"iscaptain":0,"ismgcaptain":0},
                          {"tla":"ALO","skill":1,"iscaptain":0,"ismgcaptain":0},
                          {"tla":"MCL","skill":2,"iscaptain":0,"ismgcaptain":1}],
         "cards": {}},
        {"rank": 4, "user_name": "Guilherme Figueiredo","display_name": "GUI", "points": 251,
         "pick_details": [{"tla":"VER","skill":1,"iscaptain":0,"ismgcaptain":1},
                          {"tla":"NOR","skill":1,"iscaptain":1,"ismgcaptain":0},
                          {"tla":"HAM","skill":1,"iscaptain":0,"ismgcaptain":0},
                          {"tla":"ANT","skill":1,"iscaptain":0,"ismgcaptain":0},
                          {"tla":"GAS","skill":1,"iscaptain":0,"ismgcaptain":0},
                          {"tla":"RBR","skill":2,"iscaptain":0,"ismgcaptain":0}],
         "cards": {"WC": 4}},
        {"rank": 5, "user_name": "Eduardo Santos Lima","display_name": "EDU", "points": 238,
         "pick_details": [{"tla":"LEC","skill":1,"iscaptain":1,"ismgcaptain":0},
                          {"tla":"SAI","skill":1,"iscaptain":0,"ismgcaptain":0},
                          {"tla":"HAM","skill":1,"iscaptain":0,"ismgcaptain":0},
                          {"tla":"NOR","skill":1,"iscaptain":0,"ismgcaptain":0},
                          {"tla":"VER","skill":1,"iscaptain":0,"ismgcaptain":0},
                          {"tla":"FER","skill":2,"iscaptain":0,"ismgcaptain":1}],
         "cards": {}},
        {"rank": 6, "user_name": "Robson Jardim",      "display_name": "ROB", "points": 225,
         "pick_details": [{"tla":"ALO","skill":1,"iscaptain":0,"ismgcaptain":1},
                          {"tla":"STR","skill":1,"iscaptain":1,"ismgcaptain":0},
                          {"tla":"NOR","skill":1,"iscaptain":0,"ismgcaptain":0},
                          {"tla":"PIA","skill":1,"iscaptain":0,"ismgcaptain":0},
                          {"tla":"LEC","skill":1,"iscaptain":0,"ismgcaptain":0},
                          {"tla":"AMR","skill":2,"iscaptain":0,"ismgcaptain":0}],
         "cards": {"Auto": 6}},
        {"rank": 7, "user_name": "Vinicius Agostini",  "display_name": "VIN", "points": 210,
         "pick_details": [{"tla":"PIA","skill":1,"iscaptain":1,"ismgcaptain":0},
                          {"tla":"NOR","skill":1,"iscaptain":0,"ismgcaptain":0},
                          {"tla":"LEC","skill":1,"iscaptain":0,"ismgcaptain":0},
                          {"tla":"SAI","skill":1,"iscaptain":0,"ismgcaptain":0},
                          {"tla":"HAM","skill":1,"iscaptain":0,"ismgcaptain":0},
                          {"tla":"MCL","skill":2,"iscaptain":0,"ismgcaptain":1}],
         "cards": {}},
        {"rank": 7, "user_name": "Raphael Stein",      "display_name": "STE", "points": 210,
         "pick_details": [{"tla":"HAM","skill":1,"iscaptain":0,"ismgcaptain":1},
                          {"tla":"ANT","skill":1,"iscaptain":1,"ismgcaptain":0},
                          {"tla":"VER","skill":1,"iscaptain":0,"ismgcaptain":0},
                          {"tla":"NOR","skill":1,"iscaptain":0,"ismgcaptain":0},
                          {"tla":"RUS","skill":1,"iscaptain":0,"ismgcaptain":0},
                          {"tla":"MER","skill":2,"iscaptain":0,"ismgcaptain":0}],
         "cards": {"NoNeg": 7}},
        {"rank": 9, "user_name": "Priscila Rigotto",   "display_name": "PRI", "points": 195,
         "pick_details": [{"tla":"NOR","skill":1,"iscaptain":0,"ismgcaptain":1},
                          {"tla":"PIA","skill":1,"iscaptain":1,"ismgcaptain":0},
                          {"tla":"HAM","skill":1,"iscaptain":0,"ismgcaptain":0},
                          {"tla":"RUS","skill":1,"iscaptain":0,"ismgcaptain":0},
                          {"tla":"VER","skill":1,"iscaptain":0,"ismgcaptain":0},
                          {"tla":"MCL","skill":2,"iscaptain":0,"ismgcaptain":0}],
         "cards": {}},
        {"rank": 10,"user_name": "Liberio Junior",     "display_name": "LIB", "points": 182,
         "pick_details": [{"tla":"SAI","skill":1,"iscaptain":1,"ismgcaptain":0},
                          {"tla":"LEC","skill":1,"iscaptain":0,"ismgcaptain":0},
                          {"tla":"HAM","skill":1,"iscaptain":0,"ismgcaptain":0},
                          {"tla":"PIA","skill":1,"iscaptain":0,"ismgcaptain":0},
                          {"tla":"NOR","skill":1,"iscaptain":0,"ismgcaptain":0},
                          {"tla":"FER","skill":2,"iscaptain":0,"ismgcaptain":1}],
         "cards": {"FinFix": 10}},
        {"rank": 11,"user_name": "Rafael Dias",        "display_name": "RAF", "points": 167,
         "pick_details": [{"tla":"VER","skill":1,"iscaptain":1,"ismgcaptain":0},
                          {"tla":"HAM","skill":1,"iscaptain":0,"ismgcaptain":0},
                          {"tla":"NOR","skill":1,"iscaptain":0,"ismgcaptain":0},
                          {"tla":"PIA","skill":1,"iscaptain":0,"ismgcaptain":0},
                          {"tla":"LEC","skill":1,"iscaptain":0,"ismgcaptain":0},
                          {"tla":"RBR","skill":2,"iscaptain":0,"ismgcaptain":1}],
         "cards": {}},
    ]
    return race, results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate F1 standings card")
    parser.add_argument("--html",  action="store_true", help="Save HTML preview only (no Playwright needed)")
    parser.add_argument("--live",  action="store_true", help="Show LIVE dot in header")
    parser.add_argument("--out",   default="standings_card.png", help="Output PNG path")
    args = parser.parse_args()

    race, results = _make_test_data()
    league = "Bolão F1"

    if args.html:
        path = save_html_preview(race, results, league, is_live=args.live)
        print(f"Open in browser: {path}")
    else:
        try:
            path = generate_standings_image(race, results, league,
                                            output_path=args.out, is_live=args.live)
            print(f"PNG ready: {path}")
        except ImportError:
            print("\nPlaywright not installed. Falling back to HTML preview.")
            path = save_html_preview(race, results, league, is_live=args.live)
            print(f"Open in browser: {path}")
