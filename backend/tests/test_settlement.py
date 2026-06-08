from app.services.settlement import (
    grade_moneyline,
    grade_spread,
    grade_total,
    parse_matchup,
)


def test_grade_moneyline_home_win():
    assert (
        grade_moneyline("Boston Celtics", "Boston Celtics", "Miami Heat", 112, 105)
        == "win"
    )
    assert (
        grade_moneyline("Miami Heat", "Boston Celtics", "Miami Heat", 112, 105)
        == "loss"
    )


def test_grade_spread_home_covers():
    assert (
        grade_spread("Boston Celtics -4.5", "Boston Celtics", "Miami Heat", 112, 105)
        == "win"
    )


def test_grade_spread_away_covers():
    assert (
        grade_spread("Miami Heat +4.5", "Boston Celtics", "Miami Heat", 110, 108)
        == "win"
    )


def test_grade_total_over():
    assert grade_total("Over 218.5", 112, 110) == "win"
    assert grade_total("Under 218.5", 112, 110) == "loss"


def test_parse_matchup():
    home, away = parse_matchup("Miami Heat @ Boston Celtics")
    assert home == "Boston Celtics"
    assert away == "Miami Heat"
