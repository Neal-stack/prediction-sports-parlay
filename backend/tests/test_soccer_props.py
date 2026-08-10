import math
from datetime import datetime, timezone

import pytest

from app.models.schemas import GameSummary, PickLeg
from app.services import soccer_player_stats as sps
from app.services import soccer_props
from app.services.settlement import grade_player_prop


def _game() -> GameSummary:
    return GameSummary(
        id="espn_760490",
        sport="wc",
        home_team="Ivory Coast",
        away_team="Norway",
        start_time=datetime(2026, 6, 30, tzinfo=timezone.utc),
    )


ROSTER = [
    {"id": "1", "name": "Star Striker", "position": "F"},
    {"id": "2", "name": "Backup Striker", "position": "F"},
    {"id": "3", "name": "Key Midfielder", "position": "M"},
    {"id": "4", "name": "Center Back", "position": "D"},
    {"id": "5", "name": "Keeper", "position": "G"},
]


# --- Position mapping -------------------------------------------------------
def test_position_group_mapping():
    assert sps._position_group("Forward") == "F"
    assert sps._position_group("CF") == "F"
    assert sps._position_group("Goalkeeper") == "G"
    assert sps._position_group("Center Back") == "D"
    assert sps._position_group("Midfielder") == "M"
    assert sps._position_group("") == "M"  # default


# --- Poisson helpers --------------------------------------------------------
def test_poisson_sf_matches_manual():
    lam = 2.3
    # P(X > 1.5) = 1 - P(0) - P(1)
    expected = 1 - math.exp(-lam) - lam * math.exp(-lam)
    assert abs(soccer_props._poisson_sf(1.5, lam) - expected) < 1e-9


def test_group_lambdas_splits_within_group():
    rates = soccer_props._group_lambdas(ROSTER, 2.0, soccer_props.GOAL_SHARE)
    # Two forwards split the forward share of 2.0 goals.
    forwards = [p for p in ROSTER if p["position"] == "F"]
    per_fwd = 2.0 * soccer_props.GOAL_SHARE["F"] / 2
    for f in forwards:
        assert abs(rates[id(f)] - per_fwd) < 1e-9
    keeper = next(p for p in ROSTER if p["position"] == "G")
    assert rates[id(keeper)] == 0.0


# --- Anchor generation ------------------------------------------------------
@pytest.mark.asyncio
async def test_suggest_anchors_produces_valid_prop_legs(monkeypatch):
    async def fake_roster(team_name):
        return ROSTER if team_name == "Ivory Coast" else []

    monkeypatch.setattr(sps, "team_roster", fake_roster)

    ctx = {"lambda_home": 2.1, "lambda_away": 0.9}
    legs = await soccer_props.suggest_soccer_prop_anchors(_game(), ctx, max_props=3)

    assert legs, "expected at least one soccer prop anchor"
    for leg in legs:
        # Must be a valid PickLeg and a player_prop.
        pick = PickLeg(**leg)
        assert pick.market == "player_prop"
        assert pick.prop_side == "over"
        assert pick.stat in ("goals", "shots")
        assert pick.player
        assert -100000 < pick.odds_american < 100000
    # No keeper props.
    assert all(leg["player"] != "Keeper" for leg in legs)
    # At most one prop per player.
    players = [leg["player"] for leg in legs]
    assert len(players) == len(set(players))


@pytest.mark.asyncio
async def test_no_anchors_without_lambdas(monkeypatch):
    ctx = {}  # market fallback: no model goals
    legs = await soccer_props.suggest_soccer_prop_anchors(_game(), ctx)
    assert legs == []


@pytest.mark.asyncio
async def test_striker_outranks_defender(monkeypatch):
    async def fake_roster(team_name):
        return ROSTER if team_name == "Ivory Coast" else []

    monkeypatch.setattr(sps, "team_roster", fake_roster)
    ctx = {"lambda_home": 2.4, "lambda_away": 0.6}
    legs = await soccer_props.suggest_soccer_prop_anchors(_game(), ctx, max_props=5)
    scorer_legs = [l for l in legs if l["stat"] == "goals"]
    if len(scorer_legs) >= 2:
        # Forwards should have a higher scoring probability than defenders.
        by_player = {l["player"]: l["win_probability"] for l in scorer_legs}
        if "Star Striker" in by_player and "Center Back" in by_player:
            assert by_player["Star Striker"] > by_player["Center Back"]


# --- Grading ----------------------------------------------------------------
def test_count_goals_from_key_events():
    summary = {
        "keyEvents": [
            {"type": {"text": "Goal"}, "athletesInvolved": [{"id": "1", "displayName": "Star Striker"}]},
            {"type": {"text": "Goal - Header"}, "athletesInvolved": [{"id": "1", "displayName": "Star Striker"}]},
            {"type": {"text": "Yellow Card"}, "athletesInvolved": [{"id": "3", "displayName": "Key Midfielder"}]},
            {"type": {"text": "Own Goal"}, "athletesInvolved": [{"id": "4", "displayName": "Center Back"}]},
        ]
    }
    assert sps._count_goals(summary, "Star Striker", "1") == 2
    assert sps._count_goals(summary, "Key Midfielder", "3") == 0
    assert sps._count_goals(summary, "Center Back", "4") == 0  # own goal excluded


def test_find_shots_from_leaders():
    summary = {
        "leaders": [
            {
                "leaders": [
                    {
                        "name": "totalShots",
                        "leaders": [{"athlete": {"id": "1", "displayName": "Star Striker"}, "displayValue": "4"}],
                    }
                ]
            }
        ]
    }
    assert sps._find_shots(summary, "Star Striker", "1") == 4.0
    assert sps._find_shots(summary, "Nobody", "99") is None


def test_grade_soccer_goalscorer():
    # Scored: over 0.5 goals wins.
    assert grade_player_prop(stat="goals", prop_line=0.5, prop_side="over", box_score={"goals": 1.0}) == "win"
    # Blanked: over 0.5 goals loses.
    assert grade_player_prop(stat="goals", prop_line=0.5, prop_side="over", box_score={"goals": 0.0}) == "loss"


def test_grade_soccer_shots():
    assert grade_player_prop(stat="shots", prop_line=1.5, prop_side="over", box_score={"shots": 3.0}) == "win"
    assert grade_player_prop(stat="shots", prop_line=2.5, prop_side="over", box_score={"shots": 2.0}) == "loss"
