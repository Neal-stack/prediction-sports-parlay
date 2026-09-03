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
    market: Literal["moneyline", "spread", "total", "player_prop"]
    selection: str
    odds_american: int
    implied_prob: float
    win_probability: float
    confidence: float
    score: float
    rationale: str
    edge: Optional[float] = None  # model win prob - implied prob
    model_source: Optional[str] = None  # "model" | "market_fallback"
    # Player prop fields (market == "player_prop")
    player: Optional[str] = None
    player_id: Optional[str] = None
    stat: Optional[str] = None
    prop_line: Optional[float] = None
    prop_side: Optional[str] = None  # "over" | "under"
    fair_odds_american: Optional[int] = None  # break-even price for this leg
    availability: Optional[float] = None  # P(player suits up) haircut applied
    stat_source: Optional[str] = None  # "gamelog" | "season_avg"
    sample_games: Optional[int] = None
    prior_season_games: Optional[int] = None  # games borrowed from last season
    line_source: Optional[str] = None  # "book" | "model"
    book: Optional[str] = None  # bookmaker offering the best price
    user_probability: Optional[float] = None
    edge_vs_implied: Optional[float] = None


# "props" = player-stat legs only; "standard" = props-first, with a game
# market (ML/spread/total) admitted only when the model edge is large.
ParlayMode = Literal["standard", "props"]


class ParlayRequest(BaseModel):
    legs: int = Field(default=3, ge=2, le=5)
    sport: Optional[str] = None
    risk: RiskLevel = "balanced"
    game_id: Optional[str] = None  # same-game parlay when set
    mode: ParlayMode = "standard"  # "props" = stack player-stat alt-line legs (NBA)


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
    anchors: list[PickLeg] = Field(default_factory=list)  # optional high-confidence add-ons
    # Honest parlay math: naive product vs correlation-adjusted, and what the
    # slip is worth at the quoted price.
    correlated_win_prob: Optional[float] = None
    fair_combined_american: Optional[int] = None  # break-even price for the slip
    expected_value_per_100: Optional[float] = None
    ev_warning: Optional[str] = None
    book_check_passed: bool = True
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
    draw_odds: Optional[int] = None  # soccer 3-way result
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
    odds_source: Optional[str] = None  # "espn" | "odds_api" | "sharpapi"
    espn: bool = True
    supabase: bool
    player_props: bool = False
    weather: str
    ai_provider: Optional[str] = None
    games_cached: int
    games_source: Optional[str] = None
    last_odds_sync_at: Optional[datetime] = None
    last_odds_sync_error: Optional[str] = None
    odds_requests_remaining: Optional[int] = None
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
