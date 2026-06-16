import asyncio
import logging
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings
from app.services.context import refresh_all_context
from app.services.odds import get_todays_games, sync_odds
from app.services.scores import sync_recent_final_scores
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
    # ESPN provides free odds + scores with no key, so the pipeline always runs
    # unless the app is explicitly in demo mode.
    if _scheduler or settings.use_demo_data:
        return

    odds_minutes = max(5, settings.odds_sync_minutes)
    context_minutes = max(10, settings.research_ttl_minutes)

    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(_odds_job, "interval", minutes=odds_minutes, id="odds_sync", max_instances=1)
    _scheduler.add_job(_context_job, "interval", minutes=context_minutes, id="context_sync", max_instances=1)
    _scheduler.add_job(_scores_job, "interval", minutes=10, id="score_sync", max_instances=1)
    _scheduler.start()

    loop = asyncio.get_running_loop()
    loop.create_task(_odds_job())
    loop.create_task(_context_job())
    loop.create_task(_scores_job())


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
