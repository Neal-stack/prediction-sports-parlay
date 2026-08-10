from app.services import soccer_model as sm
from app.services.parlay_generator import _sgp_combo_is_valid
from app.services.settlement import grade_leg, grade_moneyline


# --- Poisson model ---------------------------------------------------------
def test_three_way_probabilities_sum_to_one():
    p = sm.match_probabilities(1.6, 1.1)
    assert abs(p["home_win"] + p["draw"] + p["away_win"] - 1.0) < 0.01
    assert abs(p["over"] + p["under"] - 1.0) < 0.01


def test_favorite_has_higher_win_prob():
    p = sm.match_probabilities(2.3, 0.6)
    assert p["home_win"] > p["away_win"]
    assert p["home_win"] > 0.6


def test_even_match_has_meaningful_draw():
    p = sm.match_probabilities(1.3, 1.3)
    assert p["home_win"] == p["away_win"]
    assert 0.2 < p["draw"] < 0.35


def test_low_scoring_leans_under():
    p = sm.match_probabilities(0.8, 0.7)
    assert p["under"] > p["over"]


# --- Soccer SGP validity ---------------------------------------------------
def _leg(market, selection):
    return {"sport": "wc", "market": market, "selection": selection, "matchup": "A @ B"}


def test_sgp_allows_result_plus_total():
    assert _sgp_combo_is_valid([_leg("moneyline", "B"), _leg("total", "Over 2.5")]) is True


def test_sgp_rejects_two_results():
    assert _sgp_combo_is_valid([_leg("moneyline", "B"), _leg("moneyline", "Draw")]) is False


def test_sgp_rejects_over_and_under():
    assert _sgp_combo_is_valid([_leg("total", "Over 2.5"), _leg("total", "Under 2.5")]) is False


# --- 3-way grading ---------------------------------------------------------
def test_draw_selection_wins_on_tie():
    assert grade_moneyline("Draw", "Spain", "Brazil", 1, 1, three_way=True) == "win"
    assert grade_moneyline("Draw", "Spain", "Brazil", 2, 1, three_way=True) == "loss"


def test_team_ml_loses_on_draw_in_soccer():
    # 3-way: a tie is a loss for a team pick, not a push.
    assert grade_moneyline("Spain", "Spain", "Brazil", 1, 1, three_way=True) == "loss"
    # Two-way sports still push on a tie.
    assert grade_moneyline("Spain", "Spain", "Brazil", 1, 1, three_way=False) == "push"


def test_grade_leg_routes_soccer_draw():
    r = grade_leg(market="moneyline", selection="Draw", matchup="Brazil @ Spain", home_score=0, away_score=0, sport="wc")
    assert r == "win"


def test_grade_leg_soccer_total_goals():
    r = grade_leg(market="total", selection="Over 2.5", matchup="Brazil @ Spain", home_score=2, away_score=1, sport="wc")
    assert r == "win"
