from datetime import datetime, timezone

from app.models.schemas import GameSummary
from app.services import power_model


def _game(**kw) -> GameSummary:
    base = dict(
        id="g1",
        sport="nba",
        home_team="Boston Celtics",
        away_team="Miami Heat",
        start_time=datetime.now(timezone.utc),
        moneyline_home=-150,
        moneyline_away=130,
        spread_home=-4.5,
        total=218.5,
    )
    base.update(kw)
    return GameSummary(**base)


STATS = {
    "Boston Celtics": {"win_pct": 0.7, "avg_pf": 120.0, "avg_pa": 110.0, "games_played": 50},
    "Miami Heat": {"win_pct": 0.5, "avg_pf": 112.0, "avg_pa": 112.0, "games_played": 50},
}


def test_stronger_team_is_favored():
    prob, reason, debug = power_model.base_win_probability(_game(), STATS)
    assert reason == "ok"
    assert prob is not None and prob > 0.5
    assert debug["expected_margin"] > 0


def test_independent_of_betting_line():
    """Probability must not be derived from the moneyline — flipping the line
    while keeping stats fixed leaves the model unchanged."""
    p1, _, _ = power_model.base_win_probability(_game(moneyline_home=-150), STATS)
    p2, _, _ = power_model.base_win_probability(_game(moneyline_home=+400), STATS)
    assert p1 == p2


def test_missing_stats_returns_none():
    prob, reason, _ = power_model.base_win_probability(_game(), {})
    assert prob is None
    assert reason == "no_team_stats"


def test_spread_cover_prob_sides_complementary():
    home = power_model.spread_cover_prob("nba", 6.0, -4.5, home_side=True)
    away = power_model.spread_cover_prob("nba", 6.0, -4.5, home_side=False)
    assert abs((home + away) - 1.0) < 1e-6
    assert home > away  # home favored by margin


def test_over_prob_responds_to_projection():
    high = power_model.over_prob("nba", projected_total=230.0, posted_total=218.5)
    low = power_model.over_prob("nba", projected_total=205.0, posted_total=218.5)
    assert high > 0.5 > low


def test_injury_margin_shifts_probability():
    healthy, _, _ = power_model.base_win_probability(_game(), STATS)
    hurt, _, _ = power_model.base_win_probability(_game(), STATS, injury_margin_home=8.0)
    assert hurt < healthy
