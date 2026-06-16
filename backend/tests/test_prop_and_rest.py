from datetime import datetime, timezone

import pytest

from app.models.schemas import GameSummary
from app.services import power_model
from app.services.settlement import grade_player_prop


# --- Player-prop grading from a box score ----------------------------------
BOX = {"points": 28, "rebounds": 9, "assists": 6, "3pm": 4}


def test_prop_over_wins():
    assert grade_player_prop(stat="points", prop_line=26.5, prop_side="over", box_score=BOX) == "win"


def test_prop_over_loses():
    assert grade_player_prop(stat="points", prop_line=30.5, prop_side="over", box_score=BOX) == "loss"


def test_prop_under_wins():
    assert grade_player_prop(stat="rebounds", prop_line=10.5, prop_side="under", box_score=BOX) == "win"


def test_prop_push_on_exact_line():
    assert grade_player_prop(stat="assists", prop_line=6.0, prop_side="over", box_score=BOX) == "push"


def test_prop_missing_stat_raises():
    with pytest.raises(ValueError):
        grade_player_prop(stat="blocks", prop_line=1.5, prop_side="over", box_score=BOX)


# --- Rest / back-to-back feeds the model -----------------------------------
def _game() -> GameSummary:
    return GameSummary(
        id="g1",
        sport="nba",
        home_team="Boston Celtics",
        away_team="Miami Heat",
        start_time=datetime.now(timezone.utc),
        moneyline_home=-150,
        spread_home=-4.5,
        total=218.5,
    )


STATS = {
    "Boston Celtics": {"win_pct": 0.6, "avg_pf": 116.0, "avg_pa": 112.0, "games_played": 50},
    "Miami Heat": {"win_pct": 0.6, "avg_pf": 116.0, "avg_pa": 112.0, "games_played": 50},
}


def test_home_back_to_back_hurts_home():
    rested, _, _ = power_model.base_win_probability(_game(), STATS, rest_adj=0.0)
    # Negative rest_adj = home on a back-to-back, away rested.
    tired, _, _ = power_model.base_win_probability(_game(), STATS, rest_adj=-2.5)
    assert tired < rested


def test_away_back_to_back_helps_home():
    base, _, _ = power_model.base_win_probability(_game(), STATS, rest_adj=0.0)
    boosted, _, _ = power_model.base_win_probability(_game(), STATS, rest_adj=2.5)
    assert boosted > base
