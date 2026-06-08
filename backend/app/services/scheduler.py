import asyncio
import logging
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings
from app.services.context import refresh_all_context
from app.services.odds import get_todays_games
from app.services.scores import sync_recent_final_scores
from app.services.sharpapi import sync_odds
from app.services.sync_state import record_odds_sync, record_odds_sync_error

logger = logging.getLogger(__name__)
_scheduler: Optional[AsyncIOScheduler] = None


async def _odds_job() -> None:
    try:
        count = await sync_odds()
        record_odds_sync(count)
        if count:
            logger.info("Synced %s games from SharpAPI", count)
    except Exception as exc:
        record_odds_sync_error(str(exc))
        logger.exception("Odds sync failed")


async def _scores_job() -> None:
    try:
        count = await sync_recent_final_scores()
        if count:
            logger.info("Updated final scores for %s games", count)
    except Exception:
        logger.exception("Score sync failed")


async def _context_job() -> None:
    try:
        games = await get_todays_games()
        if games:
            await refresh_all_context(games)
            logger.info("Refreshed context for %s games", len(games))
    except Exception:
        logger.exception("Context refresh failed")


def start_scheduler() -> None:
    global _scheduler
    if _scheduler or not settings.sharpapi_key:
        return

    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(_odds_job, "interval", seconds=60, id="odds_sync", max_instances=1)
    _scheduler.add_job(_context_job, "interval", minutes=30, id="context_sync", max_instances=1)
    if settings.api_sports_key:
        _scheduler.add_job(
            _scores_job, "interval", minutes=5, id="score_sync", max_instances=1
        )
    _scheduler.start()

    loop = asyncio.get_running_loop()
    loop.create_task(_odds_job())
    loop.create_task(_context_job())
    if settings.api_sports_key:
        loop.create_task(_scores_job())


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
