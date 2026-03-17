"""
commands/recap.py - /recap slash command.

/recap [date]
  Shows all picks from a given date (default: today).
  Reports hits, misses, pending, win rate, and P&L summary.
"""

import logging
from typing import Optional
from datetime import date, timedelta

import discord
from discord import app_commands
from discord.ext import commands

from database.db import get_recent_picks, get_picks_by_date_range, update_pick_result
from utils.embeds import recap_embed, error_embed, info_embed

log = logging.getLogger(__name__)


def _validate_date(date_str: Optional[str]) -> str:
    """
    Validate and normalise a date string.
    Accepts 'YYYY-MM-DD', 'today', 'yesterday'.
    Returns ISO date string.
    """
    if not date_str or date_str.lower() == "today":
        return date.today().isoformat()
    if date_str.lower() == "yesterday":
        return (date.today() - timedelta(days=1)).isoformat()
    try:
        parsed = date.fromisoformat(date_str)
        return parsed.isoformat()
    except ValueError:
        return date.today().isoformat()


def _calculate_pnl(picks: list[dict]) -> float:
    """
    Estimate P&L from picks assuming $100 stake per pick at listed odds.
    Pending picks are excluded.
    """
    total = 0.0
    for pick in picks:
        if pick.get("result") == "pending":
            continue
        odds_str = pick.get("odds") or "-110"
        try:
            odds_val = float(str(odds_str).replace("+", ""))
            if odds_val > 0:
                profit = 100 * odds_val / 100
            else:
                profit = 100 * 100 / abs(odds_val)
        except (ValueError, ZeroDivisionError):
            profit = 90.91  # -110 default

        if pick.get("result") == "hit":
            total += profit
        elif pick.get("result") == "miss":
            total -= 100.0

    return round(total, 2)


class RecapCog(commands.Cog, name="Recap"):
    """Daily recap command."""

    def __init__(self, bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="recap",
        description="End-of-day P&L recap. Shows all picks with results for a date.",
    )
    @app_commands.describe(
        recap_date=(
            "Date to recap: YYYY-MM-DD, 'today', or 'yesterday'. "
            "Defaults to today."
        ),
        sport="Filter by sport (optional)",
        mark_results=(
            "Attempt to auto-resolve pending picks from live scores (experimental)."
        ),
    )
    async def recap(
        self,
        interaction: discord.Interaction,
        recap_date: Optional[str] = None,
        sport: Optional[str] = None,
        mark_results: Optional[bool] = False,
    ) -> None:
        await interaction.response.defer(thinking=True)

        date_str = _validate_date(recap_date)

        try:
            picks = await get_recent_picks(
                sport=sport.upper() if sport else None,
                pick_date=date_str,
                limit=50,
            )
        except Exception as exc:
            log.error("/recap fetch error: %s", exc)
            await interaction.followup.send(
                embed=error_embed("Recap unavailable", str(exc))
            )
            return

        if not picks:
            await interaction.followup.send(
                embed=info_embed(
                    "No picks found",
                    f"No {'**' + sport.upper() + '** ' if sport else ''}picks logged for **{date_str}**.\n"
                    f"Use `/picks` to generate today's picks.",
                )
            )
            return

        # Optionally attempt to auto-resolve pending picks
        if mark_results:
            picks = await _try_resolve_pending(self.bot, picks)

        pnl = _calculate_pnl(picks)

        embed = recap_embed(picks=picks, recap_date=date_str, pnl=pnl)

        # Add per-sport breakdown if mixed
        sports_in_picks = {p.get("sport", "NBA") for p in picks}
        if len(sports_in_picks) > 1:
            for sp in sorted(sports_in_picks):
                sp_picks = [p for p in picks if p.get("sport") == sp]
                sp_hits   = sum(1 for p in sp_picks if p.get("result") == "hit")
                sp_misses = sum(1 for p in sp_picks if p.get("result") == "miss")
                embed.add_field(
                    name=f"{sp} Breakdown",
                    value=f"✅ {sp_hits}  ❌ {sp_misses}  ⏳ {len(sp_picks) - sp_hits - sp_misses}",
                    inline=True,
                )

        await interaction.followup.send(embed=embed)

    # ── /recap week ────────────────────────────────────────────────────────────

    @app_commands.command(
        name="recap_week",
        description="7-day rolling performance recap.",
    )
    async def recap_week(
        self,
        interaction: discord.Interaction,
        sport: Optional[str] = None,
    ) -> None:
        await interaction.response.defer(thinking=True)

        end   = date.today().isoformat()
        start = (date.today() - timedelta(days=6)).isoformat()

        try:
            picks = await get_picks_by_date_range(
                start_date=start,
                end_date=end,
                sport=sport.upper() if sport else None,
            )
        except Exception as exc:
            log.error("/recap_week fetch error: %s", exc)
            await interaction.followup.send(
                embed=error_embed("Weekly recap unavailable", str(exc))
            )
            return

        if not picks:
            await interaction.followup.send(
                embed=info_embed("No picks", f"No picks found in the last 7 days.")
            )
            return

        hits    = [p for p in picks if p.get("result") == "hit"]
        misses  = [p for p in picks if p.get("result") == "miss"]
        pending = [p for p in picks if p.get("result") == "pending"]
        win_rate = len(hits) / max(len(hits) + len(misses), 1)
        pnl = _calculate_pnl(picks)

        embed = discord.Embed(
            title=f"📅 7-Day Recap  ({start} → {end})",
            description=(
                f"Total picks: **{len(picks)}**  |  "
                f"✅ {len(hits)}  ❌ {len(misses)}  ⏳ {len(pending)}\n"
                f"Win Rate: **{win_rate:.0%}**  |  "
                f"Est. P&L (@ $100/pick): **{'+'if pnl>=0 else ''}{pnl:.2f}**"
            ),
            color=0x00C851 if pnl >= 0 else 0xFF3547,
        )
        embed.set_footer(text="Jarvis Analytics")

        # Top performers
        lock_hits = [p for p in hits if p.get("recommendation") == "LOCK"]
        if lock_hits:
            embed.add_field(
                name=f"LOCK hits ({len(lock_hits)})",
                value="\n".join(
                    f"✅ {p['player']} {p['prop_type']} {p['line']}"
                    for p in lock_hits[:5]
                ),
                inline=False,
            )

        await interaction.followup.send(embed=embed)


async def _try_resolve_pending(bot, picks: list[dict]) -> list[dict]:
    """
    Attempt to resolve pending picks by checking live scores.
    This is a best-effort stub — full implementation requires
    a live score API or manual resolution.
    """
    # For now, just return picks as-is.
    # A production bot would query a live score feed here.
    return picks


async def setup(bot) -> None:
    await bot.add_cog(RecapCog(bot))
