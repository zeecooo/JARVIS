"""
utils/embeds.py - Discord embed builder helpers for Jarvis.

All public functions return discord.Embed objects ready to send.

Colour scheme:
  Green  (#00C851) - LOCK   ≥75
  Yellow (#FFB300) - SHARP  60–74
  Orange (#FF6D00) - LEAN   50–59
  Red    (#FF3547) - SKIP   <50
"""

import discord
from datetime import date
from typing import Optional

from analysis.engine import PickResult

# ── Colour constants ──────────────────────────────────────────────────────────
COLOR_LOCK   = 0x00C851
COLOR_SHARP  = 0xFFB300
COLOR_LEAN   = 0xFF6D00
COLOR_SKIP   = 0xFF3547
COLOR_INFO   = 0x2196F3
COLOR_PARLAY = 0x9C27B0
COLOR_RECAP  = 0x607D8B

# ── Recommendation badges ─────────────────────────────────────────────────────
BADGES = {
    "LOCK":  "🔒 LOCK",
    "SHARP": "🎯 SHARP",
    "LEAN":  "👀 LEAN",
    "SKIP":  "❌ SKIP",
}

TREND_ARROWS = {
    "up":   "📈",
    "down": "📉",
    "flat": "➡️",
    # NHL / alternate keys
    "improving":  "📈",
    "declining":  "📉",
    "stable":     "➡️",
}


def _pick_color(confidence: int) -> int:
    if confidence >= 75:
        return COLOR_LOCK
    if confidence >= 60:
        return COLOR_SHARP
    if confidence >= 50:
        return COLOR_LEAN
    return COLOR_SKIP


def confidence_bar(score: int, length: int = 10) -> str:
    """
    Build a visual confidence bar using block unicode characters.

    Example: confidence_bar(70) → '███████░░░ 70%'
    """
    filled = round((score / 100) * length)
    bar = "█" * filled + "░" * (length - filled)
    return f"`{bar}` {score}%"


def hit_rate_bar(rate: float, label: str = "") -> str:
    """
    One-line hit-rate bar.

    Example: hit_rate_bar(0.8, "L5") → 'L5 ████████░░ 80%'
    """
    pct = int(rate * 100)
    filled = round(rate * 10)
    bar = "█" * filled + "░" * (10 - filled)
    prefix = f"{label} " if label else ""
    return f"{prefix}`{bar}` {pct}%"


def _defense_emoji(rating: str) -> str:
    return {
        "elite":    "🟢",
        "good":     "🔵",
        "average":  "🟡",
        "poor":     "🟠",
        "terrible": "🔴",
    }.get(rating, "⚪")


# ── Single pick embed ─────────────────────────────────────────────────────────

