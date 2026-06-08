from datetime import datetime, timedelta, timezone
from typing import List, Optional

from app.config import settings
from app.db.supabase import get_supabase
from app.models.schemas import GameSummary
from app.services.demo_data import demo_games
from app.services.sharpapi import fetch_live_events, _to_game_summary


async def _from_supabase(sport: Optional[str] = None) -> List[GameSummary]:
    sb = get_supabase()
    if not sb:
        return []

    now = datetime.now(timezone.utc)
    window_start = (now - timedelta(hours=2)).isoformat()
    window_end = (now + timedelta(hours=36)).isoformat()

    query = (
        sb.table("games")
        .select("*")
        .gte("start_time", window_start)
        .lte("start_time", window_end)
        .order("start_time")
    )
    if sport:
        query = query.eq("sport", sport.lower())

    try:
        games_resp = query.execute()
    except Exception:
        return []
    games = games_resp.data or []
    if not games:
        return []

    result: List[GameSummary] = []
    for g in games:
        odds_resp = (
            sb.table("odds_snapshots")
            .select("*")
            .eq("game_id", g["id"])
            .order("captured_at", desc=True)
            .limit(1)
            .execute()
        )
        latest = (odds_resp.data or [None])[0]
        result.append(
            _to_game_summary(
                {
                    **g,
                    "start_time": datetime.fromisoformat(
                        str(g["start_time"]).replace("Z", "+00:00")
                    ),
                },
                latest,
            )
        )
    return result


async def _from_sharpapi_live(sport: Optional[str] = None) -> List[GameSummary]:
    events = await fetch_live_events()
    games = [_to_game_summary(ev) for ev in events]
    if sport:
        games = [g for g in games if g.sport == sport.lower()]
    return games


async def get_todays_games(sport: Optional[str] = None) -> List[GameSummary]:
    if settings.use_demo_data:
        games = demo_games()
        if sport:
            games = [g for g in games if g.sport == sport.lower()]
        return games

    games = await _from_supabase(sport)
    if games:
        return games

    if settings.sharpapi_key:
        live = await _from_sharpapi_live(sport)
        if live:
            return live

    return []
