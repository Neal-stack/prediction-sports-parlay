from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from app.config import settings
from app.db.async_db import run_sync
from app.db.supabase import get_supabase
from app.models.schemas import GameSummary
from app.services import espn, odds_api, sharpapi
from app.services.demo_data import demo_games
from app.services.sync_state import record_games_source

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Shared normalization + persistence (source-agnostic)
# --------------------------------------------------------------------------- #
def event_to_game_summary(row: dict, odds: Optional[dict] = None) -> GameSummary:
    odds = odds or {}
    return GameSummary(
        id=row["id"],
        sport=row["sport"],
        home_team=row["home_team"],
        away_team=row["away_team"],
        start_time=row["start_time"],
        venue=row.get("venue"),
        is_outdoor=bool(row.get("is_outdoor", False)),
        moneyline_home=odds.get("moneyline_home") or row.get("moneyline_home"),
        moneyline_away=odds.get("moneyline_away") or row.get("moneyline_away"),
        draw_odds=odds.get("draw_odds") or row.get("draw_odds"),
        spread_home=odds.get("spread_home") if odds.get("spread_home") is not None else row.get("spread_home"),
        spread_home_odds=odds.get("spread_home_odds") or row.get("spread_home_odds") or -110,
        spread_away_odds=odds.get("spread_away_odds") or row.get("spread_away_odds") or -110,
        total=odds.get("total") if odds.get("total") is not None else row.get("total"),
        over_odds=odds.get("over_odds") or row.get("over_odds") or -110,
        under_odds=odds.get("under_odds") or row.get("under_odds") or -110,
    )


def _coerce_start(value) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return datetime.now(timezone.utc) + timedelta(hours=6)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc) + timedelta(hours=6)


async def fetch_live_events() -> List[dict]:
    """Pull odds events from the configured source, newest data first."""
    source = settings.odds_source
    events: List[dict] = []
    if source == "odds_api":
        events = await odds_api.fetch_live_events()
    elif source == "sharpapi":
        events = await sharpapi.fetch_live_events()

    # ESPN is the free default and also a fallback if a keyed source is empty.
    if not events:
        for sport in espn.SPORT_PATHS:
            events.extend(await espn.fetch_odds_events(sport))

    for ev in events:
        ev["start_time"] = _coerce_start(ev.get("start_time"))
    return events


async def persist_events(events: List[dict]) -> int:
    sb = get_supabase()
    if not sb or not events:
        return 0

    def _persist():
        for ev in events:
            sb.table("games").upsert(
                {
                    "id": ev["id"],
                    "sport": ev["sport"],
                    "home_team": ev["home_team"],
                    "away_team": ev["away_team"],
                    "start_time": ev["start_time"].isoformat(),
                    "venue": ev.get("venue"),
                    "is_outdoor": ev.get("is_outdoor", False),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            ).execute()
            sb.table("odds_snapshots").insert(
                {
                    "game_id": ev["id"],
                    "book": ev.get("book", settings.odds_source or "espn"),
                    "moneyline_home": ev.get("moneyline_home"),
                    "moneyline_away": ev.get("moneyline_away"),
                    "draw_odds": ev.get("draw_odds"),
                    "spread_home": ev.get("spread_home"),
                    "spread_home_odds": ev.get("spread_home_odds", -110),
                    "spread_away_odds": ev.get("spread_away_odds", -110),
                    "total": ev.get("total"),
                    "over_odds": ev.get("over_odds", -110),
                    "under_odds": ev.get("under_odds", -110),
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                }
            ).execute()

    try:
        await run_sync(_persist)
        return len(events)
    except Exception:
        logger.exception("Failed to persist odds events")
        return 0


async def sync_odds() -> int:
    events = await fetch_live_events()
    if not events:
        return 0
    stored = await persist_events(events)
    # If there's no DB, the live events still serve on-demand reads.
    return stored or len(events)


# --------------------------------------------------------------------------- #
# Read path
# --------------------------------------------------------------------------- #
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
            event_to_game_summary(
                {**g, "start_time": _coerce_start(g["start_time"])},
                latest,
            )
        )
    return result


async def _from_live(sport: Optional[str] = None) -> List[GameSummary]:
    events = await fetch_live_events()
    games = [event_to_game_summary(ev, ev) for ev in events]
    if sport:
        games = [g for g in games if g.sport == sport.lower()]
    return sorted(games, key=lambda g: g.start_time)


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

    live = await _from_live(sport)
    if live:
        record_games_source(settings.odds_source or "espn")
        return live

    record_games_source("none")
    return []
