from typing import List, Optional

from fastapi import APIRouter, HTTPException

from app.models.schemas import GameSummary, LineMovementPoint
from app.services.line_movement import get_line_movement
from app.services.odds import get_todays_games

router = APIRouter(prefix="/api/games", tags=["games"])


@router.get("", response_model=List[GameSummary])
async def list_games(sport: Optional[str] = None):
    return await get_todays_games(sport)


@router.get("/{game_id}/line-movement", response_model=List[LineMovementPoint])
async def game_line_movement(game_id: str):
    points = await get_line_movement(game_id)
    if not points:
        raise HTTPException(status_code=404, detail="No line history for this game yet")
    return points


@router.get("/{game_id}", response_model=GameSummary)
async def get_game(game_id: str):
    games = await get_todays_games()
    for g in games:
        if g.id == game_id:
            return g
    raise HTTPException(status_code=404, detail="Game not found")
