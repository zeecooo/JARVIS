"""
bot.py - Main entry point for Jarvis, the Discord sports analytics bot.

Loads all cogs (slash command groups), syncs the command tree on ready,
and wires up the shared database + HTTP clients.
"""

import asyncio
import logging
import sys

import discord
from discord.ext import commands

import config
from database.db import init_db

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("jarvis")

# ── Intents ───────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = False  # Not needed for slash-only bot


class Jarvis(commands.Bot):
    """
    Subclass of commands.Bot that owns the shared API clients and DB lifecycle.

    Attributes
    ----------
    nba_client     : NBAClient      – BallDontLie NBA API
    nhl_client     : NHLClient      – NHL official API
    nfl_client     : NFLClient      – ESPN NFL API
    soccer_client  : SoccerClient   – ESPN Soccer API
    tennis_client  : TennisClient   – ESPN Tennis API
    esports_client : EsportsClient  – Pandascore API
    odds_client    : OddsClient     – The Odds API (lines)
    """

    def __init__(self) -> None:
        super().__init__(
            command_prefix="!",  # Fallback prefix (not used for slash commands)
            intents=intents,
            help_command=None,
        )
        self.nba_client = None
        self.nhl_client = None
        self.nfl_client = None
        self.soccer_client = None
        self.tennis_client = None
        self.esports_client = None
        self.odds_client = None

    async def setup_hook(self) -> None:
        """
        Called by discord.py before login.  Load cogs, initialise DB, and
        optionally sync the command tree to a test guild for instant updates.
        """
        await init_db()
        log.info("Database initialised.")

        # Instantiate all sport clients
        from data.nba_client import NBAClient
        from data.nhl_client import NHLClient
        from data.nfl_client import NFLClient
        from data.soccer_client import SoccerClient
        from data.tennis_client import TennisClient
        from data.esports_client import EsportsClient
        from data.odds_client import OddsClient

        self.nba_client    = NBAClient()
        self.nhl_client    = NHLClient()
        self.nfl_client    = NFLClient()
        self.soccer_client = SoccerClient()
        self.tennis_client = TennisClient()
        self.esports_client = EsportsClient()
        self.odds_client   = OddsClient()
        log.info("All sport clients ready (NBA, NHL, NFL, Soccer, Tennis, Esports).")

        # Load command cogs
        cogs = [
            "commands.picks",
            "commands.analyze",
            "commands.parlay",
            "commands.bankroll",
            "commands.recap",
            "commands.locks",
        ]
        for cog in cogs:
            try:
                await self.load_extension(cog)
                log.info("Loaded cog: %s", cog)
            except Exception as exc:  # noqa: BLE001
                log.error("Failed to load cog %s: %s", cog, exc)

        # Sync slash commands
        if config.DISCORD_GUILD_ID:
            guild = discord.Object(id=config.DISCORD_GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            log.info("Slash commands synced to guild %s.", config.DISCORD_GUILD_ID)
        else:
            await self.tree.sync()
            log.info("Slash commands synced globally.")

    async def on_ready(self) -> None:
        log.info("Jarvis is online as %s (ID: %s)", self.user, self.user.id)
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="the lines | /picks",
            )
        )

    async def close(self) -> None:
        """Graceful shutdown: close all HTTP sessions before disconnecting."""
        for client_name in ["nba_client", "nhl_client", "nfl_client",
                            "soccer_client", "tennis_client", "esports_client", "odds_client"]:
            client = getattr(self, client_name, None)
            if client:
                try:
                    await client.close()
                except Exception:
                    pass
        await super().close()


# ── Entrypoint ────────────────────────────────────────────────────────────────

def main() -> None:
    bot = Jarvis()
    try:
        asyncio.run(bot.start(config.DISCORD_TOKEN))
    except KeyboardInterrupt:
        log.info("Shutting down Jarvis.")


if __name__ == "__main__":
    main()
