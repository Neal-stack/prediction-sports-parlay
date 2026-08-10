"""Soccer (World Cup) player data + match grading from ESPN (free, no key).

Unlike NBA, ESPN exposes **no** pre-game per-player season averages for the
World Cup (the athlete `/stats` endpoint 404s for most tournament players).
So player-prop *projections* can't be built from averages — they're derived
from the team goals model + roster positions instead (see ``soccer_props``).

This module owns the two things that DO come from ESPN for soccer players:

1. Rosters with positions (goalscorer/shot projection input).
2. Final match stats for grading — goals from ``keyEvents`` and shots from the
   box score / leaders once a game is played.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from app.services import espn, player_stats

logger = logging.getLogger(__name__)

SITE = "https://site.api.espn.com/apis/site/v2/sports"

# Position group -> its ESPN abbreviations / name keywords.
_GOALKEEPER = "G"
_DEFENDER = "D"
_MIDFIELDER = "M"
_FORWARD = "F"


def _position_group(raw: str) -> str:
    """Map an ESPN position string to F / M / D / G (defaults to midfielder)."""
    s = (raw or "").strip().lower()
    if not s:
        return _MIDFIELDER
    if "goalkeep" in s or s in ("g", "gk"):
        return _GOALKEEPER
    if "forward" in s or "striker" in s or "winger" in s or s in ("f", "st", "cf", "lw", "rw"):
        return _FORWARD
    if "defen" in s or "back" in s or s in ("d", "cb", "lb", "rb", "wb"):
        return _DEFENDER
    if "midfield" in s or s in ("m", "cm", "cdm", "cam", "lm", "rm"):
        return _MIDFIELDER
    # First letter fallback.
    return {"g": _GOALKEEPER, "d": _DEFENDER, "m": _MIDFIELDER, "f": _FORWARD}.get(s[0], _MIDFIELDER)


async def _team_id(team_name: str) -> Optional[str]:
    path = espn.SPORT_PATHS.get("wc")
    if not path:
        return None
    data = await player_stats._get(
        f"{SITE}/{path[0]}/{path[1]}/teams", ttl=timedelta(days=3)
    )
    if not data:
        return None
    try:
        teams = data["sports"][0]["leagues"][0]["teams"]
    except (KeyError, IndexError):
        return None
    for t in teams:
        team = t.get("team", {})
        if espn.team_match(team.get("displayName", ""), team_name):
            return str(team.get("id"))
    return None


async def team_roster(team_name: str) -> List[dict]:
    """Return [{id, name, position}] for a national team, or [] if unavailable."""
    tid = await _team_id(team_name)
    if not tid:
        return []
    path = espn.SPORT_PATHS["wc"]
    data = await player_stats._get(
        f"{SITE}/{path[0]}/{path[1]}/teams/{tid}/roster", ttl=timedelta(days=1)
    )
    if not data:
        return []
    out: List[dict] = []
    for a in data.get("athletes", []) or []:
        # Some payloads nest athletes under position groups.
        items = a.get("items") if isinstance(a, dict) and "items" in a else [a]
        for sub in items:
            pos = sub.get("position") or {}
            pos_str = pos.get("abbreviation") or pos.get("name") or "" if isinstance(pos, dict) else str(pos)
            name = sub.get("displayName") or sub.get("fullName") or ""
            if not name:
                continue
            out.append(
                {
                    "id": str(sub.get("id")) if sub.get("id") is not None else None,
                    "name": name,
                    "position": _position_group(pos_str),
                }
            )
    return out


def _last_name(name: str) -> str:
    return name.lower().split()[-1] if name else ""


def _name_matches(candidate: str, player: str) -> bool:
    c = (candidate or "").lower()
    p = (player or "").lower()
    if not c or not p:
        return False
    last = _last_name(player)
    return c == p or (bool(last) and last in c)


async def fetch_player_match_stats(
    game_id: str,
    home_team: str,
    away_team: str,
    ref_date: datetime,
    *,
    player: str,
    player_id: Optional[str] = None,
) -> Optional[Dict[str, float]]:
    """Final match stats {goals, shots} for a player, or None if unavailable.

    Goals are counted from ``keyEvents`` (goal-type events attributed to the
    player). Shots come from the box score if populated, else the totalShots
    leader block. Returns None when the summary can't be loaded so the caller
    can fall back to manual settlement.
    """
    event_id = await player_stats._event_id_for("wc", game_id, home_team, away_team, ref_date)
    if not event_id:
        return None

    path = espn.SPORT_PATHS["wc"]
    data = await player_stats._get(
        f"{SITE}/{path[0]}/{path[1]}/summary", {"event": event_id}, ttl=timedelta(minutes=10)
    )
    if not data:
        return None

    goals = _count_goals(data, player, player_id)
    shots = _find_shots(data, player, player_id)

    stats: Dict[str, float] = {"goals": float(goals)}
    if shots is not None:
        stats["shots"] = float(shots)
    return stats


_GOAL_TYPES = {"goal", "goal - header", "goal - free-kick", "penalty - scored", "own goal"}


def _count_goals(summary: dict, player: str, player_id: Optional[str]) -> int:
    goals = 0
    for ev in summary.get("keyEvents", []) or []:
        type_text = (ev.get("type", {}) or {}).get("text", "").lower()
        if "goal" not in type_text:
            continue
        # Exclude own goals from the scorer's credit.
        if "own goal" in type_text:
            continue
        scored = int(ev.get("scoreValue", 0) or 0) > 0 or type_text in _GOAL_TYPES
        if not scored and "goal" not in type_text:
            continue
        for ath in ev.get("athletesInvolved", []) or []:
            a = ath.get("athlete", ath) if isinstance(ath, dict) else {}
            aid = str(a.get("id")) if a.get("id") is not None else None
            name = a.get("displayName") or a.get("fullName") or ""
            if (player_id and aid == str(player_id)) or _name_matches(name, player):
                goals += 1
                break
    return goals


def _find_shots(summary: dict, player: str, player_id: Optional[str]) -> Optional[float]:
    # 1) Per-player box score (populated for some finished matches).
    box = summary.get("boxscore", {}) or {}
    for team_block in box.get("players", []) or []:
        for stat_block in team_block.get("statistics", []) or []:
            names = stat_block.get("names") or stat_block.get("keys") or []
            for ath in stat_block.get("athletes", []) or []:
                athlete = ath.get("athlete", {}) or {}
                aid = str(athlete.get("id")) if athlete.get("id") is not None else None
                name = athlete.get("displayName") or ""
                if not ((player_id and aid == str(player_id)) or _name_matches(name, player)):
                    continue
                values = dict(zip(names, ath.get("stats", [])))
                for key in ("totalShots", "shotsTotal", "shots", "SH"):
                    if key in values:
                        try:
                            return float(values[key])
                        except (ValueError, TypeError):
                            pass
    # 2) totalShots leader block (only the leader, best-effort).
    for team_block in summary.get("leaders", []) or []:
        for cat in team_block.get("leaders", []) or []:
            if cat.get("name") != "totalShots":
                continue
            for l in cat.get("leaders", []) or []:
                a = l.get("athlete", {}) or {}
                aid = str(a.get("id")) if a.get("id") is not None else None
                name = a.get("displayName") or ""
                if (player_id and aid == str(player_id)) or _name_matches(name, player):
                    try:
                        return float(l.get("displayValue") or l.get("value"))
                    except (ValueError, TypeError):
                        return None
    return None
