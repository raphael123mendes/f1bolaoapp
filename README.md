# F1 Bolão — Fantasy League Web App

A mobile-first F1 fantasy league dashboard built as a static site hosted on GitHub Pages. No backend, no build tools — pure HTML, CSS and JavaScript with all data embedded directly in each page.

---

## Live Site

```
https://raphael123mendes.github.io/f1bolaoapp/
```

---

## File Structure

```
/
├── index.html              ← League Standings (main page, loads by default)
├── results.html            ← Race Results & Market Impact
├── market.html             ← Market Overview (prices, efficiency)
├── race.html               ← Race Analysis & Strategy Portal
├── team.html               ← Player Team Detail Dashboard
├── driver.html             ← Driver & Constructor Analytics
├── historical.html         ← Historical Performance Hub
│
├── results.json            ← Season results with race_prize column (generated)
├── races.json              ← 2026 calendar with sprint/cancelled flags
├── picks.json              ← Player picks per race (drivers + constructors)
├── prices.json             ← Market prices per entity per race
├── breakdowns.json         ← Points breakdown by scoring category
│
└── images/
    ├── portraits/          ← Driver portrait PNGs (22 drivers)
    ├── cars/               ← Constructor car livery WEBPs (11 teams)
    ├── circuits/           ← Circuit layout SVGs (24 circuits)
    └── logos/              ← Constructor logos (reserved for future use)
```

---

## Pages

### `index.html` — League Standings
- P1 spotlight hero with current leader
- Expandable accordion rows for all 11 players
- Per-player: season points, budget (`new_budget`), avg/race, rank trend, cards used, prize total
- Trophy Race chart — rank progression across all rounds
- Footer stats: league average points, highest budget
- **Data:** `results.json`

### `results.html` — Race Results
- Round switcher — all 24 rounds, completed races clickable, cancelled rounds struck through
- Race header with circuit SVG watermark, sprint weekend badge
- Player table sorted by `race_rank` (not season rank) showing race points, season total, budget, budget delta
- Weekend MVPs: Top Driver, Top Constructor, Market Riser, Market Faller
- Driver portraits and constructor car images with graceful fallback
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

### `team.html` — Player Team Detail
- Accessed from Standings via "View Full Team Details" button: `team.html?user=Liberio+Junior`
- Profile header with tier badge (Podium / Mid / Backmarker), season rank, team name
- 5 metric cards: Total Points, Avg/Race, Season Rank, Budget, Prize Money
- 4 mini charts: Budget Evolution, Points per Race, Prize Earnings, Season Rank trend
- Strategy Cards: all 6 cards shown (used = green tick, unused = grey cross). GD suffix stripped.
- Driver lineup: top 2 as hero cards (portrait, greyscale → colour on hover), bottom 3 compact
- NEW badge on picks that changed from previous race
- Constructor cards with car livery images
- **Click any driver/constructor → navigates to `driver.html?entity=Name`**
- **Landscape mode:** Sidebar with stats, right panel with rank history table (season rank, race rank, delta, points, budget, prize per round)
- **Data:** `picks.json`, `results.json`, `prices.json`

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

---

## Navigation Flow

```
index.html (Standings)
  └─ View Full Team Details → team.html?user=Name
       └─ Click driver/constructor → driver.html?entity=Name
            └─ Tap image or History button → historical.html?entity=Name

market.html
  └─ Click any row → driver.html?entity=Name

results.html → standalone (round switcher built in)
race.html    → standalone (round switcher + player selector built in)
```

Bottom navigation bar (Standings / Results / Market / Race) is present on all pages.

---

## Data Notes

### `results.json` — added `race_prize` column
Prize money is pre-calculated per race with tie-splitting logic:
- 1st: $19.25 / 2nd: $11.55 / 3rd: $7.70
- Ties split the pooled prizes equally (e.g. two P1s share $30.80)
- Non-podium players get `race_prize: 0.0`

### Budget fields
- `new_budget` — the player's budget going into the next race (used for display)
- `current_budget` — budget at start of current race
- `budget_remaining` — unspent cash after picks
- Delta displayed = `new_budget - current_budget`

### Pick deduplication
`picks.json` contains one row per user per pick per race. For market/historical analytics, picks are deduplicated to one row per entity per race (same `pick_points_gd` value across all users who picked the same entity).

### Image paths
All images referenced as relative paths from the root:
- `images/portraits/{slug}.png` — driver portraits
- `images/cars/{slug}.webp` — constructor car liveries
- `images/circuits/round-XX_{venue}.svg` — circuit layouts

Circuit images use venue-keyword mapping (not round number) since the image set follows a different season order than the 2026 calendar. Australian GP has no circuit image — handled gracefully.

### Known image placeholders
- `images/portraits/arvid_lindblad.png` — placeholder (2KB), replace with real portrait
- `images/portraits/carlos_sainz.png` — placeholder (2KB), replace with real portrait

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

## Strategy Cards

| Card | Type |
|---|---|
| No Negative | GD1 |
| Autopilot | GD1 |
| Extra DRS | GD2 |
| Limitless | GD2 |
| Final Fix | GD3 |
| Wildcard | GD3 |

GD suffix stripped in all UI displays.

---

## Season Calendar Notes (races.json)

- Round 4 (Bahrain) and Round 5 (Saudi Arabia) cancelled — Middle East conflict
- Round 16 (Spanish GP) at new Madrid Street Circuit (Madring) — subject to FIA homologation
- Round 17 (Azerbaijan) on Saturday — accommodates Remembrance Day
- Sprint weekends: Rounds 2, 6, 7, 11, 14, 18

---

## Updating Data Each Race

After each race weekend, update the following files:

1. **`results.json`** — add new entries for the new race_number, then re-run the prize calculation script to regenerate `race_prize` values
2. **`picks.json`** — add picks for the new race
3. **`prices.json`** — add new price entries (race_number + next_race_number)
4. **`breakdowns.json`** — add breakdown rows for the new race

All HTML files embed the JSON data directly — after updating the JSON files, the embedded data in each HTML file also needs to be refreshed. The simplest approach is to re-run the data embedding step for each page.

---

## Tech Stack

- Pure HTML / CSS / JavaScript — no framework, no build step
- Fonts: Space Grotesk (headlines) + Inter (body) via Google Fonts
- Icons: Material Symbols Outlined
- Charts: hand-rolled SVG (no chart library dependency)
- Hosting: GitHub Pages (static)
