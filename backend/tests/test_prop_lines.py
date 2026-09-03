"""Real book prop lines: de-vigging, best-price selection, event matching."""
import pytest

from app.services import prop_lines as pl


# --- De-vig -------------------------------------------------------------------
def test_devig_removes_the_hold():
    """Raw implied prices sum above 1; the excess is the book's margin."""
    raw_over = pl.american_to_prob(-122)
    raw_under = pl.american_to_prob(-110)
    assert raw_over + raw_under > 1.0  # the hold
    fair = pl.devig(-122, -110)
    assert 0.50 < fair < 0.53
    # A fair pair must come back symmetric.
    assert pl.devig(-110, -110) == 0.5


def test_devig_handles_plus_money():
    fair = pl.devig(148, -200)
    assert 0.35 < fair < 0.40


def test_devig_needs_both_sides():
    assert pl.devig(-110, None) is None
    assert pl.devig(None, -110) is None


# --- Best price across books ---------------------------------------------------
def _payload(books):
    return {"bookmakers": [
        {"key": key, "markets": [{"key": "player_points", "outcomes": outs}]}
        for key, outs in books
    ]}


def test_normalize_picks_best_price_each_side():
    """Higher American number is always better for the bettor."""
    payload = _payload([
        ("bookA", [{"description": "Jayson Tatum", "name": "Over", "point": 26.5, "price": -122},
                   {"description": "Jayson Tatum", "name": "Under", "point": 26.5, "price": -130}]),
        ("bookB", [{"description": "Jayson Tatum", "name": "Over", "point": 26.5, "price": -108},
                   {"description": "Jayson Tatum", "name": "Under", "point": 26.5, "price": -140}]),
    ])
    rows = pl.normalize_event_props(payload)
    assert len(rows) == 1
    row = rows[0]
    assert row["over_odds"] == -108 and row["over_book"] == "bookB"   # better over
    assert row["under_odds"] == -130 and row["under_book"] == "bookA"  # better under


def test_normalize_keeps_distinct_lines_apart():
    payload = _payload([
        ("bookA", [{"description": "X", "name": "Over", "point": 24.5, "price": -110},
                   {"description": "X", "name": "Over", "point": 26.5, "price": 120}]),
    ])
    rows = pl.normalize_event_props(payload)
    assert {r["line"] for r in rows} == {24.5, 26.5}


def test_normalize_maps_all_four_markets():
    payload = {"bookmakers": [{"key": "b", "markets": [
        {"key": m, "outcomes": [{"description": "X", "name": "Over", "point": 1.5, "price": -110}]}
        for m in ("player_points", "player_rebounds", "player_assists", "player_threes")
    ]}]}
    assert {r["stat"] for r in pl.normalize_event_props(payload)} == {
        "points", "rebounds", "assists", "3pm"}


def test_normalize_ignores_unknown_markets_and_junk():
    payload = {"bookmakers": [{"key": "b", "markets": [
        {"key": "player_blocks", "outcomes": [
            {"description": "X", "name": "Over", "point": 1.5, "price": -110}]},
        {"key": "player_points", "outcomes": [
            {"description": "", "name": "Over", "point": 20.5, "price": -110},      # no player
            {"description": "Y", "name": "Yes", "point": 20.5, "price": -110},      # bad side
            {"description": "Z", "name": "Over", "point": None, "price": -110},     # no line
        ]},
    ]}]}
    assert pl.normalize_event_props(payload) == []


def test_normalize_empty_payload():
    assert pl.normalize_event_props({}) == []


# --- Event matching -------------------------------------------------------------
EVENTS = [
    {"id": "e1", "home_team": "Detroit Pistons", "away_team": "Boston Celtics"},
    {"id": "e2", "home_team": "Los Angeles Lakers", "away_team": "Golden State Warriors"},
]


def test_match_event_finds_matchup():
    assert pl.match_event(EVENTS, "Detroit Pistons", "Boston Celtics") == "e1"


def test_match_event_is_direction_sensitive():
    """Home and away are not interchangeable — a flipped matchup is a different game."""
    assert pl.match_event(EVENTS, "Boston Celtics", "Detroit Pistons") is None


def test_match_event_tolerates_naming_drift():
    assert pl.match_event(EVENTS, "Pistons", "Celtics") == "e1"


def test_match_event_returns_none_when_absent():
    assert pl.match_event(EVENTS, "Miami Heat", "Chicago Bulls") is None


# --- Market mapping --------------------------------------------------------------
def test_market_stat_mapping_round_trips():
    for market, stat in pl.MARKET_STATS.items():
        assert pl.STAT_MARKETS[stat] == market


# --- Trusting (or refusing) a disagreement with the market ---------------------
from app.services import props  # noqa: E402


def test_small_disagreement_is_trusted():
    v = props.evaluate_book_line(model_prob=0.60, devig_prob=0.55, sample=25)
    assert v["trusted"] and abs(v["edge"] - 0.05) < 1e-9


