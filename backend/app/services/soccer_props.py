"""World Cup player-prop projections (anytime goalscorer + shots).

ESPN exposes no pre-game per-player season stats for the World Cup, so these
props can't be priced off averages like the NBA ones. Instead they're derived
from the same independent Poisson goals model the match markets use:

- The model's expected team goals (lambda) are split across the roster by
  position (forwards get the biggest attacking share), giving each player an
  expected-goals rate. P(anytime scorer) = 1 - exp(-player_lambda).
- Expected team shots scale with the goals lambda and split the same way; a
  per-player shots line is priced with the Poisson survival function.

Lines and prices are model-derived (fair odds from the model probability) and
labeled as such — free sources don't publish soccer player-prop lines. Props
are surfaced as optional anchors, never auto-added to the main slip.
"""
from __future__ import annotations

import math
from typing import List, Optional

from app.models.schemas import GameSummary
from app.services import espn, soccer_player_stats

# Share of a team's expected goals / shots by position group.
GOAL_SHARE = {"F": 0.52, "M": 0.36, "D": 0.12, "G": 0.0}
SHOT_SHARE = {"F": 0.45, "M": 0.42, "D": 0.13, "G": 0.0}

# Typical number of starters per group. National-team rosters list ~26 players,
# but a group's output concentrates on the starters, so we split the team rate
# across this many (not the whole squad).
EFFECTIVE_STARTERS = {"F": 2, "M": 3, "D": 4, "G": 1}

# How many per group we're willing to *surface* as candidates. Slightly wider
# than the starter count because ESPN roster order isn't a depth chart, so a
# key attacker can be listed 3rd — we'd rather include them than miss a star.
SURFACE_CAP = {"F": 3, "M": 3, "D": 2, "G": 0}

# Team shots per game scale ~ 10 at an average attack (lambda 1.35).
SHOTS_AT_AVG = 10.0
AVG_GOALS = 1.35


def _poisson_pmf(k: int, lam: float) -> float:
    return math.exp(-lam) * lam**k / math.factorial(k)


def _poisson_sf(line: float, lam: float) -> float:
    """P(X > line) for integer counts (line is a .5 threshold)."""
    below = 0.0
    for k in range(0, int(math.floor(line)) + 1):
        below += _poisson_pmf(k, lam)
    return max(0.0, min(1.0, 1.0 - below))


def _group_lambdas(roster: List[dict], team_lambda: float, share: dict) -> dict:
    """Map player id -> per-player rate, splitting the team rate across the
    likely starters in each group (not the whole squad)."""
    counts: dict = {}
    for p in roster:
        counts[p["position"]] = counts.get(p["position"], 0) + 1
    rates: dict = {}
    for p in roster:
        grp = p["position"]
        denom = max(1, min(counts.get(grp, 1), EFFECTIVE_STARTERS.get(grp, 1)))
        rates[id(p)] = team_lambda * share.get(grp, 0.0) / denom
    return rates


def _likely_starters(roster: List[dict]) -> List[dict]:
    """Keep the first ~N of each group in roster order as assumed starters.

    Pre-game lineups aren't published for free, so this is a heuristic: it caps
    how many players per group we surface so we don't present a whole bench of
    identical projections.
    """
    kept: List[dict] = []
    seen: dict = {}
    for p in roster:
        grp = p["position"]
        rank = seen.get(grp, 0)
        seen[grp] = rank + 1
        if rank < SURFACE_CAP.get(grp, 0):
            kept.append(p)
    return kept


def _fair_odds(prob: float) -> int:
    return espn._american_from_implied(prob)


