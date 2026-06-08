from typing import List, Optional

from fastapi import APIRouter, Header, HTTPException

from app.models.schemas import (
    ConfirmSettlementRequest,
    PerformanceStats,
    SavedParlayRecord,
    SaveParlayRequest,
    SetLegOutcomeRequest,
    SettlementSuggestionResponse,
    SuggestSettlementRequest,
)
from app.services.calibration import get_calibration_summary
from app.services.parlay_tracking import (
    confirm_settlement,
    get_performance,
    get_parlay,
    list_parlays,
    save_parlay,
    set_leg_outcome,
    suggest_settlement,
)

router = APIRouter(prefix="/api/tracking", tags=["tracking"])


def _require_session(session_id: Optional[str]) -> str:
    if not session_id or len(session_id) < 8:
        raise HTTPException(status_code=400, detail="X-Session-Id header required")
    return session_id


@router.post("/parlays", response_model=SavedParlayRecord)
async def create_parlay(
    body: SaveParlayRequest,
    x_session_id: Optional[str] = Header(default=None),
):
    session_id = _require_session(x_session_id)
    return await save_parlay(session_id, body)


@router.get("/parlays", response_model=List[SavedParlayRecord])
async def get_parlays(
    x_session_id: Optional[str] = Header(default=None),
    limit: int = 50,
):
    session_id = _require_session(x_session_id)
    return await list_parlays(session_id, limit=min(limit, 100))


@router.patch("/parlays/{parlay_id}/legs", response_model=SavedParlayRecord)
async def update_leg_outcome(
    parlay_id: str,
    body: SetLegOutcomeRequest,
    x_session_id: Optional[str] = Header(default=None),
):
    session_id = _require_session(x_session_id)
    result = await set_leg_outcome(session_id, parlay_id, body)
    if not result:
        raise HTTPException(status_code=404, detail="Parlay not found")
    return result


@router.get("/performance", response_model=PerformanceStats)
async def performance(x_session_id: Optional[str] = Header(default=None)):
    session_id = _require_session(x_session_id)
    return await get_performance(session_id)


@router.get("/calibration")
async def calibration():
    return await get_calibration_summary()


@router.post("/suggest", response_model=SettlementSuggestionResponse)
async def suggest(body: SuggestSettlementRequest):
    return await suggest_settlement(body)


@router.get("/parlays/{parlay_id}/suggest", response_model=SettlementSuggestionResponse)
async def suggest_saved_parlay(
    parlay_id: str,
    x_session_id: Optional[str] = Header(default=None),
):
    session_id = _require_session(x_session_id)
    record = await get_parlay(session_id, parlay_id)
    if not record:
        raise HTTPException(status_code=404, detail="Parlay not found")
    return await suggest_settlement(
        SuggestSettlementRequest(legs=record.legs, leg_outcomes=record.leg_outcomes)
    )


@router.post("/parlays/{parlay_id}/confirm", response_model=SavedParlayRecord)
async def confirm_saved_parlay(
    parlay_id: str,
    body: ConfirmSettlementRequest,
    x_session_id: Optional[str] = Header(default=None),
):
    session_id = _require_session(x_session_id)
    result = await confirm_settlement(session_id, parlay_id, body)
    if not result:
        raise HTTPException(status_code=404, detail="Parlay not found")
    return result
