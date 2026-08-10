"""Independent soccer model (World Cup).

Soccer can't reuse the 2-way power model: it has three results (win/draw/loss)
and is low-scoring, so we model goals directly with Poisson distributions.

Each team gets an attack and defense strength relative to the tournament
average (from ESPN goals-for/against per game), heavily shrunk toward 1.0
because World Cup samples are tiny (1-3 group games). We turn those into
expected goals (lambda) for each side, build the independent Poisson
score-probability matrix, and read off every market we price:
P(home win), P(draw), P(away win), P(over/under 2.5), P(both teams to score).

Odds are never used here — only for edge later. If goals data is missing,
`match_probabilities_for` returns None and the caller falls back to the market.
"""
from __future__ import annotations

import math
from typing import Dict, Optional, Tuple

from app.models.schemas import GameSummary
from app.services import espn

# Average goals per team per game at a World Cup (~2.7 total).
TOURNAMENT_AVG_GOALS = 1.35
# Modest edge for the ESPN-designated "home" side (WC is largely neutral).
HOME_FACTOR = 1.06
# Shrinkage constant: strength = 1 + (raw-1) * gp/(gp+K). Small K still pulls
# 1-3 game samples hard toward average.
SHRINK_K = 3.0
MAX_GOALS = 10
DEFAULT_TOTAL_LINE = 2.5


def _poisson_pmf(k: int, lam: float) -> float:
    return math.exp(-lam) * lam**k / math.factorial(k)


def _strength(avg_for: float, avg_against: float, gp: int) -> Tuple[float, float]:
    """Return (attack, defense) multipliers relative to tournament average."""
    weight = gp / (gp + SHRINK_K) if gp else 0.0
    raw_attack = (avg_for / TOURNAMENT_AVG_GOALS) if avg_for else 1.0
    raw_defense = (avg_against / TOURNAMENT_AVG_GOALS) if avg_against is not None else 1.0
    attack = 1 + (raw_attack - 1) * weight
    defense = 1 + (raw_defense - 1) * weight  # >1 means concedes more (weaker D)
    return max(0.3, attack), max(0.3, defense)


def expected_goals(
    game: GameSummary, team_stats: Dict[str, dict]
) -> Optional[Tuple[float, float, Dict[str, float]]]:
    home = espn.find_team_stats(team_stats, game.home_team)
    away = espn.find_team_stats(team_stats, game.away_team)
    if not home or not away:
        return None
    if home.get("avg_pf") is None or away.get("avg_pf") is None:
        return None

    h_att, h_def = _strength(home.get("avg_pf"), home.get("avg_pa"), home.get("games_played", 0))
    a_att, a_def = _strength(away.get("avg_pf"), away.get("avg_pa"), away.get("games_played", 0))

    # Home expected goals = avg * home attack * away defensive leakiness * edge.
    lam_home = TOURNAMENT_AVG_GOALS * h_att * a_def * HOME_FACTOR
    lam_away = TOURNAMENT_AVG_GOALS * a_att * h_def / HOME_FACTOR
    lam_home = max(0.2, min(4.5, lam_home))
    lam_away = max(0.2, min(4.5, lam_away))
    debug = {
        "lambda_home": round(lam_home, 3),
        "lambda_away": round(lam_away, 3),
        "home_attack": round(h_att, 3),
        "away_attack": round(a_att, 3),
    }
    return lam_home, lam_away, debug


def match_probabilities(lam_home: float, lam_away: float, total_line: float = DEFAULT_TOTAL_LINE) -> dict:
    """Independent-Poisson score matrix → all priced outcome probabilities."""
    home_pmf = [_poisson_pmf(i, lam_home) for i in range(MAX_GOALS + 1)]
    away_pmf = [_poisson_pmf(j, lam_away) for j in range(MAX_GOALS + 1)]

    p_home = p_draw = p_away = 0.0
    p_over = p_btts = 0.0
    for i in range(MAX_GOALS + 1):
        for j in range(MAX_GOALS + 1):
            p = home_pmf[i] * away_pmf[j]
            if i > j:
                p_home += p
            elif i == j:
                p_draw += p
            else:
                p_away += p
            if i + j > total_line:
                p_over += p
            if i >= 1 and j >= 1:
                p_btts += p

    # Normalize (truncation at MAX_GOALS loses a sliver of mass).
    total = p_home + p_draw + p_away
    if total > 0:
        p_home, p_draw, p_away = p_home / total, p_draw / total, p_away / total

    clamp = lambda x: round(max(0.01, min(0.98, x)), 4)
    return {
        "home_win": clamp(p_home),
        "draw": clamp(p_draw),
        "away_win": clamp(p_away),
        "over": clamp(p_over),
        "under": clamp(1 - p_over),
        "btts": clamp(p_btts),
        "projected_total": round(lam_home + lam_away, 2),
        "total_line": total_line,
    }


def match_probabilities_for(
    game: GameSummary, team_stats: Dict[str, dict]
) -> Optional[dict]:
    """Full market probabilities for a match, or None if goals data is missing."""
    eg = expected_goals(game, team_stats)
    if not eg:
        return None
    lam_home, lam_away, debug = eg
    line = game.total if game.total is not None else DEFAULT_TOTAL_LINE
    probs = match_probabilities(lam_home, lam_away, line)
    probs.update(debug)
    return probs
