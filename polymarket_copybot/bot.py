"""
Polymarket Copy Trading Bot
Monitors a target trader's open positions and copies them via the Polymarket CLOB API,
with Claude AI risk evaluation before each trade.
"""

import logging
import os
import time
from datetime import datetime, timezone

import anthropic
import requests
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, MarketOrderArgs

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
load_dotenv()

PRIVATE_KEY = os.getenv("PRIVATE_KEY")
FUNDER_ADDRESS = os.getenv("FUNDER_ADDRESS")
API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")
API_PASSPHRASE = os.getenv("API_PASSPHRASE")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
TARGET_WALLET = os.getenv("TARGET_WALLET")
MAX_POSITION_USDC = float(os.getenv("MAX_POSITION_USDC", "50"))
COPY_FRACTION = float(os.getenv("COPY_FRACTION", "0.10"))
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "30"))
MAX_ENTRY_AGE_MINUTES = float(os.getenv("MAX_ENTRY_AGE_MINUTES", "10"))

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------
creds = ApiCreds(
    api_key=API_KEY,
    api_secret=API_SECRET,
    api_passphrase=API_PASSPHRASE,
)
clob = ClobClient(
    host="https://clob.polymarket.com",
    chain_id=137,
    key=PRIVATE_KEY,
    creds=creds,
    signature_type=2,
    funder=FUNDER_ADDRESS,
)

anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
copied_positions: dict[str, dict] = {}  # token_id -> {side, size_usdc, entry_price, timestamp}

# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def fetch_positions(wallet: str) -> list[dict]:
    """Fetch open positions for a wallet address."""
    try:
        resp = requests.get(
            "https://data-api.polymarket.com/positions",
            params={"user": wallet},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json() or []
    except Exception as exc:
        logger.error("Failed to fetch positions for %s: %s", wallet, exc)
        return []


def fetch_activity(wallet: str, limit: int = 50) -> list[dict]:
    """Fetch recent trade activity for a wallet address."""
    try:
        resp = requests.get(
            "https://data-api.polymarket.com/activity",
            params={"user": wallet, "limit": limit, "type": "trade"},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json() or []
    except Exception as exc:
        logger.error("Failed to fetch activity for %s: %s", wallet, exc)
        return []


def fetch_market_meta(condition_id: str) -> dict:
    """Fetch market metadata from Gamma API."""
    try:
        resp = requests.get(
            "https://gamma-api.polymarket.com/markets",
            params={"condition_id": condition_id},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) and data:
            return data[0]
        if isinstance(data, dict):
            return data
        return {}
    except Exception as exc:
        logger.error("Failed to fetch market meta for %s: %s", condition_id, exc)
        return {}


def get_orderbook_spread(token_id: str) -> dict:
    """Fetch best bid/ask and spread percentage from CLOB."""
    try:
        book = clob.get_order_book(token_id)
        bids = book.bids or []
        asks = book.asks or []

        best_bid = float(bids[0].price) if bids else 0.0
        best_ask = float(asks[0].price) if asks else 1.0

        if best_ask > 0:
            spread_pct = (best_ask - best_bid) / best_ask * 100
        else:
            spread_pct = 100.0

        return {"best_bid": best_bid, "best_ask": best_ask, "spread_pct": spread_pct}
    except Exception as exc:
        logger.error("Failed to fetch orderbook for %s: %s", token_id, exc)
        return {"best_bid": 0, "best_ask": 1, "spread_pct": 100}


# ---------------------------------------------------------------------------
# Streak computation
# ---------------------------------------------------------------------------

def compute_streak(activity: list[dict]) -> dict:
    """
    Compute win/loss stats from the last 20 closed trades.
    A trade is a win if cashPnl > 0, a loss if cashPnl < 0.
    """
    trades = [a for a in activity if a.get("type") == "trade"][:20]
    wins = sum(1 for t in trades if float(t.get("cashPnl", 0)) > 0)
    losses = sum(1 for t in trades if float(t.get("cashPnl", 0)) < 0)
    total = wins + losses
    win_rate_pct = round((wins / total * 100) if total > 0 else 0.0, 1)
    return {"recent_wins": wins, "recent_losses": losses, "win_rate_pct": win_rate_pct}


# ---------------------------------------------------------------------------
# Risk evaluation
# ---------------------------------------------------------------------------

def should_copy(
    market_meta: dict,
    spread_info: dict,
    my_existing_position: dict | None,
    target_entry_price: float,
    current_price: float,
    side: str,
    streak: dict,
    entry_age_minutes: float,
) -> tuple[bool, str]:
    """
    Evaluate whether to copy a trade.
    Hard gate fires before Claude API call.
    Returns (decision: bool, reason: str).
    """
    # --- Hard latency gate ---
    if entry_age_minutes > MAX_ENTRY_AGE_MINUTES:
        return (
            False,
            f"Entry is {entry_age_minutes:.1f} min old — latency window exceeded",
        )

    # --- Build Claude prompt ---
    question = market_meta.get("question", "Unknown")
    end_date = market_meta.get("endDate", "Unknown")
    liquidity = float(market_meta.get("liquidity", 0))

    best_bid = spread_info["best_bid"]
    best_ask = spread_info["best_ask"]
    spread_pct = spread_info["spread_pct"]

    if target_entry_price > 0:
        if side.upper() == "YES":
            price_slip_pct = (current_price - target_entry_price) / target_entry_price * 100
        else:
            price_slip_pct = (target_entry_price - current_price) / target_entry_price * 100
    else:
        price_slip_pct = 0.0

    wins = streak["recent_wins"]
    losses = streak["recent_losses"]
    win_rate = streak["win_rate_pct"]

    prompt = f"""You are a Polymarket risk manager for a copy-trading bot.
=== Market ===
Question      : {question}
End date      : {end_date}
Liquidity     : ${liquidity} USDC
Best bid/ask  : {best_bid} / {best_ask}
Spread        : {spread_pct:.2f}%
=== Trade Signal ===
Side          : {side}
Target entry  : {target_entry_price}
Current price : {current_price}
Price slip    : {price_slip_pct:.2f}% since target entered
Entry age     : {entry_age_minutes:.1f} minutes ago
=== Our Exposure ===
Already in this market: {my_existing_position or "None"}
=== Target Trader Streak (last 20 closed trades) ===
Wins: {wins} | Losses: {losses} | Win rate: {win_rate}%
=== Deviation Rules ===
DEVIATE if ANY of these are true:
1. Spread > 5%
2. Price slipped > 15% against copy direction since target entered
3. Already hold > $30 in this market
4. Market resolves in < 24 hours AND edge is unclear
5. Target win rate < 40% over last 20 trades
6. Liquidity < $5,000
7. Entry > {MAX_ENTRY_AGE_MINUTES} minutes old
Reply with exactly:
DECISION: COPY or DEVIATE
REASON: one concise sentence"""

    try:
        message = anthropic_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        response_text = message.content[0].text.strip()
    except Exception as exc:
        logger.error("Claude API error: %s", exc)
        return (False, f"Claude API error: {exc}")

    # Parse response
    decision = False
    reason = "No reason provided"

    for line in response_text.splitlines():
        if line.startswith("DECISION:"):
            decision = "COPY" in line.upper()
        elif line.startswith("REASON:"):
            reason = line.split("REASON:", 1)[1].strip()

    return (decision, reason)


# ---------------------------------------------------------------------------
# Order execution
# ---------------------------------------------------------------------------

def execute_copy(token_id: str, side: str, target_size_usdc: float, current_price: float) -> None:
    """Place a market order copying the target's trade."""
    size_usdc = min(target_size_usdc * COPY_FRACTION, MAX_POSITION_USDC)

    try:
        order_args = MarketOrderArgs(token_id=token_id, amount=size_usdc)
        resp = clob.create_and_post_market_order(order_args)
        logger.info("✅ Order placed: %s", resp)
        copied_positions[token_id] = {
            "side": side,
            "size_usdc": size_usdc,
            "entry_price": current_price,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        logger.error("❌ Order failed: %s", exc)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run() -> None:
    logger.info("🤖 Bot started | Target: %s", TARGET_WALLET)

    seen_positions: set[str] = set()

    while True:
        try:
            # 1. Fetch target's open positions
            target_positions = fetch_positions(TARGET_WALLET)

            # 2. Fetch my open positions
            my_positions = fetch_positions(FUNDER_ADDRESS)
            my_position_map: dict[str, dict] = {
                p.get("conditionId", p.get("asset", "")): p for p in my_positions
            }

            # 3. Fetch target's last 50 trade activities
            activity = fetch_activity(TARGET_WALLET, limit=50)

            # 4. Compute win/loss streak from activity
            streak = compute_streak(activity)

            # Track current token IDs to detect exits
            current_token_ids: set[str] = set()

            # 5. Evaluate each target position
            for position in target_positions:
                token_id = position.get("asset") or position.get("tokenId", "")
                condition_id = position.get("conditionId", "")
                size_usdc = float(position.get("curValue", position.get("value", 0)))
                outcome = position.get("outcome", "YES").upper()
                side = "YES" if outcome in ("YES", "1") else "NO"

                # Determine entry price and timestamp
                avg_price = float(position.get("avgPrice", position.get("price", 0.5)))
                position_ts = position.get("createdAt") or position.get("startDate")

                if token_id:
                    current_token_ids.add(token_id)

                # 5a. Skip if already evaluated this run
                if token_id in seen_positions:
                    continue
                seen_positions.add(token_id)

                # 5b. Skip if already copied
                if token_id in copied_positions:
                    continue

                # 5c. Skip if size < $5 USDC
                if size_usdc < 5.0:
                    continue

                # 5d. Compute entry age in minutes
                entry_age_minutes = 0.0
                if position_ts:
                    try:
                        if isinstance(position_ts, (int, float)):
                            ts = datetime.fromtimestamp(position_ts, tz=timezone.utc)
                        else:
                            ts = datetime.fromisoformat(str(position_ts).replace("Z", "+00:00"))
                        entry_age_minutes = (
                            datetime.now(timezone.utc) - ts
                        ).total_seconds() / 60.0
                    except Exception:
                        entry_age_minutes = 0.0

                # 5e. Fetch orderbook spread
                spread_info = get_orderbook_spread(token_id)
                current_price = spread_info["best_ask"] if side == "YES" else spread_info["best_bid"]

                # 5f. Fetch market metadata
                market_meta = fetch_market_meta(condition_id) if condition_id else {}

                # 5g. Evaluate with should_copy()
                my_existing = my_position_map.get(condition_id)
                copy, reason = should_copy(
                    market_meta=market_meta,
                    spread_info=spread_info,
                    my_existing_position=my_existing,
                    target_entry_price=avg_price,
                    current_price=current_price,
                    side=side,
                    streak=streak,
                    entry_age_minutes=entry_age_minutes,
                )

                question_short = market_meta.get("question", token_id)[:60]

                # 5h/5i. Act on decision
                if copy:
                    logger.info("🟢 COPY decision | %s", question_short)
                    execute_copy(token_id, side, size_usdc, current_price)
                else:
                    logger.info("🔴 DEVIATE | %s | %s", question_short, reason)

            # 6. Detect target exits (positions we copied that target has closed)
            for token_id in list(copied_positions.keys()):
                if token_id not in current_token_ids:
                    logger.info("🚪 Target closed position %s", token_id)

            # Reset seen_positions to only current open positions each cycle
            seen_positions = current_token_ids.copy()

        except KeyboardInterrupt:
            logger.info("Stopped by user.")
            break
        except Exception as exc:
            logger.error("Main loop error: %s", exc)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()
