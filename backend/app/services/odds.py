import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from app.config import settings
from app.db.async_db import run_sync
from app.db.supabase import get_supabase
from app.models.schemas import GameSummary
from app.services.demo_data import demo_games
from app.services.sharpapi import fetch_live_events, _to_game_summary
from app.services.sync_state import record_games_source

logger = logging.getLogger(__name__)


async def _from_supabase(sport: Optional[str] = None) -> List[GameSummary]:
    sb = get_supabase()
    if not sb:
        return []

    now = datetime.now(timezone.utc)
    window_start = (now - timedelta(hours=2)).isoformat()
    window_end = (now + timedelta(hours=36)).isoformat()

    def _fetch_games():
        query = (
            sb.table("games")
            .select("*")
            .gte("start_time", window_start)
            .lte("start_time", window_end)
            .order("start_time")
        )
        if sport:
            query = query.eq("sport", sport.lower())
        return query.execute()

    try:
        games_resp = await run_sync(_fetch_games)
    except Exception:
        logger.exception("Supabase games query failed")
        return []
    games = games_resp.data or []
    if not games:
        return []

    game_ids = [g["id"] for g in games]
    odds_cutoff = (now - timedelta(hours=48)).isoformat()

    def _fetch_odds():
        return (
            sb.table("odds_snapshots")
            .select("*")
            .in_("game_id", game_ids)
            .gte("captured_at", odds_cutoff)
            .order("captured_at", desc=True)
            .execute()
        )

    try:
        odds_resp = await run_sync(_fetch_odds)
    except Exception:
        logger.exception("Supabase odds batch query failed")
        odds_resp = type("R", (), {"data": []})()

    latest_by_game: dict = {}
    for row in odds_resp.data or []:
        gid = row["game_id"]
        if gid not in latest_by_game:
            latest_by_game[gid] = row

    result: List[GameSummary] = []
    for g in games:
        latest = latest_by_game.get(g["id"])
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
    try:
        events = await fetch_live_events()
    except Exception:
        logger.exception("SharpAPI live fetch failed")
        return []
    games = [_to_game_summary(ev) for ev in events]
    if sport:
        games = [g for g in games if g.sport == sport.lower()]
    return games


async def get_todays_games(sport: Optional[str] = None) -> List[GameSummary]:
    if settings.use_demo_data:
        games = demo_games()
        if sport:
            games = [g for g in games if g.sport == sport.lower()]
        record_games_source("demo")
        return games

    games = await _from_supabase(sport)
    if games:
        record_games_source("supabase")
        return games

    if settings.sharpapi_key:
        live = await _from_sharpapi_live(sport)
        if live:
            record_games_source("sharpapi")
            return live

    record_games_source("none")
    return []
