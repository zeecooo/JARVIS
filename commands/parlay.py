"""
commands/parlay.py - /parlay slash command.

/parlay [type] [legs]
  Types:
    lock      - All high-confidence picks (≥75)
    sharp     - Sharp-money picks (≥65)
    h2h       - H2H history-weighted picks
    plus_money - Positive expected value / +odds picks

  legs - Number of legs (2–6, default: 3)
"""

import math
import logging
from typing import Optional, Literal

import discord
from discord import app_commands
from discord.ext import commands

from analysis.engine import PickResult
from commands.picks import _fetch_todays_game_props
from utils.embeds import parlay_embed, error_embed, info_embed
import config

log = logging.getLogger(__name__)


# ── Odds math ─────────────────────────────────────────────────────────────────

def american_to_decimal(american: str) -> float:
    """Convert American odds string to decimal odds multiplier."""
    try:
        val = float(str(american).strip().replace("+", ""))
        if val > 0:
            return round(val / 100 + 1, 4)
        else:
            return round(100 / abs(val) + 1, 4)
    except (ValueError, ZeroDivisionError):
        return 1.909  # default to -110


def decimal_to_american(decimal: float) -> str:
    """Convert decimal odds back to American odds string."""
    if decimal >= 2.0:
        return f"+{int(round((decimal - 1) * 100))}"
    else:
        return f"-{int(round(100 / (decimal - 1)))}"


def combine_parlay_odds(legs: list[PickResult]) -> tuple[str, float]:
    """
    Calculate combined parlay odds and projected $100 payout.

    Returns: (american_odds_str, payout_for_100)
    """
    combined_decimal = 1.0
    for leg in legs:
        dec = american_to_decimal(leg.odds or "-110")
        combined_decimal *= dec

    payout = round((combined_decimal - 1) * 100, 2) + 100
    return decimal_to_american(combined_decimal), payout


def _risk_assessment(picks: list[PickResult], parlay_type: str) -> str:
    """Generate a human-readable risk note for the parlay."""
    min_conf = min(p.confidence for p in picks) if picks else 0
    avg_conf = sum(p.confidence for p in picks) / max(len(picks), 1)
    b2b_count = sum(1 for p in picks if p.back_to_back)
    injury_count = sum(1 for p in picks if p.injury_flag)

    notes = []

    if avg_conf >= 75:
        notes.append("✅ Strong parlay — high average confidence.")
    elif avg_conf >= 65:
        notes.append("🎯 Solid parlay with acceptable risk.")
    elif avg_conf >= 55:
        notes.append("👀 Moderate risk — some questionable legs.")
    else:
        notes.append("⚠️ High-risk parlay — several weak legs.")

    if min_conf < 55:
        notes.append(f"⚠️ Weakest leg at only {min_conf}% confidence.")

    if b2b_count:
        notes.append(f"⚠️ {b2b_count} leg(s) involve back-to-back players.")

    if injury_count:
        notes.append(f"🚑 {injury_count} leg(s) have active injury flags.")

    if len(picks) >= 5:
        notes.append("🎰 5+ leg parlay — very difficult to hit.")

    return "\n".join(notes)


# ── Parlay selection strategies ───────────────────────────────────────────────

def _select_lock(picks: list[PickResult], n: int) -> list[PickResult]:
    """All picks with confidence ≥ LOCK_THRESHOLD, best first."""
    filtered = [p for p in picks if p.confidence >= config.LOCK_THRESHOLD]
    return sorted(filtered, key=lambda p: p.confidence, reverse=True)[:n]


def _select_sharp(picks: list[PickResult], n: int) -> list[PickResult]:
    """
    Sharp-money strategy: picks ≥ SHARP_THRESHOLD with good H2H and defense rating.
    """
    filtered = [p for p in picks if p.confidence >= config.SHARP_THRESHOLD]

    def _sharp_score(p: PickResult) -> float:
        score = p.confidence
        if p.h2h_rate >= 0.7:
            score += 5
        if p.defense_rating in ("poor", "terrible"):
            score += 5
        return score

    return sorted(filtered, key=_sharp_score, reverse=True)[:n]


