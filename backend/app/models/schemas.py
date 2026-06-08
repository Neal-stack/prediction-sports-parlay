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
    games_source: Optional[str] = None
    last_odds_sync_at: Optional[datetime] = None
    last_odds_sync_error: Optional[str] = None
    tracking_enabled: bool = False
    calibration_samples: int = 0


LegOutcome = Literal["pending", "win", "loss", "push"]


class SaveParlayRequest(BaseModel):
    parlay: ParlayResponse
    stake: float = Field(ge=0, le=100000)


class SavedParlayRecord(BaseModel):
    id: str
    session_id: str
    stake: float
    combined_american: int
    combined_implied_prob: float
    estimated_win_prob: float
    risk: RiskLevel
    same_game: bool
    outcome: LegOutcome
    legs: list[dict]
    leg_outcomes: list[LegOutcome]
    summary: Optional[str] = None
    generated_at: datetime
    saved_at: datetime
    settled_at: Optional[datetime] = None


class SetLegOutcomeRequest(BaseModel):
    leg_index: int = Field(ge=0)
    outcome: Literal["win", "loss", "push"]


class PerformanceStats(BaseModel):
    total_parlays: int
    pending: int
    wins: int
    losses: int
    pushes: int
    leg_hit_rate: Optional[float] = None
    model_predicted_rate: Optional[float] = None
    calibration_gap: Optional[float] = None


class ConfirmSettlementRequest(BaseModel):
    outcomes: list[SetLegOutcomeRequest] = Field(min_length=1)


class LegSettlementSuggestion(BaseModel):
    leg_index: int
    outcome: Optional[Literal["win", "loss", "push"]] = None
    score_display: Optional[str] = None
    reason: str
    ready: bool = False


class SettlementSuggestionResponse(BaseModel):
    ready: bool
    suggestions: list[LegSettlementSuggestion]
    message: Optional[str] = None


class SuggestSettlementRequest(BaseModel):
    legs: list[dict]
    leg_outcomes: list[LegOutcome] = Field(default_factory=list)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    parlay: Optional[ParlayResponse] = None


class ChatResponse(BaseModel):
    reply: str
    provider: Optional[str] = None
