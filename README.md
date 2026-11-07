# F1 Bolão — Fantasy League Web App

A mobile-first dashboard for our private F1 Fantasy league (11 players). Static site on
GitHub Pages — plain HTML, CSS and JavaScript, no framework or build step. The pages read
JSON files that GitHub Actions refreshes from the F1 Fantasy API.

**Live site:** https://raphael123mendes.github.io/f1bolaoapp/

---

## How it works

```
F1 Fantasy API ──► GitHub Actions (Python) ──► JSON files in this repo ──► HTML pages (GitHub Pages)
```

| Workflow | When | What it does |
|---|---|---|
| **F1 Fantasy Standings** (`f1_standings.yml` → `bot/f1_quick.py`) | **Start by hand** up to 2 h before qualifying / sprint / race (leave `force_run` = 0) | **Session mode:** one run covers the whole session and publishes `live/*.json` at checkpoints — qualifying: Q1 end, Q2 end (from F1 Fantasy bonuses), complete, final +15 min; sprint/race: every ~10 laps (estimated), chequered flag, final (race +45 min). |
| **F1 Results Pipeline** (`f1_results_pipeline.yml` → `results_SC/run_all.py` + `build_fantasy_f1.py`) | Manually after each race (Actions → *Run workflow*; optional race number) | Final data for the race → `results.json`, `picks.json`, `prices.json`, `breakdowns.json`, `races.json` |
| **F1 Refresh Cookie** (`f1_get_cookie.yml` → `bot/f1_login.py`) | Manually, when the other workflows fail with 401 | Logs in to F1 Fantasy and saves a fresh `F1_COOKIE` secret |

### Live session mode (`bot/session_mode.py`)
- Uses only the F1 Fantasy API: the schedule feed's session status (1 upcoming, 2 live,
  4 complete) and the constructor bonuses that appear when Q1 / Q2 end ("…Q2", "…Q3").
- Laps are **estimated** (~10 laps per 15 min); red flags aren't visible, so after the normal
  race length updates say "session still running".
- Started early it waits for the start; a session that's already final is skipped.
- Test without network: `python bot/simulate_session_mode.py`
- `SESSION_MODE: "0"` in `f1_standings.yml` → old one-update-per-run behaviour.

### Secrets (Settings → Secrets and variables → Actions)
- `F1_COOKIE` — F1 Fantasy session cookie (set by *F1 Refresh Cookie*)
- `F1_EMAIL`, `F1_PASSWORD` — F1 account used by *F1 Refresh Cookie*
- `GH_PAT` — token that lets *F1 Refresh Cookie* update the `F1_COOKIE` secret

Never put phone numbers, API keys or passwords in files in this repo — it is public.

---

## Season & calendar

- The season is set **once** in `config.json` → `"season": 2026`.
- The **master calendar** is `calendar/2026.json` — the only calendar to edit by hand
  (rounds, F1 Fantasy `gameday_id`, names, circuit, sprint/cancelled flags, session times in UTC).
- `races.json` (used by the website) is **generated** from it — don't edit it by hand.
  After changing the calendar locally, run `python season.py` to regenerate it
  (the results pipeline also regenerates it on every run).
- Every results run compares the calendar with F1 Fantasy's own calendar and prints
  ⚠ warnings in the log if gameday numbers, names or race dates disagree.

### New season checklist
1. Add `calendar/2027.json` (same format as `calendar/2026.json`).
2. Set `"season": 2027` in `config.json`.
3. Check `league_id` in `bot/config.json` and `results_SC/config.json`.
4. Archive or clear last season's data files (`results.json`, `picks.json`, `prices.json`,
   `breakdowns.json`, `results_SC/data/race_*.json`, `live/*.json`).

---

## File structure

```
/
├── index.html            ← League Standings (home)
├── results.html          ← Race Results (live + final)
├── market.html           ← Market Overview
├── race.html             ← Race Analysis
├── team.html             ← Player detail
├── driver.html           ← Driver & Constructor analytics
├── historical.html       ← Historical performance per entity
├── stats.html            ← Season Stats
│
├── config.json           ← season + site settings (card list, image slugs)
├── season.py             ← reads calendar/<season>.json, generates races.json
├── calendar/2026.json    ← MASTER calendar
├── races.json            ← generated calendar for the pages
├── results.json          ← one row per player per race (final)
├── picks.json            ← one row per pick per player per race
├── prices.json           ← prices per entity per race
├── breakdowns.json       ← points by scoring category per pick
├── live/                 ← live race-weekend data (written by the standings bot)
│
├── bot/                  ← live bot (f1_quick.py), F1 login (f1_login.py), config
├── results_SC/           ← results pipeline scripts + data/race_NN_*.json snapshots
└── images/               ← portraits, car liveries, circuit SVGs, logos
```

