"""Player-prop parlay mode: sigma model, alt lines, candidate generation, selection."""
from datetime import datetime, timezone

import pytest

from app.models.schemas import GameSummary, ParlayRequest
from app.services import parlay_generator, props
from app.services.parlay_generator import _select_prop_legs, generate_parlay


def _game(gid: str = "espn_401", home: str = "Boston Celtics", away: str = "Miami Heat") -> GameSummary:
    return GameSummary(
        id=gid,
        sport="nba",
        home_team=home,
        away_team=away,
        start_time=datetime(2026, 11, 1, tzinfo=timezone.utc),
        moneyline_home=-150,
        moneyline_away=130,
        spread_home=-4.5,
        total=218.5,
    )


# --- Sigma model -------------------------------------------------------------
def test_sigma_scales_with_average():
    assert props.stat_sigma("points", 28.0) > props.stat_sigma("points", 9.0)


def test_sigma_respects_floor():
    assert props.stat_sigma("points", 1.0) == props.STAT_SIGMA_MIN["points"]
    assert props.stat_sigma("3pm", 0.5) == props.STAT_SIGMA_MIN["3pm"]


def test_sigma_realistic_for_star_scorer():
    # ~25 ppg scorer should swing roughly 6-8 points a night, not a flat 9.
    assert 6.0 <= props.stat_sigma("points", 25.0) <= 8.0


# --- Line snapping ------------------------------------------------------------
def test_half_line_never_integer():
    for raw in (19.8, 20.0, 20.2, 4.9, 0.1):
        line = props.half_line(raw)
        assert line % 1 == 0.5
        assert line <= raw + 0.5


def test_half_line_floor():
    assert props.half_line(19.8) == 19.5
    assert props.half_line(20.2) == 20.5
    assert props.half_line(-3.0) == 0.5


# --- Odds derivation -----------------------------------------------------------
def test_american_from_prob():
    assert props.american_from_prob(0.8) == -400
    assert props.american_from_prob(0.5) == -100
    assert props.american_from_prob(0.4) == 150


# --- Candidate generation ------------------------------------------------------
AVERAGES = {
    "star guard": {"points": 27.0, "rebounds": 5.0, "assists": 7.5, "3pm": 3.2, "minutes": 35.0, "player_id": "11"},
    "glass big": {"points": 14.0, "rebounds": 11.0, "assists": 2.0, "3pm": 0.2, "minutes": 32.0, "player_id": "22"},
    "bench spark": {"points": 12.0, "rebounds": 3.0, "assists": 2.0, "3pm": 2.0, "minutes": 14.0, "player_id": "33"},
    "second star": {"points": 24.0, "rebounds": 6.0, "assists": 5.0, "3pm": 2.5, "minutes": 34.0, "player_id": "44"},
    "second big": {"points": 16.0, "rebounds": 9.5, "assists": 3.0, "3pm": 0.1, "minutes": 30.0, "player_id": "55"},
}

LEADERS_BY_GAME = {
    "espn_2": [
        {"player": "Second Star", "player_id": "44", "stat": "points", "avg": 24.0},
        {"player": "Second Big", "player_id": "55", "stat": "rebounds", "avg": 9.5},
    ],
}
DEFAULT_LEADERS = [
    {"player": "Star Guard", "player_id": "11", "stat": "points", "avg": 27.0},
    {"player": "Glass Big", "player_id": "22", "stat": "rebounds", "avg": 11.0},
    {"player": "Bench Spark", "player_id": "33", "stat": "points", "avg": 12.0},
]


@pytest.fixture
def patched_sources(monkeypatch):
    async def fake_averages(sport, home, away, player):
        return AVERAGES.get(player.lower())

    async def fake_leaders(sport, game_id, home, away, date=None):
        return LEADERS_BY_GAME.get(game_id, DEFAULT_LEADERS)

    monkeypatch.setattr(props.player_stats, "player_season_averages", fake_averages)
    monkeypatch.setattr(props.espn, "game_leaders", fake_leaders)