def pick_embed(pick: PickResult, pick_id: Optional[int] = None) -> discord.Embed:
    """
    Full analysis embed for a single PickResult.

    Shows:
    - Header with player, prop, and recommendation badge
    - Confidence bar
    - L5/L10/L20 hit rate bars
    - Defense rank
    - H2H rate
    - Home/away split
    - Minutes trend
    - Back-to-back warning
    - Injury flag
    - Bullet-point reasoning
    """
    badge = BADGES.get(pick.recommendation, "👀 LEAN")
    color = _pick_color(pick.confidence)

    title = (
        f"{badge}  {pick.player_name}  "
        f"{'Over' if pick.direction == 'over' else 'Under'} {pick.line} {pick.prop_type}"
    )
    desc = f"**{pick.sport}**  |  {pick.team} vs {pick.opponent}  |  {'🏠 Home' if pick.is_home else '✈️ Away'}"

    embed = discord.Embed(title=title, description=desc, color=color)
    embed.set_footer(text=f"Jarvis Analytics  •  {date.today().isoformat()}")

    # Confidence
    embed.add_field(
        name="Confidence",
        value=confidence_bar(pick.confidence),
        inline=False,
    )

    # Hit rates
    hr = pick.hit_rates
    if hr:
        hr_lines = "\n".join([
            hit_rate_bar(hr.get("l5", 0.0), "L5 "),
            hit_rate_bar(hr.get("l10", 0.0), "L10"),
            hit_rate_bar(hr.get("l20", 0.0), "L20"),
        ])
        trend_emoji = TREND_ARROWS.get(hr.get("trend", "flat"), "➡️")
        hr_lines += f"\nTrend {trend_emoji}  |  Avg: **{hr.get('avg', 0):.1f}**"
        embed.add_field(name="Hit Rates", value=hr_lines, inline=True)

    # Defense rank
    if pick.defense_label:
        def_emoji = _defense_emoji(pick.defense_rating)
        embed.add_field(
            name="Opp. Defense",
            value=f"{def_emoji} **{pick.defense_label}**\n({pick.defense_rating.title()})",
            inline=True,
        )

    # H2H
    if pick.h2h_games > 0:
        embed.add_field(
            name="H2H vs Opp",
            value=f"{hit_rate_bar(pick.h2h_rate)} over {pick.h2h_games} games",
            inline=True,
        )

    # Home / away
    loc_label = "Home Rate" if pick.is_home else "Away Rate"
    embed.add_field(
        name=loc_label,
        value=hit_rate_bar(pick.home_away_rate),
        inline=True,
    )

    # Minutes / TOI
    trend_emoji = TREND_ARROWS.get(pick.min_trend, "➡️")
    embed.add_field(
        name="Minutes Trend",
        value=f"{trend_emoji} L5 avg **{pick.avg_minutes:.1f}** min",
        inline=True,
    )

    # Flags
    flags = []
    if pick.back_to_back:
        flags.append("⚠️ Back-to-back tonight")
    if pick.injury_flag:
        flags.append(f"🚑 Injury: {pick.injury_note}")
    if flags:
        embed.add_field(name="⚠️ Flags", value="\n".join(flags), inline=False)

    # Reasoning
    if pick.reasoning:
        reasoning_text = "\n".join(pick.reasoning[:8])  # cap at 8 bullets
        embed.add_field(name="Analysis", value=reasoning_text, inline=False)

    # Odds + pick ID footer
    footer_parts = []
    if pick.odds:
        footer_parts.append(f"Odds: {pick.odds}")
    if pick_id:
        footer_parts.append(f"Pick ID: #{pick_id}")
    if footer_parts:
        embed.add_field(name="\u200b", value=" | ".join(footer_parts), inline=False)

    return embed


# ── Slip embed (multi-pick analyze) ──────────────────────────────────────────

def slip_embed(
    picks: list[PickResult],
    slip_text: str,
    overall_score: float,
    slip_id: Optional[int] = None,
) -> discord.Embed:
    """
    Multi-leg slip analysis embed.
    Shows a summary line for each leg plus a combined confidence score.
    """
    color = _pick_color(int(overall_score))
    embed = discord.Embed(
        title=f"📋 Slip Analysis  |  Overall Score: {int(overall_score)}%",
        description=f"`{slip_text[:200]}`",
        color=color,
    )
    embed.set_footer(text=f"Jarvis Analytics  •  {date.today().isoformat()}")

    # Add each leg
    for i, pick in enumerate(picks, 1):
        badge = BADGES.get(pick.recommendation, "👀")
        hr = pick.hit_rates
        l5  = f"{hr.get('l5', 0):.0%}" if hr else "N/A"
        l10 = f"{hr.get('l10', 0):.0%}" if hr else "N/A"

        flags = ""
        if pick.back_to_back:
            flags += " ⚠️"
        if pick.injury_flag:
            flags += " 🚑"

        value_lines = [
            f"{badge}{flags}",
            f"Confidence: **{pick.confidence}%**",
            f"L5/L10: {l5} / {l10}",
        ]
        if pick.defense_label:
            def_emoji = _defense_emoji(pick.defense_rating)
            value_lines.append(f"Defense: {def_emoji} {pick.defense_label}")

        embed.add_field(
            name=f"Leg {i}: {pick.player_name} {'O' if pick.direction == 'over' else 'U'}{pick.line} {pick.prop_type}",
            value="\n".join(value_lines),
            inline=True,
        )

    # Weak link warning
    min_pick = min(picks, key=lambda p: p.confidence) if picks else None
    if min_pick and min_pick.confidence < 50:
        embed.add_field(
            name="⚠️ Weak Link",
            value=(
                f"{min_pick.player_name} {min_pick.prop_type} has only "
                f"**{min_pick.confidence}%** confidence — consider removing."
            ),
            inline=False,
        )

    if slip_id:
        embed.add_field(name="\u200b", value=f"Slip ID: #{slip_id}", inline=False)

    return embed