async def suggest_soccer_prop_anchors(
    game: GameSummary, ctx: dict, *, max_props: int = 3
) -> List[dict]:
    if not espn.is_soccer(game.sport):
        return []

    lam_home = ctx.get("lambda_home")
    lam_away = ctx.get("lambda_away")
    if lam_home is None or lam_away is None:
        return []

    matchup = f"{game.away_team} @ {game.home_team}"
    goal_legs: List[dict] = []
    shot_legs: List[dict] = []

    for team_name, team_lambda in ((game.home_team, float(lam_home)), (game.away_team, float(lam_away))):
        roster = await soccer_player_stats.team_roster(team_name)
        if not roster:
            continue

        goal_rates = _group_lambdas(roster, team_lambda, GOAL_SHARE)
        team_shots = max(5.0, min(22.0, SHOTS_AT_AVG * (team_lambda / AVG_GOALS)))
        shot_rates = _group_lambdas(roster, team_shots, SHOT_SHARE)

        for p in _likely_starters(roster):
            if p["position"] == "G":
                continue
            player = p["name"]
            player_id = p.get("id")
            g_lam = goal_rates.get(id(p), 0.0)
            s_lam = shot_rates.get(id(p), 0.0)

            # Anytime goalscorer (over 0.5 goals) — the headline WC prop.
            p_score = 1.0 - math.exp(-g_lam) if g_lam > 0 else 0.0
            if p_score >= 0.15:
                goal_legs.append(
                    _leg(
                        game,
                        matchup,
                        player,
                        player_id,
                        stat="goals",
                        prop_line=0.5,
                        selection=f"{player} to score",
                        prob=p_score,
                        rationale=(
                            f"Model expects {g_lam:.2f} goals for {player} "
                            f"(~{p_score:.0%} anytime scorer). Model-derived line — confirm your book."
                        ),
                        score_bonus=0.25,  # keep goalscorer (the headline) ahead of shots on score
                    )
                )

            # Shots over a .5 line near the projection.
            if s_lam >= 1.0:
                line = max(0.5, round(s_lam) - 0.5)
                p_over = _poisson_sf(line, s_lam)
                if p_over >= 0.45:
                    shot_legs.append(
                        _leg(
                            game,
                            matchup,
                            player,
                            player_id,
                            stat="shots",
                            prop_line=line,
                            selection=f"{player} Over {line:g} shots",
                            prob=p_over,
                            rationale=(
                                f"Model projects {s_lam:.1f} shots for {player} "
                                f"(~{p_over:.0%} over {line:g}). Model-derived line — confirm your book."
                            ),
                        )
                    )

    goal_legs.sort(key=lambda x: x["win_probability"], reverse=True)
    shot_legs.sort(key=lambda x: x["win_probability"], reverse=True)

    # Compose a diverse slate: lead with goalscorers (the headline market),
    # leave room for a shots prop, one prop per player.
    out: List[dict] = []
    used: set = set()

    def _take(source: List[dict], limit: int) -> None:
        for leg in source:
            if len(out) >= limit:
                return
            if leg["player"] in used:
                continue
            used.add(leg["player"])
            out.append(leg)

    _take(goal_legs, max(1, max_props - 1))  # headline goalscorers
    _take(shot_legs, max_props)              # at least one shots prop, new players
    _take(goal_legs, max_props)              # backfill with more goalscorers
    _take(shot_legs, max_props)
    return out


def _leg(
    game: GameSummary,
    matchup: str,
    player: str,
    player_id: Optional[str],
    *,
    stat: str,
    prop_line: float,
    selection: str,
    prob: float,
    rationale: str,
    score_bonus: float = 0.0,
) -> dict:
    prob = round(max(0.05, min(0.95, prob)), 4)
    odds = _fair_odds(prob)
    return {
        "game_id": game.id,
        "sport": game.sport,
        "matchup": matchup,
        "market": "player_prop",
        "selection": selection,
        "odds_american": odds,
        "implied_prob": prob,
        "win_probability": prob,
        "confidence": round(min(0.7, 0.4 + prob * 0.3), 4),
        "score": round(prob + score_bonus, 4),
        "edge": 0.0,  # fair (model-derived) line — no market edge claimed
        "model_source": "model",
        "player": player,
        "player_id": str(player_id) if player_id is not None else None,
        "stat": stat,
        "prop_line": prop_line,
        "prop_side": "over",
        "rationale": rationale,
    }
