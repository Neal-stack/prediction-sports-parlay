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

    async def no_log(sport, athlete_id, *, limit=25):
        return []  # season-average path unless a test opts into logs

    monkeypatch.setattr(props.player_stats, "player_season_averages", fake_averages)
    monkeypatch.setattr(props.player_stats, "player_game_log", no_log)
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
        # Prices must stay in a range a book actually offers. The old deep alt
        # lines produced ~-1400, which no book beats, making them unplayable.
        assert -700 < leg["odds_american"] < -100


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


# --- Availability: the part the stat model can't see -------------------------
def _ctx_with(status: str) -> dict:
    return {
        "prop_angles": [{"player": "Star Guard", "stat": "points", "direction": "over", "confidence": 0.0}],
        "injuries_home": [{"player": "Star Guard", "status": status, "position": "G"}],
    }


async def test_ruled_out_player_is_dropped(patched_sources):
    for status in ("Out", "Injured Reserve", "Doubtful", "Suspension"):
        legs = await props.prop_parlay_candidates(_game(), _ctx_with(status), risk="bold")
        assert all(l["player"] != "Star Guard" for l in legs), f"{status} should be dropped"


def test_availability_lookup_by_status():
    """The haircut itself: ruled out -> None, uncertain -> reduced, healthy -> baseline."""
    assert props.player_availability("Star Guard", _ctx_with("Out")) is None
    assert props.player_availability("Star Guard", _ctx_with("Doubtful")) is None
    assert props.player_availability("Star Guard", _ctx_with("Questionable")) == props.RISKY_AVAILABILITY
    assert props.player_availability("Star Guard", _ctx_with("Probable")) == props.BASE_AVAILABILITY
    assert props.player_availability("Nobody Listed", _ctx_with("Out")) == props.BASE_AVAILABILITY


def test_availability_matches_on_surname():
    ctx = {"injuries_home": [{"player": "Victor Wembanyama", "status": "Out", "position": "C"}]}
    assert props.player_availability("Wembanyama", ctx) is None


async def test_questionable_survives_no_tier(patched_sources):
    """A 0.82 availability multiplier drops a player under every tier's floor.

    Intentional for a safety-first product: if it is a coin flip whether he
    suits up, he does not belong on a slip sold as near-locks.
    """
    for tier in ("safe", "balanced", "bold"):
        legs = await props.prop_parlay_candidates(_game(), _ctx_with("Questionable"), risk=tier)
        assert all(l["player"] != "Star Guard" for l in legs), tier


async def test_healthy_player_still_capped_below_certainty(patched_sources):
    legs = await props.prop_parlay_candidates(_game(), {}, risk="safe")
    assert legs
    for l in legs:
        assert l["win_probability"] <= props.MAX_LEG_PROB
        assert l["availability"] == props.BASE_AVAILABILITY
        # Break-even price is published so the user can compare to their book.
        assert l["fair_odds_american"] < 0


async def test_break_even_price_is_better_than_quoted(patched_sources):
    """Fair odds must be less juiced than the vig-loaded quote."""
    legs = await props.prop_parlay_candidates(_game(), {}, risk="safe")
    for l in legs:
        assert l["fair_odds_american"] > l["odds_american"]  # e.g. -891 > -1417


# --- Parlay-level honesty math -----------------------------------------------
def test_correlated_prob_penalises_same_game():
    a = {"game_id": "g1", "win_probability": 0.9}
    b = {"game_id": "g1", "win_probability": 0.9}
    c = {"game_id": "g2", "win_probability": 0.9}
    same = parlay_generator.correlated_win_prob([a, b])
    diff = parlay_generator.correlated_win_prob([a, c])
    assert same < diff
    assert abs(diff - 0.81) < 1e-6  # different games = plain product


def test_ev_is_negative_at_juiced_prices():
    """Two 90% legs at typical prop juice must show a loss, not a profit."""
    prob = 0.81
    ev = parlay_generator.parlay_ev_per_100(prob, -695)
    assert ev < 0


