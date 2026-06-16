"""Injury impact via ESPN's free league-wide injury feed.

Replaces the old per-team API-Sports calls (one ESPN request covers a whole
league) and the flat-penalty model. Output is a *point-margin* impact per team
so it feeds directly into the power model, plus the raw player list for the
Gemini research prompt and chat. Who is out matters: position weights make a
starting QB or goalie hurt far more than a backup.
"""
from __future__ import annotations

from typing import Dict, List

from app.services import espn

# How much each injured player can swing the scoring margin, by status.
STATUS_SEVERITY = {
    "out": 1.0,
    "injured reserve": 1.0,
    "suspension": 1.0,
    "doubtful": 0.7,
    "questionable": 0.4,
    "day-to-day": 0.3,
    "probable": 0.1,
}

# Baseline points a key player at this position is worth (sport-specific).
POSITION_WEIGHT = {
    "nfl": {"QB": 7.0, "WR": 1.8, "RB": 1.5, "TE": 1.0, "_default": 0.7},
    "nba": {"_default": 3.0},
    "mlb": {"SP": 1.2, "P": 0.8, "_default": 0.3},
    "nhl": {"G": 0.9, "_default": 0.3},
}

# Cap total injury swing so a long report can't dominate the model.
MAX_TEAM_MARGIN = {"nfl": 9.0, "nba": 7.0, "mlb": 2.0, "nhl": 1.5}


def _severity(status: str) -> float:
    s = (status or "").lower()
    for key, val in STATUS_SEVERITY.items():
        if key in s:
            return val
    return 0.2


def _position_value(sport: str, position: str) -> float:
    table = POSITION_WEIGHT.get(sport, {"_default": 1.0})
    return table.get((position or "").upper(), table["_default"])


def _team_margin(sport: str, injuries: List[dict]) -> float:
    """Diminishing-returns sum of injury impacts for one team."""
    impacts = sorted(
        (_position_value(sport, inj.get("position", "")) * _severity(inj.get("status", "")) for inj in injuries),
        reverse=True,
    )
    total = 0.0
    for i, impact in enumerate(impacts):
        total += impact * (0.6 ** i)  # 1.0, 0.6, 0.36, ... weighting
    return round(min(total, MAX_TEAM_MARGIN.get(sport, 6.0)), 2)


def _significant(injuries: List[dict]) -> List[dict]:
    return [
        inj
        for inj in injuries
        if _severity(inj.get("status", "")) >= STATUS_SEVERITY["questionable"]
    ]


async def injury_context_for_teams(
    sport: str, home_team: str, away_team: str, game_id: str | None = None
) -> Dict[str, object]:
    league = await espn.fetch_injuries(sport)

    home_list: List[dict] = []
    away_list: List[dict] = []
    for team_name, entries in league.items():
        if espn.team_match(team_name, home_team):
            home_list.extend(entries)
        elif espn.team_match(team_name, away_team):
            away_list.extend(entries)

    home_sig = _significant(home_list)
    away_sig = _significant(away_list)

    return {
        "injury_margin_home": _team_margin(sport, home_sig),
        "injury_margin_away": _team_margin(sport, away_sig),
        "injuries_home": home_sig[:6],
        "injuries_away": away_sig[:6],
    }
