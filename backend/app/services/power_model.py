"""Independent win-probability model.

The whole point of this module: produce a win probability that does NOT start
from the betting line. We derive each team's strength from season scoring
margin and win rate (via ESPN standings), apply home-court/field, rest, and
injury adjustments, then convert the rating gap to a probability with a
per-sport logistic scale.

If team stats are unavailable (off-season, ESPN hiccup), `base_win_probability`
returns (None, reason) and the caller falls back to a market-anchored prior
with reduced confidence — clearly flagged rather than silently faked.
"""
from __future__ import annotations

import math
from typing import Dict, Optional, Tuple

from app.models.schemas import GameSummary
from app.services import espn

# Home edge expressed in net points (sport-specific).
HOME_EDGE = {"nba": 2.5, "nfl": 2.0, "mlb": 0.25, "nhl": 0.30}

# Logistic scale: points of margin equivalent to a meaningful prob swing.
# Larger scale -> each point of margin moves probability less.
MARGIN_SCALE = {"nba": 11.0, "nfl": 9.0, "mlb": 3.5, "nhl": 2.6}

# League-average total points, used to anchor the over/under lean.
LEAGUE_AVG_TOTAL = {"nba": 226.0, "nfl": 44.5, "mlb": 8.7, "nhl": 6.0}

# Standard deviation of final scoring margin / total, for normal-CDF leg probs.
MARGIN_STD = {"nba": 12.0, "nfl": 13.5, "mlb": 4.2, "nhl": 2.2}
TOTAL_STD = {"nba": 18.0, "nfl": 10.5, "mlb": 4.0, "nhl": 2.4}


def _logistic(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def spread_cover_prob(
    sport: str, expected_margin: float, home_spread: float, *, home_side: bool
) -> float:
    """P(selected side covers). expected_margin is home_score - away_score.

    Home covers when margin > -home_spread (home_spread is negative for favs).
    """
    sigma = MARGIN_STD.get(sport, 11.0)
    home_cover = _normal_cdf((expected_margin + home_spread) / sigma)
    prob = home_cover if home_side else 1.0 - home_cover
    return round(max(0.05, min(0.95, prob)), 4)


def over_prob(sport: str, projected_total: float, posted_total: float) -> float:
    """P(combined score goes over the posted total)."""
    sigma = TOTAL_STD.get(sport, 16.0)
    prob = 1.0 - _normal_cdf((posted_total - projected_total) / sigma)
    return round(max(0.05, min(0.95, prob)), 4)


def _team_rating(stats: Optional[dict], league_avg_margin: float = 0.0) -> Optional[float]:
    """Net rating in points: (avg scored - avg allowed), blended with win%.

    Win% is folded in so a team with limited scoring data still ranks sensibly.
    """
    if not stats:
        return None
    avg_pf = stats.get("avg_pf")
    avg_pa = stats.get("avg_pa")
    win_pct = stats.get("win_pct", 0.5)

    if avg_pf is not None and avg_pa is not None:
        margin = avg_pf - avg_pa
    else:
        # Map win% to an approximate margin: .500 -> 0, every 10% ~ a few pts.
        margin = (win_pct - 0.5) * 20.0

    # Light shrink toward win%-implied margin to stabilize small samples.
    winrate_margin = (win_pct - 0.5) * 20.0
    return 0.7 * margin + 0.3 * winrate_margin


def base_win_probability(
    game: GameSummary,
    team_stats: Dict[str, dict],
    *,
    rest_adj: float = 0.0,
    injury_margin_home: float = 0.0,
    injury_margin_away: float = 0.0,
) -> Tuple[Optional[float], str, Dict[str, float]]:
    """Return (home_win_prob, reason, debug).

    home_win_prob is the independent model estimate, or None when we lack stats.
    `*_margin` adjustments are in points (positive favors that side's own team).
    """
    sport = game.sport.lower()
    home = espn.find_team_stats(team_stats, game.home_team)
    away = espn.find_team_stats(team_stats, game.away_team)

    home_rating = _team_rating(home)
    away_rating = _team_rating(away)
    if home_rating is None or away_rating is None:
        return None, "no_team_stats", {}

    home_edge = HOME_EDGE.get(sport, 1.5)
    scale = MARGIN_SCALE.get(sport, 8.0)

    expected_margin = (
        (home_rating - away_rating)
        + home_edge
        + rest_adj
        - injury_margin_home
        + injury_margin_away
    )
    prob = _logistic(expected_margin / scale)
    prob = max(0.05, min(0.95, prob))

    debug = {
        "home_rating": round(home_rating, 2),
        "away_rating": round(away_rating, 2),
        "home_edge": home_edge,
        "expected_margin": round(expected_margin, 2),
    }
    return round(prob, 4), "ok", debug


def total_points_lean(
    game: GameSummary,
    team_stats: Dict[str, dict],
) -> Tuple[Optional[str], float, Dict[str, float]]:
    """Estimate whether the game projects over/under its posted total.

    Returns (lean, projected_total, debug). lean is 'over' | 'under' | None.
    """
    sport = game.sport.lower()
    home = espn.find_team_stats(team_stats, game.home_team)
    away = espn.find_team_stats(team_stats, game.away_team)
    if not home or not away:
        return None, 0.0, {}

    h_pf, h_pa = home.get("avg_pf"), home.get("avg_pa")
    a_pf, a_pa = away.get("avg_pf"), away.get("avg_pa")
    if None in (h_pf, h_pa, a_pf, a_pa):
        return None, 0.0, {}

    league_avg = LEAGUE_AVG_TOTAL.get(sport, 0.0)
    # Each side's expected score = blend of its offense and opponent defense.
    home_pts = (h_pf + a_pa) / 2
    away_pts = (a_pf + h_pa) / 2
    projected = home_pts + away_pts

    lean = None
    if game.total is not None:
        diff = projected - game.total
        threshold = max(2.0, league_avg * 0.02)
        if diff > threshold:
            lean = "over"
        elif diff < -threshold:
            lean = "under"

    return lean, round(projected, 1), {"projected_total": round(projected, 1)}
