from fastapi import APIRouter

from app.config import settings
from app.db.supabase import get_supabase
from app.models.schemas import StatusResponse
from app.services.calibration import get_calibration_summary
from app.services.odds import get_todays_games
from app.services.sync_state import get_sync_state

router = APIRouter(prefix="/api/status", tags=["status"])


@router.get("", response_model=StatusResponse)
async def get_status():
    games = await get_todays_games()
    sync = get_sync_state()
    calibration = await get_calibration_summary()
    sb = get_supabase()

    return StatusResponse(
        demo_mode=settings.use_demo_data,
        sharpapi=bool(settings.sharpapi_key),
        supabase=bool(sb),
        api_sports=bool(settings.api_sports_key),
        gnews=bool(settings.gnews_api_key),
        weather="open-meteo",
        ai_provider=settings.ai_provider,
        games_cached=len(games),
        games_source=sync.get("games_source"),
        last_odds_sync_at=sync.get("last_odds_sync_at"),
        last_odds_sync_error=sync.get("last_odds_sync_error"),
        tracking_enabled=bool(sb),
        calibration_samples=calibration.get("sample_count", 0),
    )
