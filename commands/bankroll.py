"""
commands/bankroll.py - Bankroll management slash commands.

/bankroll set [amount]    - Set/reset starting bankroll
/bankroll status          - Show P&L, win rate, ROI
/bankroll bet [pick_id] [stake] - Log a bet with Kelly Criterion guidance
"""

import logging
from typing import Optional, Literal

import discord
from discord import app_commands
from discord.ext import commands

from database.db import (
    get_user_bankroll,
    create_bankroll,
    update_bankroll,
    log_bet,
    settle_bet,
    get_recent_picks,
)
from utils.embeds import bankroll_embed, error_embed, info_embed, COLOR_INFO

log = logging.getLogger(__name__)


# ── Kelly Criterion ───────────────────────────────────────────────────────────

def kelly_stake(
    bankroll: float,
    win_probability: float,
    american_odds: str,
    fraction: float = 0.5,
) -> float:
    """
    Calculate the Kelly Criterion recommended stake.

    Parameters
    ----------
    bankroll         : Current bankroll
    win_probability  : Estimated win probability (0.0 – 1.0)
    american_odds    : e.g. "-110", "+150"
    fraction         : Kelly fraction (0.5 = half-Kelly, safer)

    Returns
    -------
    Recommended stake in dollars (rounded to cents).
    """
    try:
        odds_val = float(str(american_odds).strip().replace("+", ""))
        if odds_val > 0:
            decimal_odds = odds_val / 100 + 1
        else:
            decimal_odds = 100 / abs(odds_val) + 1
    except (ValueError, ZeroDivisionError):
        decimal_odds = 1.909  # -110 default

    b = decimal_odds - 1  # net odds (profit per unit stake)
    p = win_probability
    q = 1 - p

    kelly = (b * p - q) / b
    kelly = max(0.0, kelly)  # never bet negative

    recommended = round(bankroll * kelly * fraction, 2)
    # Cap at 20% of bankroll as a safety guard
    cap = round(bankroll * 0.20, 2)
    return min(recommended, cap)


def confidence_to_probability(confidence: int) -> float:
    """
    Convert a Jarvis confidence score (0–100) to an estimated win probability.
    Applies a mild regression toward 50% to avoid overconfidence.
    """
    raw = confidence / 100
    # Blend toward 0.5 (55% weight on raw, 45% toward 0.5)
    blended = raw * 0.55 + 0.5 * 0.45
    return round(max(0.05, min(0.95, blended)), 4)


