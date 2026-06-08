from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

RiskLevel = Literal["safe", "balanced", "bold"]


class HealthResponse(BaseModel):
    status: str = "ok"
    timestamp: datetime


class PickLeg(BaseModel):
    game_id: str
    sport: str
    matchup: str
    market: Literal["moneyline", "spread", "total"]
    selection: str
    odds_american: int
    implied_prob: float
    win_probability: float
    confidence: float
    score: float
    rationale: str
    user_probability: Optional[float] = None
    edge_vs_implied: Optional[float] = None


class ParlayRequest(BaseModel):
    legs: int = Field(default=3, ge=2, le=5)
    sport: Optional[str] = None
    risk: RiskLevel = "balanced"
    game_id: Optional[str] = None  # same-game parlay when set


class ParlayResponse(BaseModel):
    legs: list[PickLeg]
    combined_american: int
    combined_implied_prob: float
    estimated_win_prob: float
    payout_on_100: float
    risk: RiskLevel
    same_game: bool = False
    summary: str
    ai_insight: Optional[str] = None
    generated_at: datetime


class LineMovementPoint(BaseModel):
    captured_at: datetime
    moneyline_home: Optional[int] = None
    moneyline_away: Optional[int] = None
    spread_home: Optional[float] = None
    total: Optional[float] = None


class GameSummary(BaseModel):
    id: str
    sport: str
    home_team: str
    away_team: str
    start_time: datetime
    venue: Optional[str] = None
    is_outdoor: bool = False
    moneyline_home: Optional[int] = None
    moneyline_away: Optional[int] = None
    spread_home: Optional[float] = None
    spread_home_odds: int = -110
    spread_away_odds: int = -110
    total: Optional[float] = None
    over_odds: int = -110
    under_odds: int = -110


class UserEdgeInput(BaseModel):
    leg_index: int = Field(ge=0)
    user_probability: float = Field(ge=0.05, le=0.95)


class EdgeAnalysisRequest(BaseModel):
    parlay: ParlayResponse
    user_edges: list[UserEdgeInput] = Field(default_factory=list)


class EdgeAnalysisResponse(BaseModel):
    legs: list[PickLeg]
    user_estimated_win_prob: float
    model_estimated_win_prob: float
    avg_edge_vs_implied: float
    summary: str


class StatusResponse(BaseModel):
    demo_mode: bool
    sharpapi: bool
    supabase: bool
    api_sports: bool
    gnews: bool
    weather: str
    ai_provider: Optional[str] = None
    games_cached: int


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    parlay: Optional[ParlayResponse] = None


class ChatResponse(BaseModel):
    reply: str
    provider: Optional[str] = None
