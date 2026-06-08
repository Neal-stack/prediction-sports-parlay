from typing import Optional

from fastapi import APIRouter, HTTPException

from app.models.schemas import EdgeAnalysisRequest, EdgeAnalysisResponse, ParlayRequest, ParlayResponse
from app.services.edge_analysis import analyze_user_edges
from app.services.parlay_generator import generate_parlay

router = APIRouter(prefix="/api/parlay", tags=["parlay"])


@router.post("/generate", response_model=ParlayResponse)
async def create_parlay(body: Optional[ParlayRequest] = None):
    req = body or ParlayRequest()
    try:
        return await generate_parlay(req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/analyze-edge", response_model=EdgeAnalysisResponse)
async def analyze_edge(body: EdgeAnalysisRequest):
    if not body.parlay.legs:
        raise HTTPException(status_code=400, detail="Parlay has no legs")
    return analyze_user_edges(body)