class BankrollCog(commands.Cog, name="Bankroll"):
    """Bankroll management commands."""

    def __init__(self, bot) -> None:
        self.bot = bot

    bankroll_group = app_commands.Group(
        name="bankroll",
        description="Bankroll management — set budget, track bets, view P&L.",
    )

    # ── /bankroll set ──────────────────────────────────────────────────────────

    @bankroll_group.command(
        name="set",
        description="Set (or reset) your starting bankroll.",
    )
    @app_commands.describe(amount="Your starting bankroll in dollars (e.g. 500)")
    async def bankroll_set(
        self,
        interaction: discord.Interaction,
        amount: float,
    ) -> None:
        if amount <= 0:
            await interaction.response.send_message(
                embed=error_embed("Invalid amount", "Bankroll must be greater than $0."),
                ephemeral=True,
            )
            return

        guild_id = str(interaction.guild_id or "DM")
        user_id  = str(interaction.user.id)

        await create_bankroll(guild_id, user_id, amount)

        embed = discord.Embed(
            title="💰 Bankroll Set",
            description=f"Your bankroll has been set to **${amount:,.2f}**.",
            color=COLOR_INFO,
        )
        embed.add_field(
            name="Kelly Criterion",
            value=(
                "Bet sizing will be automatically suggested using the half-Kelly "
                "formula to protect your bankroll."
            ),
            inline=False,
        )
        embed.set_footer(text="Use /bankroll status to view your stats.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /bankroll status ───────────────────────────────────────────────────────

    @bankroll_group.command(
        name="status",
        description="View your current bankroll, P&L, win rate, and ROI.",
    )
    async def bankroll_status(
        self,
        interaction: discord.Interaction,
    ) -> None:
        guild_id = str(interaction.guild_id or "DM")
        user_id  = str(interaction.user.id)

        br = await get_user_bankroll(guild_id, user_id)

        if not br:
            await interaction.response.send_message(
                embed=info_embed(
                    "No bankroll set",
                    "Use `/bankroll set [amount]` to get started.",
                ),
                ephemeral=True,
            )
            return

        embed = bankroll_embed(br)
        embed.description = f"Bankroll for {interaction.user.mention}"

        # Suggest Kelly stake for a -110 bet at current balance
        prob = 0.55  # avg user win rate assumption
        suggested = kelly_stake(br["current_balance"], prob, "-110", fraction=0.5)
        embed.add_field(
            name="Suggested Next Bet (½-Kelly @ -110)",
            value=f"**${suggested:.2f}**",
            inline=False,
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /bankroll bet ──────────────────────────────────────────────────────────

    @bankroll_group.command(
        name="bet",
        description="Log a bet and get Kelly Criterion stake sizing advice.",
    )
    @app_commands.describe(
        pick_id="Pick ID from /picks or /pick (optional)",
        stake="Amount you want to bet in dollars",
        odds="American odds for the bet (e.g. -110 or +150)",
    )
    async def bankroll_bet(
        self,
        interaction: discord.Interaction,
        stake: float,
        pick_id: Optional[int] = None,
        odds: Optional[str] = "-110",
    ) -> None:
        guild_id = str(interaction.guild_id or "DM")
        user_id  = str(interaction.user.id)

        br = await get_user_bankroll(guild_id, user_id)
        if not br:
            await interaction.response.send_message(
                embed=info_embed(
                    "No bankroll set",
                    "Use `/bankroll set [amount]` first.",
                ),
                ephemeral=True,
            )
            return

        current = br["current_balance"]

        if stake <= 0:
            await interaction.response.send_message(
                embed=error_embed("Invalid stake", "Stake must be > $0."),
                ephemeral=True,
            )
            return

        if stake > current:
            await interaction.response.send_message(
                embed=error_embed(
                    "Insufficient balance",
                    f"Your balance is **${current:,.2f}** but you tried to bet **${stake:,.2f}**.",
                ),
                ephemeral=True,
            )
            return

        # Get pick confidence for Kelly calculation
        confidence = 60  # default
        if pick_id:
            picks = await get_recent_picks()
            for p in picks:
                if p.get("id") == pick_id:
                    confidence = p.get("confidence", 60)
                    break

        prob = confidence_to_probability(confidence)
        kelly = kelly_stake(current, prob, odds or "-110", fraction=0.5)

        # Log the bet
        bet_id = await log_bet(
            guild_id=guild_id,
            user_id=user_id,
            pick_id=pick_id,
            stake=stake,
            odds=odds,
        )

        # Compare to Kelly recommendation
        if stake > kelly * 1.5:
            kelly_warning = f"\n⚠️ Your stake is **{stake/kelly:.1f}x** the half-Kelly recommendation."
        elif stake < kelly * 0.5:
            kelly_warning = f"\nℹ️ You could bet up to **${kelly:.2f}** (half-Kelly)."
        else:
            kelly_warning = "\n✅ Stake is in line with half-Kelly sizing."

        embed = discord.Embed(
            title="🎲 Bet Logged",
            description=(
                f"Bet **${stake:,.2f}** at **{odds}** logged successfully.\n"
                f"Bet ID: **#{bet_id}**"
                f"{kelly_warning}"
            ),
            color=COLOR_INFO,
        )
        embed.add_field(name="Balance Before", value=f"${current:,.2f}", inline=True)
        embed.add_field(name="At Risk", value=f"${stake:,.2f}", inline=True)
        embed.add_field(name="Half-Kelly Suggestion", value=f"${kelly:.2f}", inline=True)
        embed.add_field(
            name="Win Probability (est.)",
            value=f"{prob:.0%}  (based on {confidence}% confidence)",
            inline=False,
        )
        embed.set_footer(
            text="Use /bankroll settle to mark this bet as won or lost."
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /bankroll settle ───────────────────────────────────────────────────────

    @bankroll_group.command(
        name="settle",
        description="Mark a bet as won or lost and update your balance.",
    )
    @app_commands.describe(
        bet_id="Bet ID (from /bankroll bet)",
        result="win or loss",
        stake="Amount you staked",
        odds="American odds (e.g. -110 or +150)",
    )
    async def bankroll_settle(
        self,
        interaction: discord.Interaction,
        bet_id: int,
        result: Literal["win", "loss"],
        stake: float,
        odds: Optional[str] = "-110",
    ) -> None:
        guild_id = str(interaction.guild_id or "DM")
        user_id  = str(interaction.user.id)

        br = await get_user_bankroll(guild_id, user_id)
        if not br:
            await interaction.response.send_message(
                embed=error_embed("No bankroll", "Set a bankroll first with `/bankroll set`."),
                ephemeral=True,
            )
            return

        # Calculate P&L
        try:
            odds_val = float(str(odds or "-110").replace("+", ""))
            if odds_val > 0:
                profit = round(stake * odds_val / 100, 2)
            else:
                profit = round(stake * 100 / abs(odds_val), 2)
        except (ValueError, ZeroDivisionError):
            profit = round(stake * 100 / 110, 2)  # default -110

        won = result == "win"
        pnl = profit if won else -stake

        await settle_bet(bet_id, result, pnl)
        await update_bankroll(guild_id, user_id, stake, pnl, won)

        # Fetch updated bankroll
        updated_br = await get_user_bankroll(guild_id, user_id)

        status = "✅ Win!" if won else "❌ Loss"
        color = 0x00C851 if won else 0xFF3547

        embed = discord.Embed(
            title=f"Bet #{bet_id} Settled — {status}",
            description=f"P&L: **{'+'if pnl>=0 else ''}{pnl:.2f}**",
            color=color,
        )
        if updated_br:
            embed.add_field(
                name="New Balance",
                value=f"${updated_br['current_balance']:,.2f}",
                inline=True,
            )
            wins = updated_br.get("wins", 0)
            losses = updated_br.get("losses", 0)
            wr = wins / max(wins + losses, 1)
            embed.add_field(
                name="Overall Record",
                value=f"{wins}W - {losses}L ({wr:.0%})",
                inline=True,
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot) -> None:
    await bot.add_cog(BankrollCog(bot))
