from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List

from app.models.schemas import GameSummary

NOW = datetime.now(timezone.utc)


def demo_games() -> List[GameSummary]:
    start = NOW + timedelta(hours=4)
    return [
        GameSummary(
            id="nba-001",
            sport="nba",
            home_team="Boston Celtics",
            away_team="Miami Heat",
            start_time=start,
            moneyline_home=-165,
            moneyline_away=140,
            spread_home=-4.5,
            spread_home_odds=-110,
            total=218.5,
            over_odds=-108,
        ),
        GameSummary(
            id="nba-002",
            sport="nba",
            home_team="Denver Nuggets",
            away_team="Phoenix Suns",
            start_time=start + timedelta(hours=1),
            moneyline_home=-120,
            moneyline_away=100,
            spread_home=-2.5,
            total=224.0,
        ),
        GameSummary(
            id="nfl-001",
            sport="nfl",
            home_team="Kansas City Chiefs",
            away_team="Buffalo Bills",
            start_time=start + timedelta(hours=3),
            moneyline_home=-135,
            moneyline_away=115,
            spread_home=-2.5,
            total=47.5,
        ),
        GameSummary(
            id="mlb-001",
            sport="mlb",
            home_team="New York Yankees",
            away_team="Boston Red Sox",
            start_time=start + timedelta(hours=2),
            moneyline_home=-145,
            moneyline_away=125,
            spread_home=-1.5,
            total=8.5,
        ),
        GameSummary(
            id="nhl-001",
            sport="nhl",
            home_team="Edmonton Oilers",
            away_team="Vancouver Canucks",
            start_time=start + timedelta(hours=5),
            moneyline_home=-110,
            moneyline_away=-110,
            spread_home=-1.5,
            total=6.5,
        ),
    ]


def demo_final_scores() -> Dict[str, dict]:
    """Final scores for demo games (always available in demo mode)."""
    specs = [
        ("nba-001", "Boston Celtics", "Miami Heat", 112, 105),
        ("nba-002", "Denver Nuggets", "Phoenix Suns", 118, 114),
        ("nfl-001", "Kansas City Chiefs", "Buffalo Bills", 27, 24),
        ("mlb-001", "New York Yankees", "Boston Red Sox", 5, 3),
        ("nhl-001", "Edmonton Oilers", "Vancouver Canucks", 4, 2),
    ]

    results: Dict[str, dict] = {}
    for gid, home, away, home_score, away_score in specs:
        results[gid] = {
            "game_id": gid,
            "home_team": home,
            "away_team": away,
            "home_score": home_score,
            "away_score": away_score,
            "game_status": "final",
            "score_display": f"{away} {away_score} @ {home} {home_score}",
        }
    return results


def _demo_context(
    *,
    home_win_prob: float,
    expected_margin: float,
    projected_total: float,
    posted_total: float,
    lean: str,
    inj_home: float = 0.0,
    inj_away: float = 0.0,
    factors: List[str] | None = None,
    props: List[dict] | None = None,
) -> dict:
    return {
        "base_home_win_prob": home_win_prob,
        "base_reason": "ok",
        "home_win_prob": home_win_prob,
        "expected_margin": expected_margin,
        "projected_total": projected_total,
        "total_lean": lean,
        "total_confidence": 0.55 if lean != "neutral" else 0.0,
        "model_source": "model",
        "weather_factor": 0.0,
        "line_move": 0.02,
        "injury_margin_home": inj_home,
        "injury_margin_away": inj_away,
        "injuries_home": [],
        "injuries_away": [],
        "home_news": [],
        "away_news": [],
        "key_factors": factors or [],
        "prop_angles": props or [],
        "narrative": "",
        "research_source": "demo",
    }


# Context signals used by the parlay scorer (demo mode).
# These showcase the independent model producing edge vs the posted line.
CONTEXT: Dict[str, dict] = {
    "nba-001": _demo_context(
        home_win_prob=0.69,
        expected_margin=6.0,
        projected_total=221.0,
        posted_total=218.5,
        lean="over",
        inj_away=3.0,
        factors=["Celtics rest edge", "Heat missing a rotation wing"],
        props=[{"player": "Jaylen Brown", "stat": "points", "direction": "over", "confidence": 0.62}],
    ),
    "nba-002": _demo_context(
        home_win_prob=0.58,
        expected_margin=2.5,
        projected_total=226.0,
        posted_total=224.0,
        lean="over",
        inj_home=2.0,
        factors=["Nuggets home altitude", "Suns on a back-to-back"],
    ),
    "nfl-001": _demo_context(
        home_win_prob=0.6,
        expected_margin=3.5,
        projected_total=46.0,
        posted_total=47.5,
        lean="under",
        factors=["Chiefs home edge", "Cold weather caps scoring"],
    ),
    "mlb-001": _demo_context(
        home_win_prob=0.57,
        expected_margin=0.6,
        projected_total=9.1,
        posted_total=8.5,
        lean="over",
        factors=["Yankees bullpen rested", "Hitter-friendly wind"],
    ),
    "nhl-001": _demo_context(
        home_win_prob=0.55,
        expected_margin=0.4,
        projected_total=6.2,
        posted_total=6.5,
        lean="neutral",
        factors=["Oilers top line clicking"],
    ),
}