# ── Parlay embed ──────────────────────────────────────────────────────────────

def parlay_embed(
    parlay_type: str,
    picks: list[PickResult],
    combined_odds: str,
    projected_payout: float,
    stake: float = 100.0,
    risk_note: str = "",
) -> discord.Embed:
    """
    Parlay builder embed showing legs, combined odds, and risk assessment.
    """
    embed = discord.Embed(
        title=f"🎰 {parlay_type.upper()} Parlay  ({len(picks)} legs)",
        description=(
            f"Combined odds: **{combined_odds}**\n"
            f"${stake:.0f} → **${projected_payout:.2f}** projected payout"
        ),
        color=COLOR_PARLAY,
    )
    embed.set_footer(text=f"Jarvis Analytics  •  {date.today().isoformat()}")

    # Legs
    for i, pick in enumerate(picks, 1):
        hr = pick.hit_rates
        badge = BADGES.get(pick.recommendation, "👀")
        value = (
            f"{badge}  Conf: **{pick.confidence}%**\n"
            f"L5: {hr.get('l5', 0):.0%}  L10: {hr.get('l10', 0):.0%}"
        )
        if pick.back_to_back:
            value += "  ⚠️ B2B"
        embed.add_field(
            name=f"Leg {i}: {pick.player_name} {'O' if pick.direction=='over' else 'U'}{pick.line} {pick.prop_type}",
            value=value,
            inline=False,
        )

    # Risk note
    if risk_note:
        embed.add_field(name="Risk Assessment", value=risk_note, inline=False)

    return embed


# ── Recap embed ───────────────────────────────────────────────────────────────

def recap_embed(
    picks: list[dict],
    recap_date: str,
    pnl: float = 0.0,
) -> discord.Embed:
    """
    End-of-day recap embed showing all picks with hit/miss/pending status.
    """
    hits    = [p for p in picks if p.get("result") == "hit"]
    misses  = [p for p in picks if p.get("result") == "miss"]
    pending = [p for p in picks if p.get("result") == "pending"]

    win_rate = len(hits) / max(len(hits) + len(misses), 1)
    color = COLOR_LOCK if pnl >= 0 else COLOR_SKIP

    embed = discord.Embed(
        title=f"📅 Daily Recap — {recap_date}",
        description=(
            f"✅ Hits: **{len(hits)}**  "
            f"❌ Misses: **{len(misses)}**  "
            f"⏳ Pending: **{len(pending)}**\n"
            f"Win Rate: **{win_rate:.0%}**  |  P&L: **{'+'if pnl>=0 else ''}{pnl:.2f}**"
        ),
        color=color,
    )
    embed.set_footer(text="Jarvis Analytics")

    # Hit section
    if hits:
        lines = [
            f"✅ {p['player']} {p['prop_type']} {p['line']} (conf {p['confidence']}%)"
            for p in hits[:10]
        ]
        embed.add_field(name="Hits", value="\n".join(lines), inline=False)

    # Misses section
    if misses:
        lines = [
            f"❌ {p['player']} {p['prop_type']} {p['line']} (conf {p['confidence']}%)"
            for p in misses[:10]
        ]
        embed.add_field(name="Misses", value="\n".join(lines), inline=False)

    # Pending section
    if pending:
        lines = [
            f"⏳ {p['player']} {p['prop_type']} {p['line']}"
            for p in pending[:5]
        ]
        embed.add_field(name="Pending", value="\n".join(lines), inline=False)

    return embed


# ── Bankroll embed ────────────────────────────────────────────────────────────