---

## Pages

### `index.html` — League Standings
- P1 spotlight hero and expandable row per player
- Expanded row: avg points per race, prize total, **gap to the player above (▲, red) and below (▼, green)**
- **Trophy Race** chart — season rank per round; player names at the end of each line
- **Cards Used** table — players in rows, cards in columns, cell shows the round played (e.g. `R9`)
- **Data:** `results.json`

### `results.html` — Race Results
- Round switcher; live mode during race weekends (from `live/`)
- Player table by race rank: race points, season total, prize, budget and budget change
- Under each player (own line): cards used (`EXTRA DRS · R13`) and penalties
  (`-20 (Transfers)`, `-25 (Inactive)`; tap/hover for the breakdown)
- Weekend MVPs: top driver, top constructor, market riser/faller
- **Data:** `results.json`, `picks.json`, `prices.json`, `races.json`

### `market.html` — Market Overview
- Hero section: Market Sentiment (Bullish/Bearish), Top Riser, Top Faller, Best Pts/$M, Most Volatile
- All 33 entities (drivers + constructors) in sortable, filterable table
- Columns: Entity + image, Season Pts, Price, Pts/$M badge, Absolute price change + percentage
- Filter: All / Drivers / Constructors
- Sort: Points / Price / Pts per $M / Price Change
- **Click any row → navigates to `driver.html?entity=Name`**
- **Data:** `picks.json` (deduplicated), `prices.json`

### `race.html` — Race Analysis
- Round switcher + player selector dropdown
- Team Roster Telemetry: pick cards with portrait/car, captain badge (2×/3×), NEW badge for pick changes, ownership bar
- Performance Matrix radar chart (5 axes: Qualifying, Race, Sprint, Overtakes, Pit/Bonus) — player vs league average. Tap any axis → popup with category breakdown
- Strategic Differentiators: Exclusive Assets (≤40% ownership) vs Common Holdings (≥60%)
- Raw Telemetry breakdown table — all picks sorted by points, tappable for full category detail
- **Landscape mode:** Left sidebar with pick list, right panel with full horizontal bar chart for all 20 scoring categories
- **Data:** `picks.json`, `breakdowns.json`, `results.json`, `races.json`

### `team.html` — Player detail
- Opened from Standings → *View Full Team Details* (`team.html?user=Liberio+Junior`)
- Profile header, tier badge, metric cards
- 4 charts — **latest 8 races visible, swipe left for earlier ones, tap any race for its value**:
  - Budget Evolution ($M) — exact value on every point
  - Points per Race — value on every bar
  - Prize Earnings ($) — exact value on prize-winning races
  - Season Rank — green = moved up, red = moved down, white = same
- Strategy Cards — used cards lit up with the round played
- Driver lineup and constructors; tap any → `driver.html`
- **Data:** `results.json`, `picks.json`, `prices.json`, `config.json`

### `driver.html` — Driver & Constructor Analytics
- Accessed from Market (`click row`) or directly: `driver.html?entity=Charles+Leclerc`
- Filter (All/Drivers/Constructors) + scrollable entity pill selector — switch without going back
- Hero: portrait (drivers) or car livery (constructors), price + change, season pts, pts/$M, ownership bar
- Points breakdown bars — 6 categories vs league average overlay
- vs League Average comparison bars — pts/$M, total pts, ownership, price
- 4 mini charts: Price Evolution, Points/Race, Pts/$M Efficiency, Ownership
- **Tap portrait/car OR "View Full Historical Data" button → `historical.html?entity=Name`**
- **Landscape mode:** Sidebar with entity selector + stats, right panel with full-width historical charts for all races (scales to 24 races as season progresses)
- **Data:** `picks.json` (deduplicated), `prices.json`, `breakdowns.json`

