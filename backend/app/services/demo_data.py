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


# Context signals used by parlay scorer (demo)
CONTEXT: Dict[str, dict] = {
    "nba-001": {
        "line_move": 0.04,
        "injury_penalty_home": 0.0,
        "injury_penalty_away": 0.06,
        "weather_factor": 0.0,
        "news_sentiment": 0.02,
    },
    "nba-002": {
        "line_move": -0.02,
        "injury_penalty_home": 0.03,
        "injury_penalty_away": 0.0,
        "weather_factor": 0.0,
        "news_sentiment": -0.01,
    },
    "nfl-001": {
        "line_move": 0.03,
        "injury_penalty_home": 0.0,
        "injury_penalty_away": 0.04,
        "weather_factor": -0.02,
        "news_sentiment": 0.05,
    },
    "mlb-001": {
        "line_move": 0.01,
        "injury_penalty_home": 0.02,
        "injury_penalty_away": 0.0,
        "weather_factor": 0.03,
        "news_sentiment": 0.0,
    },
    "nhl-001": {
        "line_move": 0.0,
        "injury_penalty_home": 0.01,
        "injury_penalty_away": 0.01,
        "weather_factor": 0.0,
        "news_sentiment": 0.01,
    },
}