def bankroll_embed(bankroll: dict) -> discord.Embed:
    """Status embed for a user's bankroll."""
    bal   = bankroll.get("current_balance", 0)
    start = bankroll.get("budget", 0)
    pnl   = bal - start
    wins  = bankroll.get("wins", 0)
    losses = bankroll.get("losses", 0)
    total  = wins + losses
    win_rate = wins / max(total, 1)
    wagered = bankroll.get("total_wagered", 0)
    roi = (pnl / max(wagered, 1)) * 100

    color = COLOR_LOCK if pnl >= 0 else COLOR_SKIP

    embed = discord.Embed(
        title="💰 Bankroll Status",
        color=color,
    )
    embed.add_field(name="Starting Bankroll", value=f"${start:,.2f}", inline=True)
    embed.add_field(name="Current Balance", value=f"${bal:,.2f}", inline=True)
    embed.add_field(name="P&L", value=f"{'+'if pnl>=0 else ''}{pnl:,.2f}", inline=True)
    embed.add_field(name="Record", value=f"{wins}W - {losses}L  ({win_rate:.0%})", inline=True)
    embed.add_field(name="Total Wagered", value=f"${wagered:,.2f}", inline=True)
    embed.add_field(name="ROI", value=f"{roi:+.1f}%", inline=True)
    embed.set_footer(text="Jarvis Analytics")
    return embed


# ── Locks / degen list embed ──────────────────────────────────────────────────

def picks_list_embed(
    picks: list[PickResult],
    title: str,
    description: str = "",
    color: int = COLOR_INFO,
) -> discord.Embed:
    """
    Compact list embed for /picks, /locks, /degen commands.
    Shows up to 10 picks with one-liner summaries.
    """
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_footer(text=f"Jarvis Analytics  •  {date.today().isoformat()}")

    if not picks:
        embed.add_field(name="No picks found", value="Check back later.", inline=False)
        return embed

    for i, pick in enumerate(picks[:10], 1):
        badge = BADGES.get(pick.recommendation, "👀")
        hr = pick.hit_rates
        trend = TREND_ARROWS.get(hr.get("trend", "flat"), "➡️") if hr else "➡️"
        flags = ("⚠️" if pick.back_to_back else "") + ("🚑" if pick.injury_flag else "")
        name = (
            f"{i}. {pick.player_name}  "
            f"{'O' if pick.direction=='over' else 'U'}{pick.line} {pick.prop_type}"
        )
        value = (
            f"{badge}  **{pick.confidence}%**  {trend}{flags}\n"
            f"L5: {hr.get('l5',0):.0%}  L10: {hr.get('l10',0):.0%}  "
            f"H2H: {pick.h2h_rate:.0%}  |  {pick.team} vs {pick.opponent}"
        )
        embed.add_field(name=name, value=value, inline=False)

    return embed


# ── Alt-lines embed ───────────────────────────────────────────────────────────

def altlines_embed(
    player_name: str,
    prop_type: str,
    original_line: float,
    suggestions: list[dict],
    hit_rates: dict,
) -> discord.Embed:
    """Alt-line suggestions embed."""
    embed = discord.Embed(
        title=f"📏 Alt Lines — {player_name} {prop_type}",
        description=f"Original line: **{original_line}**",
        color=COLOR_INFO,
    )
    embed.set_footer(text="Jarvis Analytics")

    # Original stats
    hr = hit_rates
    embed.add_field(
        name="Current Line Stats",
        value=(
            f"L5: {hr.get('l5',0):.0%}  L10: {hr.get('l10',0):.0%}  "
            f"L20: {hr.get('l20',0):.0%}\nAvg: **{hr.get('avg',0):.1f}**"
        ),
        inline=False,
    )

    # Suggestions
    for alt in suggestions[:5]:
        est = alt.get("estimated_hit_rate", 0)
        embed.add_field(
            name=f"Alt Line: {alt.get('line', '?')}",
            value=(
                f"Est. hit rate: **{est:.0%}**\n"
                f"Approx odds: {alt.get('odds_adjustment', 'N/A')}\n"
                f"{alt.get('note', '')}"
            ),
            inline=True,
        )

    return embed


# ── First basket embed ────────────────────────────────────────────────────────

def firstbasket_embed(picks: list[dict]) -> discord.Embed:
    """First basket scorer props embed."""
    embed = discord.Embed(
        title="🏀 First Basket Props",
        description=f"Tonight's first-basket scorer picks — {date.today().isoformat()}",
        color=COLOR_INFO,
    )
    embed.set_footer(text="Jarvis Analytics  •  Based on usage & opening tip tendencies")

    if not picks:
        embed.add_field(
            name="No picks available",
            value="Could not fetch first basket props for tonight.",
            inline=False,
        )
        return embed

    for p in picks[:8]:
        embed.add_field(
            name=p.get("player", "Unknown"),
            value=(
                f"Team: {p.get('team', '?')} vs {p.get('opponent', '?')}\n"
                f"Odds: **{p.get('odds', 'N/A')}**\n"
                f"Usage rank: {p.get('usage_rank', 'N/A')}  "
                f"L5 pts: {p.get('avg_pts', 'N/A')}"
            ),
            inline=True,
        )

    return embed


