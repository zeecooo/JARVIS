"""
database/db.py - SQLite schema and helper functions using aiosqlite.

Tables
------
picks           - Individual pick records with confidence, result, odds
bankroll        - Per-user per-guild bankroll tracking
analyzed_slips  - History of /analyze slip submissions
"""

import aiosqlite
import os
from datetime import datetime, date
from typing import Optional

# Database file lives next to the project root
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "jarvis.db")


# ── Schema DDL ────────────────────────────────────────────────────────────────

_CREATE_PICKS = """
CREATE TABLE IF NOT EXISTS picks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    date          TEXT    NOT NULL,          -- ISO date string YYYY-MM-DD
    player        TEXT    NOT NULL,
    team          TEXT    NOT NULL,
    opponent      TEXT    NOT NULL,
    prop_type     TEXT    NOT NULL,          -- e.g. PTS, REB, SOG
    line          REAL    NOT NULL,
    confidence    INTEGER NOT NULL,          -- 0-100
    recommendation TEXT   NOT NULL,          -- LOCK/SHARP/LEAN/SKIP
    result        TEXT    NOT NULL DEFAULT 'pending',  -- hit/miss/pending
    odds          TEXT,                      -- American odds string e.g. "-115"
    sport         TEXT    NOT NULL DEFAULT 'NBA',
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""

_CREATE_BANKROLL = """
CREATE TABLE IF NOT EXISTS bankroll (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        TEXT    NOT NULL,
    user_id         TEXT    NOT NULL,
    budget          REAL    NOT NULL,        -- starting bankroll
    current_balance REAL    NOT NULL,        -- running balance
    total_wagered   REAL    NOT NULL DEFAULT 0,
    total_won       REAL    NOT NULL DEFAULT 0,
    wins            INTEGER NOT NULL DEFAULT 0,
    losses          INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(guild_id, user_id)
);
"""

_CREATE_ANALYZED_SLIPS = """
CREATE TABLE IF NOT EXISTS analyzed_slips (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT    NOT NULL,
    slip_text   TEXT    NOT NULL,
    score       REAL    NOT NULL,            -- average confidence across legs
    legs        INTEGER NOT NULL DEFAULT 1,
    result      TEXT    NOT NULL DEFAULT 'pending',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""

_CREATE_BET_LOG = """
CREATE TABLE IF NOT EXISTS bet_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    TEXT    NOT NULL,
    user_id     TEXT    NOT NULL,
    pick_id     INTEGER REFERENCES picks(id),
    stake       REAL    NOT NULL,
    odds        TEXT,
    result      TEXT    NOT NULL DEFAULT 'pending',  -- hit/miss/pending
    pnl         REAL    NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""


# ── Lifecycle ─────────────────────────────────────────────────────────────────

async def init_db() -> None:
    """Create all tables if they don't already exist."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(_CREATE_PICKS)
        await db.execute(_CREATE_BANKROLL)
        await db.execute(_CREATE_ANALYZED_SLIPS)
        await db.execute(_CREATE_BET_LOG)
        await db.commit()


# ── Picks helpers ─────────────────────────────────────────────────────────────

async def save_pick(
    player: str,
    team: str,
    opponent: str,
    prop_type: str,
    line: float,
    confidence: int,
    recommendation: str,
    odds: Optional[str] = None,
    sport: str = "NBA",
    pick_date: Optional[str] = None,
) -> int:
    """Insert a new pick and return its row ID."""
    today = pick_date or date.today().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO picks
                (date, player, team, opponent, prop_type, line,
                 confidence, recommendation, result, odds, sport)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (today, player, team, opponent, prop_type, line,
             confidence, recommendation, odds, sport),
        )
        await db.commit()
        return cursor.lastrowid