def test_huge_disagreement_is_refused():
    """The Tatum case: a 40-point gap is our data being wrong, not an edge."""
    v = props.evaluate_book_line(model_prob=0.894, devig_prob=0.488, sample=16)
    assert not v["trusted"]
    assert "our data" in v["reason"]


def test_disagreement_guard_is_symmetric():
    """Works in both directions — a model that is far too low is equally suspect."""
    assert not props.evaluate_book_line(model_prob=0.10, devig_prob=0.50, sample=30)["trusted"]


def test_thin_sample_is_refused_even_when_edge_is_small():
    v = props.evaluate_book_line(model_prob=0.56, devig_prob=0.54, sample=4)
    assert not v["trusted"] and "too thin" in v["reason"]


def test_guard_threshold_boundary():
    inside = props.evaluate_book_line(
        model_prob=0.50 + props.MAX_TRUSTED_DISAGREEMENT - 0.001, devig_prob=0.50, sample=20)
    outside = props.evaluate_book_line(
        model_prob=0.50 + props.MAX_TRUSTED_DISAGREEMENT + 0.001, devig_prob=0.50, sample=20)
    assert inside["trusted"] and not outside["trusted"]


# --- book_prop_candidates end to end (mocked, no credits burned) ---------------
import pytest  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

from app.models.schemas import GameSummary  # noqa: E402
from app.services import player_stats  # noqa: E402


def _game():
    return GameSummary(id="g1", sport="nba", home_team="Detroit Pistons",
                       away_team="Boston Celtics",
                       start_time=datetime(2026, 10, 20, tzinfo=timezone.utc))


@pytest.fixture
def mocked_player(monkeypatch):
    """A 22.8 ppg scorer with realistic game-to-game spread, 30 logged games."""
    values = [24.0, 18.0, 30.0, 12.0, 28.0, 22.0, 26.0, 20.0, 32.0, 16.0] * 3

    async def avgs(sport, home, away, player):
        return {"points": 24.0, "rebounds": 8.0, "assists": 5.0, "3pm": 2.0,
                "minutes": 34.0, "player_id": "99"}

    async def blended(sport, aid, *, target=25, now=None):
        return ([{"minutes": 34.0, "points": v, "rebounds": None,
                  "assists": None, "3pm": None} for v in values],
                {"current": len(values), "prior": 0})

    monkeypatch.setattr(props.player_stats, "player_season_averages", avgs)
    monkeypatch.setattr(props.player_stats, "player_log_blended", blended)
    monkeypatch.setattr(props, "calibration_adjustment", lambda *a, **k: 0.0)

    async def loaded():
        return None
    monkeypatch.setattr(props, "ensure_calibration_loaded", loaded)


def _row(line, over, under):
    return {"player": "Test Player", "stat": "points", "line": line,
            "over_odds": over, "under_odds": under,
            "over_book": "bookA", "under_book": "bookB",
            "devig_over_prob": pl.devig(over, under)}


async def test_book_candidate_takes_over_when_line_is_below_average(mocked_player):
    """Line under his average -> the over is the 'usual number' bet."""
    legs = await props.book_prop_candidates(_game(), {}, [_row(17.5, -200, 165)], min_edge=0.0)
    assert legs and legs[0]["prop_side"] == "over"
    assert legs[0]["line_source"] == "book"


async def test_book_candidate_takes_under_when_line_is_above_average(mocked_player):
    """Line above his average -> betting the over would need a career night."""
    legs = await props.book_prop_candidates(_game(), {}, [_row(28.5, 165, -200)], min_edge=0.0)
    assert legs and legs[0]["prop_side"] == "under"


async def test_book_candidate_rejects_wild_disagreement(mocked_player):
    """A line the market prices near even but our log says is a lock: refuse it."""
    # He cleared 5.5 in every logged game, but the book calls it a coin flip.
    legs = await props.book_prop_candidates(_game(), {}, [_row(5.5, -110, -110)], min_edge=0.0)
    assert legs == []


async def test_book_candidate_respects_min_edge(mocked_player):
    fair = _row(17.5, -200, 165)
    assert await props.book_prop_candidates(_game(), {}, [fair], min_edge=0.99) == []


async def test_book_candidate_drops_injured_player(mocked_player):
    ctx = {"injuries_home": [{"player": "Test Player", "status": "Out", "position": "G"}]}
    assert await props.book_prop_candidates(_game(), ctx, [_row(17.5, -200, 165)], min_edge=0.0) == []


async def test_book_candidate_carries_best_book_through(mocked_player):
    legs = await props.book_prop_candidates(_game(), {}, [_row(17.5, -200, 165)], min_edge=0.0)
    assert legs[0]["book"] == "bookA" and legs[0]["odds_american"] == -200
