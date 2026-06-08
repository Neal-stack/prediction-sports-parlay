from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

import httpx

from app.config import settings
from app.db.supabase import get_supabase

SPORT_ENDPOINTS = {
    "nba": "https://v1.basketball.api-sports.io/injuries",
    "nfl": "https://v1.american-football.api-sports.io/injuries",
    "mlb": "https://v1.baseball.api-sports.io/injuries",
    "nhl": "https://v1.hockey.api-sports.io/injuries",
}

OUT_STATUSES = {"out", "doubtful", "questionable", "day-to-day", "injured"}


def _team_match(api_team: str, target: str) -> bool:
    a = api_team.lower()
    t = target.lower()
    return t in a or a in t or t.split()[-1] in a


async def fetch_injuries(sport: str) -> List[dict]:
    if not settings.api_sports_key:
        return []

    url = SPORT_ENDPOINTS.get(sport)
    if not url:
        return []

    headers = {"x-apisports-key": settings.api_sports_key}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                return []
            payload = resp.json()
            return payload.get("response") or []
    except Exception:
        return []


def _injury_penalty(status: str) -> float:
    s = status.lower()
    if "out" in s:
        return 0.12
    if "doubtful" in s:
        return 0.08
    if "questionable" in s or "day" in s:
        return 0.04
    return 0.02


async def injury_context_for_teams(
    sport: str, home_team: str, away_team: str, game_id: Optional[str] = None
) -> Dict[str, float]:
    injuries = await fetch_injuries(sport)
    home_penalty = 0.0
    away_penalty = 0.0
    sb = get_supabase()
    rows_to_store = []

    for item in injuries:
        team_name = (
            (item.get("team") or {}).get("name")
            or item.get("team")
            or ""
        )
        if isinstance(team_name, dict):
            team_name = team_name.get("name", "")
        player = (item.get("player") or {}).get("name") or item.get("player") or "Unknown"
        if isinstance(player, dict):
            player = player.get("name", "Unknown")
        status = str(item.get("status") or item.get("reason") or "unknown")
        if not any(x in status.lower() for x in OUT_STATUSES):
            continue

        penalty = _injury_penalty(status)
        if _team_match(str(team_name), home_team):
            home_penalty = max(home_penalty, penalty)
            rows_to_store.append((home_team, str(player), status))
        elif _team_match(str(team_name), away_team):
            away_penalty = max(away_penalty, penalty)
            rows_to_store.append((away_team, str(player), status))

    if sb and game_id and rows_to_store:
        for team, player, status in rows_to_store[:8]:
            sb.table("injury_reports").insert(
                {
                    "game_id": game_id,
                    "team": team,
                    "player": player,
                    "status": status,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                }
            ).execute()

    return {
        "injury_penalty_home": round(home_penalty, 4),
        "injury_penalty_away": round(away_penalty, 4),
    }