def _select_h2h(picks: list[PickResult], n: int) -> list[PickResult]:
    """H2H-focused: best historical matchup rate."""
    eligible = [p for p in picks if p.h2h_games >= 3]
    if not eligible:
        eligible = picks  # Fallback to all picks if no H2H data
    return sorted(eligible, key=lambda p: (p.h2h_rate, p.confidence), reverse=True)[:n]


def _select_plus_money(picks: list[PickResult], n: int) -> list[PickResult]:
    """
    Plus-money (+EV): picks with positive American odds and confidence ≥ 55.
    """
    filtered = []
    for p in picks:
        try:
            odds_val = float(str(p.odds or "-110").replace("+", ""))
            if odds_val > 0 and p.confidence >= 55:
                filtered.append(p)
        except ValueError:
            pass

    if not filtered:
        # Fallback: least-juiced picks
        filtered = sorted(picks, key=lambda p: float(str(p.odds or "-110").replace("+", "")), reverse=True)

    return sorted(filtered, key=lambda p: p.confidence, reverse=True)[:n]


_STRATEGIES = {
    "lock":       _select_lock,
    "sharp":      _select_sharp,
    "h2h":        _select_h2h,
    "plus_money": _select_plus_money,
}


class ParlayCog(commands.Cog, name="Parlay"):
    """Parlay builder commands."""

    def __init__(self, bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="parlay",
        description=(
            "Build an optimal parlay from today's top picks. "
            "Types: lock, sharp, h2h, plus_money."
        ),
    )
    @app_commands.describe(
        type="Parlay strategy: lock (safest), sharp, h2h, or plus_money (+EV)",
        legs="Number of legs (2–6, default: 3)",
        sport="Sport to build from (default: NBA)",
        stake="Stake amount in dollars (default: 100)",
    )
    async def parlay(
        self,
        interaction: discord.Interaction,
        type: Optional[Literal["lock", "sharp", "h2h", "plus_money"]] = "lock",
        legs: Optional[int] = 3,
        sport: Optional[Literal["NBA", "NHL"]] = "NBA",
        stake: Optional[float] = 100.0,
    ) -> None:
        await interaction.response.defer(thinking=True)

        # Validate legs
        legs = max(2, min(6, legs or 3))
        stake = max(1.0, stake or 100.0)
        parlay_type = type or "lock"

        # Fetch scored picks
        try:
            all_picks = await _fetch_todays_game_props(
                self.bot,
                sport=sport,
                min_confidence=50,
                limit=20,
            )
        except Exception as exc:
            log.error("/parlay fetch error: %s", exc)
            await interaction.followup.send(
                embed=error_embed("Parlay unavailable", str(exc))
            )
            return

        if not all_picks:
            await interaction.followup.send(
                embed=info_embed(
                    "No picks",
                    f"No {sport} picks available to build a parlay right now.",
                )
            )
            return

        # Apply strategy
        strategy_fn = _STRATEGIES.get(parlay_type, _select_lock)
        selected = strategy_fn(all_picks, legs)

        if len(selected) < 2:
            await interaction.followup.send(
                embed=info_embed(
                    "Not enough legs",
                    (
                        f"Could not find {legs} qualifying {sport} picks for a "
                        f"**{parlay_type}** parlay.\n"
                        f"Try a different type or a lower leg count."
                    ),
                )
            )
            return

        # Calculate combined odds and projected payout
        combined_odds, projected_payout = combine_parlay_odds(selected)
        risk_note = _risk_assessment(selected, parlay_type)

        # Scale payout to actual stake
        payout_scaled = round(
            (american_to_decimal(combined_odds) - 1) * stake + stake, 2
        )

        embed = parlay_embed(
            parlay_type=parlay_type,
            picks=selected,
            combined_odds=combined_odds,
            projected_payout=payout_scaled,
            stake=stake,
            risk_note=risk_note,
        )
        await interaction.followup.send(embed=embed)


async def setup(bot) -> None:
    await bot.add_cog(ParlayCog(bot))