async def update_pick_result(pick_id: int, result: str) -> None:
    """Mark a pick as 'hit', 'miss', or 'pending'."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE picks SET result = ? WHERE id = ?",
            (result, pick_id),
        )
        await db.commit()


async def get_recent_picks(
    sport: Optional[str] = None,
    pick_date: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """Return recent picks, optionally filtered by sport and/or date."""
    today = pick_date or date.today().isoformat()
    query = "SELECT * FROM picks WHERE date = ?"
    params: list = [today]

    if sport:
        query += " AND sport = ?"
        params.append(sport)

    query += " ORDER BY confidence DESC LIMIT ?"
    params.append(limit)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def get_picks_by_date_range(
    start_date: str,
    end_date: str,
    sport: Optional[str] = None,
) -> list[dict]:
    """Fetch picks between two ISO date strings (inclusive)."""
    query = "SELECT * FROM picks WHERE date BETWEEN ? AND ?"
    params: list = [start_date, end_date]
    if sport:
        query += " AND sport = ?"
        params.append(sport)
    query += " ORDER BY date DESC, confidence DESC"

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


# ── Bankroll helpers ──────────────────────────────────────────────────────────

async def get_user_bankroll(guild_id: str, user_id: str) -> Optional[dict]:
    """Return the bankroll row for a user or None if not set."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM bankroll WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def create_bankroll(guild_id: str, user_id: str, budget: float) -> None:
    """Create or reset a bankroll for a user."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO bankroll (guild_id, user_id, budget, current_balance)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                budget = excluded.budget,
                current_balance = excluded.budget,
                total_wagered = 0,
                total_won = 0,
                wins = 0,
                losses = 0,
                updated_at = datetime('now')
            """,
            (guild_id, user_id, budget, budget),
        )
        await db.commit()


async def update_bankroll(
    guild_id: str,
    user_id: str,
    stake: float,
    pnl: float,
    won: bool,
) -> None:
    """Adjust balance after a bet settles."""
    async with aiosqlite.connect(DB_PATH) as db:
        if won:
            await db.execute(
                """
                UPDATE bankroll SET
                    current_balance = current_balance + ?,
                    total_wagered   = total_wagered + ?,
                    total_won       = total_won + ?,
                    wins            = wins + 1,
                    updated_at      = datetime('now')
                WHERE guild_id = ? AND user_id = ?
                """,
                (pnl, stake, pnl, guild_id, user_id),
            )
        else:
            await db.execute(
                """
                UPDATE bankroll SET
                    current_balance = current_balance - ?,
                    total_wagered   = total_wagered + ?,
                    losses          = losses + 1,
                    updated_at      = datetime('now')
                WHERE guild_id = ? AND user_id = ?
                """,
                (stake, stake, guild_id, user_id),
            )
        await db.commit()


# ── Bet log helpers ───────────────────────────────────────────────────────────

async def log_bet(
    guild_id: str,
    user_id: str,
    pick_id: Optional[int],
    stake: float,
    odds: Optional[str] = None,
) -> int:
    """Record a placed bet and return its ID."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO bet_log (guild_id, user_id, pick_id, stake, odds)
            VALUES (?, ?, ?, ?, ?)
            """,
            (guild_id, user_id, pick_id, stake, odds),
        )
        await db.commit()
        return cursor.lastrowid


async def settle_bet(bet_id: int, result: str, pnl: float) -> None:
    """Update a bet log entry with its final result and P&L."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE bet_log SET result = ?, pnl = ? WHERE id = ?",
            (result, pnl, bet_id),
        )
        await db.commit()


# ── Analyzed slips helpers ────────────────────────────────────────────────────

async def save_analyzed_slip(
    user_id: str,
    slip_text: str,
    score: float,
    legs: int = 1,
) -> int:
    """Persist an analyzed slip and return its ID."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO analyzed_slips (user_id, slip_text, score, legs)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, slip_text, score, legs),
        )
        await db.commit()
        return cursor.lastrowid


async def get_user_slips(user_id: str, limit: int = 10) -> list[dict]:
    """Return recent analyzed slips for a user."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM analyzed_slips WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
