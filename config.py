"""
config.py - Central configuration loader for Jarvis bot.
Reads environment variables from .env file and exposes them as typed constants.
"""

import os
from dotenv import load_dotenv

# Load .env file from the same directory as this script
load_dotenv()


def _require(key: str) -> str:
    """Fetch a required env var; raise at startup if missing."""
    value = os.getenv(key)
    if not value:
        raise EnvironmentError(
            f"Required environment variable '{key}' is not set. "
            f"Copy .env.example to .env and fill in your values."
        )
    return value


# ── Required secrets ──────────────────────────────────────────────────────────
DISCORD_TOKEN: str = _require("DISCORD_TOKEN")
BALLDONTLIE_API_KEY: str = _require("BALLDONTLIE_API_KEY")
ODDS_API_KEY: str = _require("ODDS_API_KEY")

# ── Optional sport API keys ────────────────────────────────────────────────────
# Pandascore (esports): https://pandascore.co/ — free tier 1000 req/hr
PANDASCORE_API_KEY: str = os.getenv("PANDASCORE_API_KEY", "")

# OCR.Space — used for vision (reading slip photos). Free key: https://ocr.space/ocrapi
OCR_SPACE_API_KEY: str = os.getenv("OCR_SPACE_API_KEY", "")

# ── Optional / tunable ────────────────────────────────────────────────────────
# If set, slash commands sync instantly to a single test guild instead of
# propagating globally (which can take up to an hour).
DISCORD_GUILD_ID: int | None = (
    int(os.getenv("DISCORD_GUILD_ID"))
    if os.getenv("DISCORD_GUILD_ID")
    else None
)

# ── API base URLs ─────────────────────────────────────────────────────────────
BALLDONTLIE_BASE = "https://api.balldontlie.io/v1"
NHL_BASE         = "https://api-web.nhle.com/v1"
ODDS_BASE        = "https://api.the-odds-api.com/v4"
ESPN_NFL_BASE    = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
ESPN_SOCCER_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
ESPN_TENNIS_BASE = "https://site.api.espn.com/apis/site/v2/sports/tennis"
PANDASCORE_BASE  = "https://api.pandascore.co"

# Supported sports
SUPPORTED_SPORTS = ["NBA", "NFL", "NHL", "SOCCER", "TENNIS", "ESPORTS"]

# ── Analysis thresholds ───────────────────────────────────────────────────────
# Confidence buckets used across commands
LOCK_THRESHOLD = 75        # >= 75 → LOCK
SHARP_THRESHOLD = 60       # 60–74 → SHARP
LEAN_THRESHOLD = 50        # 50–59 → LEAN
SKIP_THRESHOLD = 0         # < 50 → SKIP

# Degen picks: confidence 55-70 but big odds
DEGEN_CONF_MIN = 55
DEGEN_CONF_MAX = 70
DEGEN_ODDS_MIN = 150       # American odds minimum (e.g. +150)

# Default number of legs when the user doesn't specify
DEFAULT_PARLAY_LEGS = 3

# HTTP timeout in seconds for all API calls
HTTP_TIMEOUT = 15

# How many recent game logs to pull by default
DEFAULT_GAME_LOG_COUNT = 20
