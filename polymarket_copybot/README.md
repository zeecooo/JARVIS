# Polymarket Copy Trading Bot

This bot monitors a target trader's open positions on Polymarket via their public wallet address. Each polling cycle it fetches the target's current positions, computes a win/loss streak from recent activity, evaluates each new position through a hard latency gate and a Claude AI risk manager, and—if approved—places a proportional market order via the Polymarket CLOB API, logging every decision with a clear reason.

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` and fill in every value:

| Variable | Description |
|---|---|
| `PRIVATE_KEY` | Polygon wallet private key (dedicated bot wallet) |
| `FUNDER_ADDRESS` | Same wallet's public address |
| `API_KEY` | Polymarket CLOB API key |
| `API_SECRET` | Polymarket CLOB API secret |
| `API_PASSPHRASE` | Polymarket CLOB API passphrase |
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `TARGET_WALLET` | Trader wallet address to copy |
| `MAX_POSITION_USDC` | Max USDC per position (e.g. `50`) |
| `COPY_FRACTION` | Fraction of target size to copy (e.g. `0.10` = 10%) |
| `POLL_INTERVAL` | Seconds between cycles (e.g. `30`) |
| `MAX_ENTRY_AGE_MINUTES` | Skip positions older than this (e.g. `10`) |

Polymarket API keys are generated at: https://polymarket.com/settings/api-keys

### 3. Run the bot

```bash
python bot.py
```

---

## How it works

Every `POLL_INTERVAL` seconds the bot:

1. Fetches the target's open positions and your own positions
2. Computes a win/loss streak from the target's last 20 closed trades
3. For each new target position:
   - Applies the hard latency gate (skips if older than `MAX_ENTRY_AGE_MINUTES`)
   - Fetches orderbook spread and market metadata
   - Asks Claude AI (`claude-sonnet-4-20250514`) whether to COPY or DEVIATE
   - Places a market order if approved
4. Logs when the target closes a position you copied

---

## Deviation rules

Claude deviates (does **not** copy) if **any** of the following are true:

| # | Rule |
|---|---|
| 1 | Spread > 5% |
| 2 | Price slipped > 15% against copy direction since target entered |
| 3 | Already hold > $30 in this market |
| 4 | Market resolves in < 24 hours AND edge is unclear |
| 5 | Target win rate < 40% over last 20 trades |
| 6 | Liquidity < $5,000 |
| 7 | Entry older than `MAX_ENTRY_AGE_MINUTES` |

---

## Finding traders to copy

Browse top traders on the Polymarket leaderboard:
https://polymarket.com/leaderboard

Look for traders with:
- High profit over 30+ resolved markets
- Consistent win rate above 55%
- Active in liquid markets

---

## Safety disclaimer

> **This bot is experimental software. Always paper-trade first before using real funds. Use a dedicated wallet with only the capital you can afford to lose. Never use your main wallet. This is not financial advice.**

- Start with `MAX_POSITION_USDC=5` and `COPY_FRACTION=0.05` until you trust the setup
- Monitor logs closely during the first few hours
- Polymarket prediction markets carry significant risk of total loss
