from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from app.config import settings
from app.db.supabase import get_supabase
from app.models.schemas import GameSummary
from app.services.demo_data import CONTEXT as DEMO_CONTEXT
from app.services.injuries import injury_context_for_teams
from app.services.news import news_context_for_game
from app.services.weather import sync_weather_for_game

_context_cache: Dict[str, dict] = {}
_cache_ts: Dict[str, datetime] = {}
CACHE_TTL = timedelta(minutes=20)


def _american_to_implied(odds: int) -> float:
    if odds > 0:
        return 100 / (odds + 100)
    return abs(odds) / (abs(odds) + 100)


def _line_move_from_snapshots(game_id: str) -> float:
    """Positive = line moved toward home side (home getting more respect)."""
    sb = get_supabase()
    if not sb:
        return 0.0

    since = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
    try:
        resp = (
            sb.table("odds_snapshots")
            .select("moneyline_home,moneyline_away,spread_home,captured_at")
            .eq("game_id", game_id)
            .gte("captured_at", since)
            .order("captured_at", desc=False)
            .limit(50)
            .execute()
        )
        rows = resp.data or []
    except Exception:
        return 0.0
    if len(rows) < 2:
        return 0.0

    first, last = rows[0], rows[-1]
    move = 0.0
    if first.get("moneyline_home") and last.get("moneyline_home"):
        old_imp = _american_to_implied(int(first["moneyline_home"]))
        new_imp = _american_to_implied(int(last["moneyline_home"]))
        move += new_imp - old_imp
    if first.get("spread_home") is not None and last.get("spread_home") is not None:
        move += (float(last["spread_home"]) - float(first["spread_home"])) * -0.02
    return round(max(-0.12, min(0.12, move)), 4)


async def _build_context(game: GameSummary) -> dict:
    if settings.use_demo_data:
        return DEMO_CONTEXT.get(
            game.id,
            {
                "line_move": 0.0,
                "injury_penalty_home": 0.0,
                "injury_penalty_away": 0.0,
                "news_sentiment": 0.0,
                "weather_factor": 0.0,
            },
        )

    line_move = _line_move_from_snapshots(game.id)
    injury: dict = {}
    news: dict = {}
    weather: Optional[dict] = None

    try:
        injury, news, weather = await asyncio.gather(
            injury_context_for_teams(game.sport, game.home_team, game.away_team, game.id),
            news_context_for_game(game.home_team, game.away_team, game.id),
            sync_weather_for_game(
                game.id, game.home_team, game.sport, game.is_outdoor or game.sport in ("nfl", "mlb")
            ),
            return_exceptions=True,
        )
    except Exception:
        pass

    if isinstance(injury, Exception):
        injury = {}
    if isinstance(news, Exception):
        news = {}
    if isinstance(weather, Exception):
        weather = None

    return {
        "line_move": line_move,
        **injury,
        **news,
        "weather_factor": (weather or {}).get("weather_factor", 0.0),
    }


async def get_game_context(game: GameSummary) -> dict:
    now = datetime.now(timezone.utc)
    cached_at = _cache_ts.get(game.id)
    if cached_at and now - cached_at < CACHE_TTL and game.id in _context_cache:
        return _context_cache[game.id]

    ctx = await _build_context(game)
    _context_cache[game.id] = ctx
    _cache_ts[game.id] = now
    return ctx


async def refresh_all_context(games: List[GameSummary]) -> None:
    await asyncio.gather(*[get_game_context(g) for g in games[:12]])
