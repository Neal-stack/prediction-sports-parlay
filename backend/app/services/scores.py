from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import httpx

from app.config import settings
from app.db.async_db import run_sync
from app.db.supabase import get_supabase
from app.services.demo_data import demo_final_scores

logger = logging.getLogger(__name__)

SCORE_CONFIG = {
    "nba": {
        "url": "https://v1.basketball.api-sports.io/games",
        "league": 12,
    },
    "nfl": {
        "url": "https://v1.american-football.api-sports.io/games",
        "league": 1,
    },
    "mlb": {
        "url": "https://v1.baseball.api-sports.io/games",
        "league": 1,
    },
    "nhl": {
        "url": "https://v1.hockey.api-sports.io/games",
        "league": 57,
    },
}

FINAL_STATUSES = {"ft", "finished", "aot", "aet", "after ot", "final", "ended"}


def _team_match(api_team: str, target: str) -> bool:
    a = api_team.lower()
    t = target.lower()
    return t in a or a in t or t.split()[-1] in a


def _extract_scores(item: dict) -> Tuple[Optional[int], Optional[int], str]:
    status = item.get("status") or {}
    short = str(status.get("short") or status.get("long") or "").lower()

    scores = item.get("scores") or {}
    home_block = scores.get("home") or {}
    away_block = scores.get("away") or {}

    home_score = home_block.get("total") if isinstance(home_block, dict) else None
    away_score = away_block.get("total") if isinstance(away_block, dict) else None

    if home_score is None and isinstance(home_block, (int, float)):
        home_score = home_block
    if away_score is None and isinstance(away_block, (int, float)):
        away_score = away_block

    game_status = "final" if short in FINAL_STATUSES else "live" if short else "scheduled"
    if home_score is None or away_score is None:
        return None, None, game_status
    return int(home_score), int(away_score), game_status


async def _fetch_api_sports_games(sport: str, date_str: str) -> List[dict]:
    cfg = SCORE_CONFIG.get(sport)
    if not cfg or not settings.api_sports_key:
        return []

    headers = {"x-apisports-key": settings.api_sports_key}
    params = {"date": date_str, "league": cfg["league"], "season": datetime.now().year}

    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.get(cfg["url"], headers=headers, params=params)
            if resp.status_code != 200:
                return []
            return resp.json().get("response") or []
    except Exception:
        logger.exception("API-Sports score fetch failed for %s", sport)
        return []


async def _lookup_score_from_api(
    sport: str,
    home_team: str,
    away_team: str,
    start_time: datetime,
) -> Tuple[Optional[int], Optional[int], str]:
    for offset in (0, -1):
        day = (start_time + timedelta(days=offset)).strftime("%Y-%m-%d")
        games = await _fetch_api_sports_games(sport, day)
        for item in games:
            teams = item.get("teams") or {}
            home = (teams.get("home") or {}).get("name") or ""
            away = (teams.get("away") or {}).get("name") or ""
            if _team_match(home, home_team) and _team_match(away, away_team):
                return _extract_scores(item)
    return None, None, "scheduled"


async def get_game_result(game_id: str) -> Optional[dict]:
    """Return final/live scores for a game id."""
    if settings.use_demo_data:
        demo = demo_final_scores().get(game_id)
        if demo:
            return demo
        return None

    sb = get_supabase()

    def _fetch():
        if not sb:
            return None
        return (
            sb.table("games")
            .select("*")
            .eq("id", game_id)
            .limit(1)
            .execute()
        )

    row = None
    if sb:
        try:
            resp = await run_sync(_fetch)
            row = (resp.data or [None])[0]
        except Exception:
            logger.exception("Failed to load game %s", game_id)

    if row and row.get("game_status") == "final":
        return {
            "game_id": game_id,
            "home_team": row["home_team"],
            "away_team": row["away_team"],
            "home_score": row["home_score"],
            "away_score": row["away_score"],
            "game_status": "final",
            "score_display": f"{row['away_team']} {row['away_score']} @ {row['home_team']} {row['home_score']}",
        }

    if row:
        home_team = row["home_team"]
        away_team = row["away_team"]
        sport = row["sport"]
        start_time = datetime.fromisoformat(str(row["start_time"]).replace("Z", "+00:00"))
    else:
        return None

    home_score, away_score, status = await _lookup_score_from_api(
        sport, home_team, away_team, start_time
    )
    if home_score is None or away_score is None:
        return None

    if sb and status == "final":
        def _update():
            sb.table("games").update(
                {
                    "home_score": home_score,
                    "away_score": away_score,
                    "game_status": "final",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            ).eq("id", game_id).execute()

        try:
            await run_sync(_update)
        except Exception:
            logger.exception("Failed to persist scores for %s", game_id)

    return {
        "game_id": game_id,
        "home_team": home_team,
        "away_team": away_team,
        "home_score": home_score,
        "away_score": away_score,
        "game_status": status,
        "score_display": f"{away_team} {away_score} @ {home_team} {home_score}",
    }


async def sync_scores_for_game_ids(game_ids: List[str]) -> int:
    updated = 0
    for gid in game_ids:
        result = await get_game_result(gid)
        if result and result.get("game_status") == "final":
            updated += 1
    return updated


async def sync_recent_final_scores() -> int:
    """Refresh scores for games that started in the last 48 hours."""
    if settings.use_demo_data:
        return 0

    sb = get_supabase()
    if not sb:
        return 0

    now = datetime.now(timezone.utc)
    start = (now - timedelta(hours=48)).isoformat()
    end = now.isoformat()

    def _fetch():
        return (
            sb.table("games")
            .select("id")
            .gte("start_time", start)
            .lte("start_time", end)
            .neq("game_status", "final")
            .execute()
        )

    try:
        resp = await run_sync(_fetch)
        ids = [r["id"] for r in (resp.data or [])]
        return await sync_scores_for_game_ids(ids)
    except Exception:
        logger.exception("Failed to sync recent scores")
        return 0
