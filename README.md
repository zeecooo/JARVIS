# Jarvis — Discord Sports Analytics Bot

A production-quality Discord bot for NBA and NHL prop betting analytics.
Jarvis scores player props using hit rates, defense rankings, H2H history,
home/away splits, usage trends, back-to-back flags, and injury context.

---

## Features

| Command | Description |
|---|---|
| `/picks [sport] [min_confidence]` | Daily top NBA/NHL props scored and ranked |
| `/pick [sport]` | Pick of the day — single highest-confidence play with full analysis |
| `/parlay [type] [legs]` | Parlay builder: lock, sharp, h2h, plus_money |
| `/analyze [slip]` | Full slip analyzer for NBA + NHL props in one command |
| `/altlines [player] [prop] [line]` | Alt line suggestions with hit-rate tradeoffs |
| `/degen [sport]` | High-odds plays backed by data (55–70% confidence, +150 or better) |
| `/locks [sport]` | Safe high-confidence plays (≥75%) only |
| `/firstbasket` | First basket scorer props for tonight's games |
| `/bankroll set [amount]` | Set your starting bankroll |
| `/bankroll status` | View P&L, win rate, ROI, and Kelly suggestion |
| `/bankroll bet [pick_id] [stake]` | Log a bet with Kelly Criterion sizing |
| `/bankroll settle` | Settle a bet as won or lost |
| `/recap [date]` | End-of-day P&L recap |
| `/recap_week` | 7-day rolling performance summary |

---

## How Every Pick Is Scored

Each pick runs through the following checks and contributes to a 0–100 confidence score:

1. **L5 / L10 / L20 hit rates** vs the line (weighted 45/35/20)
2. **H2H history** vs the specific opponent
3. **Defense rank by position** (NBA: 1–30, NHL: 1–32)
4. **Home/away splits** for the player
5. **Usage & minutes trend** (rising minutes → confidence boost)
6. **Back-to-back flag** (penalty for fatigue)
7. **Injury context** (major penalty if flagged)

Confidence → Recommendation mapping:
- 🔒 **LOCK** — ≥75%
- 🎯 **SHARP** — 60–74%
- 👀 **LEAN** — 50–59%
- ❌ **SKIP** — <50%

---

## Setup

### 1. Clone and install dependencies

```bash
cd /path/to/jarvis
pip install -r requirements.txt
```

### 2. Configure environment variables

```bash
cp .env.example .env
# Edit .env and fill in your API keys
```

Required keys:

| Variable | Source |
|---|---|
| `DISCORD_TOKEN` | [discord.com/developers](https://discord.com/developers/applications) |
| `BALLDONTLIE_API_KEY` | [balldontlie.io](https://www.balldontlie.io/) |
| `ODDS_API_KEY` | [the-odds-api.com](https://the-odds-api.com/) |
| `DISCORD_GUILD_ID` | Optional — paste your server ID for instant slash-command sync |

### 3. Run the bot

```bash
python bot.py
```

---

## File Structure

```
jarvis/
├── bot.py                  Main entry point, cog loader, DB init
├── config.py               Env var loader and constants
├── requirements.txt
├── .env.example
├── README.md
├── database/
│   ├── __init__.py
│   └── db.py               SQLite schema + async helpers (aiosqlite)
├── data/
│   ├── __init__.py
│   ├── nba_client.py       BallDontLie v2 API wrapper
│   ├── nhl_client.py       NHL official API wrapper
│   └── odds_client.py      The Odds API v4 wrapper
├── analysis/
│   ├── __init__.py
│   ├── engine.py           Central scoring engine
│   ├── nba_analysis.py     NBA prop parsing, usage, B2B
│   ├── nhl_analysis.py     NHL prop parsing, TOI, B2B
│   ├── hit_rates.py        L5/L10/L20 hit rate calculator
│   └── defense.py          Team defense rankings
├── commands/
│   ├── __init__.py
│   ├── picks.py            /picks, /pick
│   ├── analyze.py          /analyze
│   ├── parlay.py           /parlay
│   ├── bankroll.py         /bankroll *
│   ├── recap.py            /recap, /recap_week
│   └── locks.py            /locks, /degen, /firstbasket, /altlines
└── utils/
    ├── __init__.py
    ├── embeds.py           Discord embed builders
    └── player_lookup.py    Name resolution, fuzzy matching, nicknames
```

---

## API Notes

### BallDontLie (NBA)
- Free tier: 60 req/min
- Used for: player lookup, game logs, team stats, injuries, schedule
- Docs: https://www.balldontlie.io/

### NHL API (free, no key required)
- Base: `https://api-web.nhle.com/v1`
- Used for: player search, game logs, standings, schedule
- Docs: https://github.com/Zmalski/NHL-API-Reference

### The Odds API
- Used for: live player prop lines, event IDs
- Docs: https://the-odds-api.com/lev4/
- Note: calls cost API quota — bot batches requests per game

---

## Database

The bot uses a local SQLite file (`jarvis.db`) with four tables:

- `picks` — all scored picks with confidence, recommendation, and result
- `bankroll` — per-user per-guild bankroll tracking
- `analyzed_slips` — history of /analyze submissions
- `bet_log` — individual bet records with P&L

---

## Extending

To add a new sport or data source:
1. Create a client in `data/`
2. Add field mappings to `analysis/hit_rates.py`
3. Add defense rank logic to `analysis/defense.py`
4. Wire it into `analysis/engine.py`
5. Add commands to `commands/`
6. Register the cog in `bot.py`