# ── Pick of the Day embed ─────────────────────────────────────────────────────

def potd_embed(pick: PickResult, pick_id: Optional[int] = None) -> discord.Embed:
    """
    Special Pick of the Day embed — same content as pick_embed but with
    a gold header and star branding to distinguish it visually.
    """
    badge = BADGES.get(pick.recommendation, "👀 LEAN")
    color = 0xFFD700  # Gold

    title = (
        f"⭐ PICK OF THE DAY  |  {badge}\n"
        f"{pick.player_name}  "
        f"{'Over' if pick.direction == 'over' else 'Under'} {pick.line} {pick.prop_type}"
    )
    desc = (
        f"**{pick.sport}**  |  {pick.team} vs {pick.opponent}  |  "
        f"{'🏠 Home' if pick.is_home else '✈️ Away'}"
    )

    embed = discord.Embed(title=title, description=desc, color=color)
    embed.set_footer(text=f"Jarvis Analytics · POTD · {date.today().isoformat()}")

    embed.add_field(
        name="Confidence",
        value=confidence_bar(pick.confidence),
        inline=False,
    )

    hr = pick.hit_rates
    if hr:
        hr_lines = "\n".join([
            hit_rate_bar(hr.get("l5", 0.0), "L5 "),
            hit_rate_bar(hr.get("l10", 0.0), "L10"),
            hit_rate_bar(hr.get("l20", 0.0), "L20"),
        ])
        trend_emoji = TREND_ARROWS.get(hr.get("trend", "flat"), "➡️")
        hr_lines += f"\nTrend {trend_emoji}  |  Avg: **{hr.get('avg', 0):.1f}**"
        embed.add_field(name="Hit Rates", value=hr_lines, inline=True)

    if pick.defense_label:
        def_emoji = _defense_emoji(pick.defense_rating)
        embed.add_field(
            name="Opp. Defense",
            value=f"{def_emoji} **{pick.defense_label}**\n({pick.defense_rating.title()})",
            inline=True,
        )

    if pick.h2h_games > 0:
        embed.add_field(
            name="H2H vs Opp",
            value=f"{hit_rate_bar(pick.h2h_rate)} over {pick.h2h_games} games",
            inline=True,
        )

    loc_label = "Home Rate" if pick.is_home else "Away Rate"
    embed.add_field(name=loc_label, value=hit_rate_bar(pick.home_away_rate), inline=True)

    trend_emoji = TREND_ARROWS.get(pick.min_trend, "➡️")
    embed.add_field(
        name="Minutes Trend",
        value=f"{trend_emoji} L5 avg **{pick.avg_minutes:.1f}** min",
        inline=True,
    )

    flags = []
    if pick.back_to_back:
        flags.append("⚠️ Back-to-back tonight")
    if pick.injury_flag:
        flags.append(f"🚑 Injury: {pick.injury_note}")
    if pick.confidence < 55:
        flags.append("⚠️ Limited data available — verify before placing")
    if flags:
        embed.add_field(name="⚠️ Flags", value="\n".join(flags), inline=False)

    if pick.reasoning:
        embed.add_field(name="Analysis", value="\n".join(pick.reasoning[:8]), inline=False)

    footer_parts = []
    if pick.odds:
        footer_parts.append(f"Odds: {pick.odds}")
    if pick_id:
        footer_parts.append(f"Pick ID: #{pick_id}")
    if footer_parts:
        embed.add_field(name="\u200b", value=" | ".join(footer_parts), inline=False)

    return embed


# ── Error embed ───────────────────────────────────────────────────────────────

def error_embed(title: str, description: str) -> discord.Embed:
    """Generic error embed."""
    return discord.Embed(title=f"❌ {title}", description=description, color=COLOR_SKIP)


def info_embed(title: str, description: str) -> discord.Embed:
    """Generic info embed."""
    return discord.Embed(title=f"ℹ️ {title}", description=description, color=COLOR_INFO)