def test_ev_is_zero_at_fair_price():
    prob = 0.81
    fair = parlay_generator.fair_american(prob)
    assert abs(parlay_generator.parlay_ev_per_100(prob, fair)) < 0.5


def test_stacking_never_rescues_negative_ev():
    """Adding legs to a -EV stack makes it worse — the core product warning."""
    evs = []
    for n in (2, 3, 4, 5):
        dec = 1.0
        for _ in range(n):
            o = props.american_from_prob(0.90 + props.PROP_VIG)
            dec *= 1 + 100 / abs(o)
        amer = int(round(-100 / (dec - 1))) if dec < 2 else int(round((dec - 1) * 100))
        evs.append(parlay_generator.parlay_ev_per_100(0.90 ** n, amer))
    assert evs == sorted(evs, reverse=True), "EV must degrade as legs are added"
    assert all(e < 0 for e in evs)


# --- Props-first selection ----------------------------------------------------
def _prop(player: str, gid: str, score: float) -> dict:
    return {"game_id": gid, "market": "player_prop", "player": player, "stat": "points",
            "score": score, "win_probability": 0.9, "edge": -0.03}


def _team_leg(gid: str, edge: float, win: float = 0.65) -> dict:
    return {"game_id": gid, "market": "moneyline", "selection": "Team", "score": 0.9,
            "win_probability": win, "edge": edge}


def test_props_fill_the_slip_before_team_markets():
    pool = [_prop("A", "g1", 0.95), _prop("B", "g2", 0.94), _team_leg("g3", 0.20)]
    chosen = parlay_generator._select_props_first(pool, 2, "safe")
    assert {c["market"] for c in chosen} == {"player_prop"}


def test_weak_team_market_never_admitted():
    pool = [_prop("A", "g1", 0.95), _team_leg("g2", 0.01)]  # edge below the bar
    chosen = parlay_generator._select_props_first(pool, 3, "safe")
    assert all(c["market"] == "player_prop" for c in chosen)


def test_wide_edge_team_market_is_admitted():
    pool = [_prop("A", "g1", 0.95), _team_leg("g2", 0.20)]
    chosen = parlay_generator._select_props_first(pool, 3, "safe")
    assert any(c["market"] == "moneyline" for c in chosen)


def test_team_market_not_stacked_on_its_own_props_game():
    """A team leg from the same game as a prop leg is correlated — skip it."""
    pool = [_prop("A", "g1", 0.95), _team_leg("g1", 0.20)]
    chosen = parlay_generator._select_props_first(pool, 3, "safe")
    assert all(c["market"] == "player_prop" for c in chosen)


def test_at_most_one_team_market_per_slip():
    pool = [_prop("A", "g1", 0.95)] + [_team_leg(f"g{i}", 0.20) for i in range(2, 6)]
    chosen = parlay_generator._select_props_first(pool, 5, "safe")
    assert sum(1 for c in chosen if c["market"] != "player_prop") <= parlay_generator.GAME_LEG_MAX


# --- Line placement guard rails ----------------------------------------------
def test_line_at_or_below_never_rounds_up():
    assert props.line_at_or_below(27.0) == 26.5   # integer average
    assert props.line_at_or_below(27.3) == 26.5   # frac below .5
    assert props.line_at_or_below(27.6) == 27.5   # frac above .5
    assert props.line_at_or_below(0.2) == 0.5     # floor


async def test_line_never_exceeds_player_average(patched_sources):
    """The core rule: no leg may require beating the player's own average."""
    for tier in ("safe", "balanced", "bold"):
        legs = await props.prop_parlay_candidates(_game(), {}, risk=tier)
        assert legs, tier
        for l in legs:
            avg = AVERAGES[l["player"].lower()][l["stat"]]
            assert l["prop_line"] <= avg, f"{tier}: {l['selection']} above avg {avg}"


async def test_line_never_deeper_than_guard_rail(patched_sources):
    """Deep alt lines are unplayable at real prices — cap how far below we go."""
    legs = await props.prop_parlay_candidates(_game(), {}, risk="safe")
    for l in legs:
        avg = AVERAGES[l["player"].lower()][l["stat"]]
        sigma = props.stat_sigma(l["stat"], avg)
        assert l["prop_line"] >= avg - props.MAX_Z_BELOW * sigma - 1.0


