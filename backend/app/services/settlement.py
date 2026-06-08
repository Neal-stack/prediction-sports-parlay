from __future__ import annotations

import logging
import re
from typing import Literal, Optional, Tuple

logger = logging.getLogger(__name__)

LegResult = Literal["win", "loss", "push"]

SPREAD_RE = re.compile(r"^(.+?)\s+([+-]?\d+(?:\.\d+)?)$")
TOTAL_RE = re.compile(r"^(over|under)\s+(\d+(?:\.\d+)?)$", re.I)


def team_match(a: str, b: str) -> bool:
    a = a.lower().strip()
    b = b.lower().strip()
    return b in a or a in b or a.split()[-1] in b or b.split()[-1] in a


def parse_matchup(matchup: str) -> Tuple[str, str]:
    """Return (home_team, away_team) from 'Away @ Home'."""
    parts = matchup.split(" @ ")
    if len(parts) == 2:
        return parts[1].strip(), parts[0].strip()
    return "", ""


def parse_spread(selection: str) -> Tuple[str, float]:
    m = SPREAD_RE.match(selection.strip())
    if not m:
        raise ValueError(f"Cannot parse spread selection: {selection}")
    return m.group(1).strip(), float(m.group(2))


def parse_total(selection: str) -> Tuple[str, float]:
    m = TOTAL_RE.match(selection.strip())
    if not m:
        raise ValueError(f"Cannot parse total selection: {selection}")
    return m.group(1).lower(), float(m.group(2))


def grade_moneyline(
    selection: str,
    home_team: str,
    away_team: str,
    home_score: int,
    away_score: int,
) -> LegResult:
    if home_score == away_score:
        return "push"
    winner = home_team if home_score > away_score else away_team
    return "win" if team_match(selection, winner) else "loss"


def grade_spread(
    selection: str,
    home_team: str,
    away_team: str,
    home_score: int,
    away_score: int,
) -> LegResult:
    team, line = parse_spread(selection)
    if team_match(team, home_team):
        adjusted = home_score + line
        other = away_score
    elif team_match(team, away_team):
        adjusted = away_score + line
        other = home_score
    else:
        logger.warning("Spread team '%s' did not match matchup", team)
        raise ValueError(f"Team not in matchup: {team}")

    if adjusted > other:
        return "win"
    if adjusted < other:
        return "loss"
    return "push"


def grade_total(
    selection: str,
    home_score: int,
    away_score: int,
) -> LegResult:
    side, line = parse_total(selection)
    combined = home_score + away_score
    if side == "over":
        if combined > line:
            return "win"
        if combined < line:
            return "loss"
    else:
        if combined < line:
            return "win"
        if combined > line:
            return "loss"
    return "push"


def grade_leg(
    *,
    market: str,
    selection: str,
    matchup: str,
    home_score: int,
    away_score: int,
) -> LegResult:
    home_team, away_team = parse_matchup(matchup)
    if not home_team or not away_team:
        raise ValueError(f"Invalid matchup: {matchup}")

    if market == "moneyline":
        return grade_moneyline(selection, home_team, away_team, home_score, away_score)
    if market == "spread":
        return grade_spread(selection, home_team, away_team, home_score, away_score)
    if market == "total":
        return grade_total(selection, home_score, away_score)
    raise ValueError(f"Unknown market: {market}")