### `historical.html` — Historical Performance Hub
- Accessed from `driver.html`: `historical.html?entity=Ferrari`
- Full entity selector (switch entities without going back)
- Hero card with forecast cards: Next Race Price, Stability Score, Ownership tier
- Season performance chart (points bars + price line overlay)
- Round-by-round table: all rounds, completed races show pts/price/change/pts$M, future rounds show price forecast with points as "—". Best race highlighted green, worst red, sprint rounds amber, cancelled struck through.
- Market Competitiveness: top 10 same-type entities ranked by pts/$M — click any to switch
- **Landscape mode:** Left sidebar with entity stats, right panel with full scrollable table + charts. Designed for 24 races.
- **Data:** `picks.json` (deduplicated), `prices.json`, `races.json`

### `stats.html` — Season Stats
- Best result of the season, best use of each card, standings swings, transfer activity, prize money
- **Data:** `results.json`, `picks.json`, `breakdowns.json`

---

## Data rules

### Race points and penalties
- `race_points` is the game's final GD score and **already includes penalties**.
- Penalties come from the game itself (same values as the F1 Fantasy team screen):
  - extra transfers: `usersubsleft < 0` × `extrasubscost` (e.g. `-10 × 1 = -10`)
  - inactive driver: `inactive_driver_penality_points` (e.g. `-25`)
- `results.json` stores them for display: `extra_transfers`, `transfer_penalty`,
  `inactive_penalty`, `penalty_points`.
- **Live:** the game only publishes the inactive-driver penalty after the race. Until then
  the bot uses the game's inactive flag on the driver and deducts per session started
  (qualifying −5, sprint −10, race −20). Final GD points always take priority once the race is done.

### Season points and rank
The API only gives the season total *as of now*, so each race saves a snapshot. When the
pipeline builds the data, a snapshot is kept if it equals the previous total + this race's
points (±10 pts for steward changes); otherwise it was taken too late (after the next race
weekend started) and is rebuilt from race points — the log shows
`⚠ R6: season snapshot taken late`. The running total always matches the official league table.

### Prizes
Per race, by race rank: 1st $19.25, 2nd $11.55, 3rd $7.70. Ties share the pooled prizes.

### Budget fields
- `new_budget` — budget going into the next race (displayed)
- `current_budget` — budget at the start of the race
- `budget_remaining` — unspent cash after picks
- Delta shown = `new_budget - current_budget`

### Cards
Stored as `"Final Fix (GD19)"`; several cards in one race are separated by `;`.
The pages show the card name and the round (`R19`).

### Image paths
All images referenced as relative paths from the root:
- `images/portraits/{slug}.webp` — driver portraits (360×360 WebP; resize new ones the same way)
- `images/cars/{slug}.webp` — constructor car liveries
- `images/circuits/round-XX_{venue}.svg` — circuit layouts

Circuit images use venue-keyword mapping (not round number) since the image set follows a different season order than the 2026 calendar. Australian GP has no circuit image — handled gracefully.

### Known image placeholders
- `images/portraits/arvid_lindblad.webp` — placeholder, replace with real portrait
- `images/portraits/carlos_sainz.webp` — placeholder, replace with real portrait

---

## Scoring Categories (breakdowns.json)

| Category | Type |
|---|---|
| Qualifying Position | Points |
| Both Driver Q2 / Q3, One Driver Q3 | Bonus |
| QF not classified | Penalty |
| Race Position | Points |
| Race position gained / lost | Bonus/Penalty |
| Race not classified (DNF) | Penalty (-20) |
| Race Fastest lap | Bonus |
| Driver Of Day | Bonus |
| Sprint Position | Points |
| Sprint position gained / lost | Bonus/Penalty |
| Sprint Not Classified | Penalty (-10) |
| Sprint Fastest lap | Bonus |
| race overtake bonus | Bonus |
| Sprint overtake bonus | Bonus |
| Fastest Pitstop | Bonus |
| 2nd Fastest Pitstop | Bonus |

---

## Tech stack
- Pure HTML / CSS / JavaScript — no framework, no build step
- Charts: hand-rolled SVG (no chart library)
- Fonts: Space Grotesk + Inter (Google Fonts); icons: Material Symbols Outlined
- Data pipeline: Python 3.11 on GitHub Actions
- Hosting: GitHub Pages
