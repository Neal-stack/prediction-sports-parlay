from app.services.parlay_generator import _sgp_combo_is_valid


def test_rejects_over_and_under():
    legs = [
        {"market": "total", "selection": "Over 215.5"},
        {"market": "total", "selection": "Under 215.5"},
        {"market": "moneyline", "selection": "New York Knicks"},
    ]
    assert _sgp_combo_is_valid(legs) is False


def test_rejects_two_moneylines():
    legs = [
        {"market": "moneyline", "selection": "New York Knicks"},
        {"market": "moneyline", "selection": "San Antonio Spurs"},
    ]
    assert _sgp_combo_is_valid(legs) is False


def test_rejects_spurs_ml_and_knicks_spread():
    legs = [
        {
            "market": "moneyline",
            "selection": "San Antonio Spurs",
            "matchup": "San Antonio Spurs @ New York Knicks",
        },
        {
            "market": "spread",
            "selection": "New York Knicks -1.5",
            "matchup": "San Antonio Spurs @ New York Knicks",
        },
    ]
    assert _sgp_combo_is_valid(legs) is False


def test_rejects_knicks_ml_and_knicks_spread():
    legs = [
        {
            "market": "moneyline",
            "selection": "New York Knicks",
            "matchup": "San Antonio Spurs @ New York Knicks",
        },
        {
            "market": "spread",
            "selection": "New York Knicks -1.5",
            "matchup": "San Antonio Spurs @ New York Knicks",
        },
    ]
    assert _sgp_combo_is_valid(legs) is False


def test_allows_knicks_ml_spurs_spread_and_over():
    """Different teams for ML vs spread is valid."""
    legs = [
        {
            "market": "moneyline",
            "selection": "New York Knicks",
            "matchup": "San Antonio Spurs @ New York Knicks",
        },
        {
            "market": "spread",
            "selection": "San Antonio Spurs +1.5",
            "matchup": "San Antonio Spurs @ New York Knicks",
        },
        {
            "market": "total",
            "selection": "Over 215.5",
            "matchup": "San Antonio Spurs @ New York Knicks",
        },
    ]
    assert _sgp_combo_is_valid(legs) is True
