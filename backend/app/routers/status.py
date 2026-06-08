from fastapi import APIRouter

from app.config import settings
from app.db.supabase import get_supabase
from app.models.schemas import StatusResponse
from app.services.odds import get_todays_games

router = APIRouter(prefix="/api/status", tags=["status"])


@router.get("", response_model=StatusResponse)
async def get_status():
    games = await get_todays_games()
    return StatusResponse(
        demo_mode=settings.use_demo_data,
        sharpapi=bool(settings.sharpapi_key),
        supabase=bool(get_supabase()),
        api_sports=bool(settings.api_sports_key),
        gnews=bool(settings.gnews_api_key),
        weather="open-meteo",
        ai_provider=settings.ai_provider,
        games_cached=len(games),
    )