async def test_prop_candidates_safe_tier(patched_sources):
    ctx = {"prop_angles": [{"player": "Star Guard", "stat": "assists", "direction": "over", "confidence": 0.6}]}
    legs = await props.prop_parlay_candidates(_game(), ctx, risk="safe")

    assert legs, "expected candidates from angles + leaders"
    for leg in legs:
        assert leg["market"] == "player_prop"
        assert leg["prop_side"] == "over"
        assert leg["prop_line"] % 1 == 0.5  # push-proof
        assert leg["win_probability"] >= props.RISK_MIN_PROB["safe"]
        # Alt line sits below the season average.
        avg = AVERAGES[leg["player"].lower()][leg["stat"]]
        assert leg["prop_line"] < avg
        # Odds reflect the high probability (heavy favorite), not flat -115.
        assert leg["odds_american"] <= -200


async def test_prop_candidates_exclude_low_minutes(patched_sources):
    legs = await props.prop_parlay_candidates(_game(), {}, risk="safe")
    assert all(leg["player"].lower() != "bench spark" for leg in legs)


async def test_prop_candidates_dedupe_player_stat(patched_sources):
    ctx = {"prop_angles": [{"player": "Star Guard", "stat": "points", "direction": "over", "confidence": 0.7}]}
    legs = await props.prop_parlay_candidates(_game(), ctx, risk="balanced")
    keys = [(leg["player"], leg["stat"]) for leg in legs]
    assert len(keys) == len(set(keys))


async def test_bolder_risk_means_higher_lines(patched_sources):
    safe = await props.prop_parlay_candidates(_game(), {}, risk="safe")
    bold = await props.prop_parlay_candidates(_game(), {}, risk="bold")
    safe_line = next(l["prop_line"] for l in safe if l["player"] == "Star Guard" and l["stat"] == "points")
    bold_line = next(l["prop_line"] for l in bold if l["player"] == "Star Guard" and l["stat"] == "points")
    assert bold_line > safe_line


async def test_non_nba_returns_empty():
    game = _game()
    game = game.model_copy(update={"sport": "nfl"})
    assert await props.prop_parlay_candidates(game, {}, risk="safe") == []


# --- Leg selection --------------------------------------------------------------
def _leg(player: str, stat: str, game_id: str, score: float, win: float = 0.8) -> dict:
    return {
        "game_id": game_id,
        "player": player,
        "stat": stat,
        "score": score,
        "win_probability": win,
    }


def test_select_prop_legs_one_per_player():
    pool = [
        _leg("A", "points", "g1", 0.9),
        _leg("A", "rebounds", "g1", 0.85),
        _leg("B", "points", "g1", 0.8),
        _leg("C", "points", "g2", 0.7),
    ]
    chosen = _select_prop_legs(pool, 3)
    assert [c["player"] for c in chosen] == ["A", "B", "C"]


def test_select_prop_legs_caps_per_game():
    pool = [_leg(p, "points", "g1", s) for p, s in (("A", 0.9), ("B", 0.8), ("C", 0.7))]
    pool.append(_leg("D", "points", "g2", 0.6))
    chosen = _select_prop_legs(pool, 4)
    assert sum(1 for c in chosen if c["game_id"] == "g1") == 2
    assert any(c["game_id"] == "g2" for c in chosen)


# --- End-to-end mode dispatch ----------------------------------------------------
async def test_generate_props_parlay(monkeypatch, patched_sources):
    games = [_game("espn_1"), _game("espn_2", home="Denver Nuggets", away="Phoenix Suns")]

    async def fake_games(sport=None):
        return games

    async def fake_ctx(game):
        return {"prop_angles": []}

    async def no_insight(response):
        return None

    monkeypatch.setattr(parlay_generator, "get_todays_games", fake_games)
    monkeypatch.setattr(parlay_generator, "get_game_context", fake_ctx)
    monkeypatch.setattr(parlay_generator, "explain_parlay", no_insight)

    resp = await generate_parlay(ParlayRequest(legs=3, sport="nba", risk="safe", mode="props"))

    assert len(resp.legs) == 3
    assert all(leg.market == "player_prop" for leg in resp.legs)
    assert all(leg.win_probability >= props.RISK_MIN_PROB["safe"] for leg in resp.legs)
    # One leg per player.
    players = [leg.player for leg in resp.legs]
    assert len(players) == len(set(players))
    # Combined probability is the product of the legs.
    prod = 1.0
    for leg in resp.legs:
        prod *= leg.win_probability
    assert abs(resp.estimated_win_prob - prod) < 1e-3
    assert resp.payout_on_100 > 0
    assert "player-prop" in resp.summary


async def test_generate_props_parlay_rejects_other_sport():
    with pytest.raises(ValueError):
        await generate_parlay(ParlayRequest(legs=3, sport="nfl", mode="props"))