async def test_bold_sits_at_the_average(patched_sources):
    legs = await props.prop_parlay_candidates(_game(), {}, risk="bold")
    star = next(l for l in legs if l["player"] == "Star Guard" and l["stat"] == "points")
    assert star["prop_line"] == props.line_at_or_below(27.0)  # 26.5


async def test_overperformance_angle_does_not_raise_the_line(patched_sources):
    """A bullish research angle must not push the line up — that's the bet we refuse."""
    neutral = await props.prop_parlay_candidates(_game(), {}, risk="balanced")
    bullish_ctx = {"prop_angles": [
        {"player": "Star Guard", "stat": "points", "direction": "over", "confidence": 1.0}]}
    bullish = await props.prop_parlay_candidates(_game(), bullish_ctx, risk="balanced")
    a = next(l["prop_line"] for l in neutral if l["player"] == "Star Guard" and l["stat"] == "points")
    b = next(l["prop_line"] for l in bullish if l["player"] == "Star Guard" and l["stat"] == "points")
    assert b == a, "confidence in an over must not move the line up"


async def test_bearish_angle_lowers_the_line(patched_sources):
    """An underperform read is the one the engine is allowed to act on."""
    neutral = await props.prop_parlay_candidates(_game(), {}, risk="balanced")
    bearish_ctx = {"prop_angles": [
        {"player": "Star Guard", "stat": "points", "direction": "under", "confidence": 1.0}]}
    bearish = await props.prop_parlay_candidates(_game(), bearish_ctx, risk="balanced")
    a = next(l["prop_line"] for l in neutral if l["player"] == "Star Guard" and l["stat"] == "points")
    b = next(l["prop_line"] for l in bearish if l["player"] == "Star Guard" and l["stat"] == "points")
    assert b < a


# --- Empirical game-log distribution ------------------------------------------
def test_empirical_prob_counts_real_hits():
    values = [30, 28, 25, 22, 20, 18, 15, 12]  # 4 of 8 clear 21.5
    p = props.empirical_prob(values, 21.5, prior=0.5)
    assert abs(p - 0.5) < 0.01


def test_empirical_prob_shrinks_a_perfect_sample():
    """20-for-20 must not read as a certainty."""
    p = props.empirical_prob([30] * 20, 10.5, prior=0.7)
    assert p < 1.0 and p > 0.9


def test_empirical_prob_falls_back_to_prior_without_log():
    assert props.empirical_prob([], 21.5, prior=0.63) == 0.63


def test_distribution_prefers_gamelog_when_deep_enough():
    series = [20.0, 25.0, 30.0, 22.0, 28.0, 24.0, 26.0, 21.0, 27.0, 23.0]
    mean, sigma, source = props.distribution_for(series, season_avg=99.0, stat="points")
    assert source == "gamelog"
    assert abs(mean - 24.6) < 0.1  # real log mean, not the bogus season average


def test_distribution_falls_back_on_thin_log():
    mean, sigma, source = props.distribution_for([20.0, 25.0], season_avg=24.0, stat="points")
    assert source == "season_avg" and mean == 24.0


async def test_erratic_stat_is_skipped(monkeypatch, patched_sources):
    """A player whose stat swings wildly is noise — don't dress it up as a pick."""
    wild = [40.0, 2.0, 38.0, 1.0, 35.0, 3.0, 30.0, 0.0, 33.0, 4.0]  # cv well over the cap

    async def wild_log(sport, athlete_id, *, limit=25):
        return [{"minutes": 30.0, "points": v, "rebounds": None, "assists": None, "3pm": None}
                for v in wild]

    monkeypatch.setattr(props.player_stats, "player_game_log", wild_log)
    legs = await props.prop_parlay_candidates(_game(), {}, risk="balanced")
    assert all(not (l["player"] == "Star Guard" and l["stat"] == "points") for l in legs)
