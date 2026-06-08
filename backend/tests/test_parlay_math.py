from app.models.schemas import PickLeg
from app.services.calibration import compute_parlay_outcome, prob_bucket
from app.services.parlay_generator import (
    american_to_implied,
    combine_american_odds,
    payout_on_100,
)


def test_american_to_implied_favorite():
    assert abs(american_to_implied(-110) - 0.5238) < 0.001


def test_american_to_implied_underdog():
    assert abs(american_to_implied(150) - 0.4) < 0.001


def test_combine_american_odds():
    legs = [
        PickLeg(
            game_id="1",
            sport="nba",
            matchup="A @ B",
            market="moneyline",
            selection="Team A",
            odds_american=-110,
            implied_prob=0.52,
            win_probability=0.55,
            confidence=0.6,
            score=1.0,
            rationale="test",
        ),
        PickLeg(
            game_id="2",
            sport="nba",
            matchup="C @ D",
            market="moneyline",
            selection="Team C",
            odds_american=-110,
            implied_prob=0.52,
            win_probability=0.55,
            confidence=0.6,
            score=1.0,
            rationale="test",
        ),
    ]
    combined, implied = combine_american_odds(legs)
    assert combined > 200
    assert 0 < implied < 0.3


def test_payout_on_100_plus_odds():
    assert payout_on_100(350) == 350


def test_payout_on_100_minus_odds():
    assert payout_on_100(-200) == 50


def test_prob_bucket():
    assert prob_bucket(0.523) == "0.50-0.55"
    assert prob_bucket(0.48) == "0.45-0.50"


def test_compute_parlay_outcome():
    assert compute_parlay_outcome(["pending", "win"]) == "pending"
    assert compute_parlay_outcome(["win", "loss"]) == "loss"
    assert compute_parlay_outcome(["win", "win"]) == "win"
    assert compute_parlay_outcome(["win", "push"]) == "push"
