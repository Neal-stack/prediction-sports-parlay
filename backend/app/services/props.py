"""Player-prop projections: anchors and prop-only parlay legs.

Free data sources don't expose live player-prop *lines* (those sit behind paid
odds tiers), so we don't fabricate book odds and feed them into bankroll math.
Props come in two flavors, both grounded in ESPN season averages with a
transparent normal model:

- **Anchors** — optional add-ons at the player's average (a ~coin-flip line),
  shown next to a game-market parlay.
- **Prop parlay legs** — the "stack high-probability stats" product: alt lines
  set *below* the average at a risk-tiered confidence target (safe ~80%,
  balanced ~70%, bold ~60%), with odds derived from the model probability plus
  typical book vig so combined payout math is meaningful.

The UI labels both as model projections to confirm against the user's book.
When a real prop-line source is added later, these become fully priced legs.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

from app.models.schemas import GameSummary
from app.services import espn, player_stats, soccer_props

STAT_LABEL = {"points": "Points", "rebounds": "Rebounds", "assists": "Assists", "3pm": "3PM"}

# Single-game spread scales with volume (Poisson-like): sigma = k * sqrt(avg),
# floored so low-volume stats keep realistic spread. Calibrated against typical
# NBA game logs (a 25 ppg scorer swings ~7 pts; an 8 ppg role player ~4).
STAT_SIGMA_K = {"points": 1.4, "rebounds": 1.15, "assists": 1.05, "3pm": 0.95}
STAT_SIGMA_MIN = {"points": 3.0, "rebounds": 1.5, "assists": 1.2, "3pm": 0.8}

# Only project stats with enough volume for the normal model to behave.
STAT_MIN_AVG = {"points": 8.0, "rebounds": 4.0, "assists": 3.0, "3pm": 1.5}
MIN_MINUTES = 20.0  # low-minute players are too volatile / DNP-prone to stack

PLACEHOLDER_ODDS = -115  # typical prop juice at the book's own line
PROP_VIG = 0.035  # implied-prob margin baked into derived alt-line odds

# Risk tier -> z-score below the projection for the alt line. After snapping to
# the .5 grid the exact probability is recomputed, so these are targets.
RISK_Z = {"safe": 0.85, "balanced": 0.55, "bold": 0.25}  # ~80% / ~71% / ~60%
RISK_MIN_PROB = {"safe": 0.72, "balanced": 0.62, "bold": 0.52}


def _implied(odds: int) -> float:
    return abs(odds) / (abs(odds) + 100) if odds < 0 else 100 / (odds + 100)


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _round_half(value: float) -> float:
    return round(value * 2) / 2


def stat_sigma(stat: str, avg: float) -> float:
    """Single-game standard deviation for a stat, scaled to the player's average."""
    k = STAT_SIGMA_K.get(stat, 1.2)
    floor = STAT_SIGMA_MIN.get(stat, 1.0)
    return max(floor, k * math.sqrt(max(avg, 0.1)))


def half_line(value: float) -> float:
    """Snap to the nearest .5 line at or below value — never an integer, so no pushes."""
    return max(0.5, math.floor(value) + 0.5)


def american_from_prob(prob: float) -> int:
    prob = max(0.02, min(0.98, prob))
    if prob >= 0.5:
        return -int(round(100 * prob / (1 - prob)))
    return int(round(100 * (1 - prob) / prob))


def _model_prob(projection: float, line: float, stat: str, direction: str, avg: float) -> float:
    sigma = stat_sigma(stat, avg)
    over = 1.0 - _normal_cdf((line - projection) / sigma)
    prob = over if direction == "over" else 1.0 - over
    return max(0.05, min(0.95, prob))


async def _candidate_angles(game: GameSummary, ctx: dict) -> List[dict]:
    """Union of Gemini prop angles and ESPN season leaders for the matchup."""
    angles = list(ctx.get("prop_angles") or [])
    seen = {((a.get("player") or "").lower(), a.get("stat")) for a in angles}
    for ldr in await espn.game_leaders(
        game.sport, game.id, game.home_team, game.away_team, game.start_time
    ):
        if not ldr.get("avg"):
            continue
        key = (ldr["player"].lower(), ldr["stat"])
        if key in seen:
            continue
        seen.add(key)
        angles.append(
            {"player": ldr["player"], "stat": ldr["stat"], "direction": "over", "confidence": 0.5}
        )
    return angles


async def _player_averages_for_angles(
    game: GameSummary, angles: List[dict]
) -> List[dict]:
    """Resolve angles to ESPN averages: [{player, player_id, stat, direction, confidence, avg, minutes}]."""
    out: List[dict] = []
    looked_up: Dict[str, Optional[dict]] = {}
    for angle in angles:
        player = (angle.get("player") or "").strip()
        stat = angle.get("stat")
        direction = angle.get("direction")
        if not player or stat not in STAT_LABEL or direction not in ("over", "under"):
            continue
        if player not in looked_up:
            looked_up[player] = await player_stats.player_season_averages(
                game.sport, game.home_team, game.away_team, player
            )
        averages = looked_up[player]
        if not averages or not averages.get(stat):
            continue
        out.append(
            {
                "player": player,
                "player_id": averages.get("player_id"),
                "stat": stat,
                "direction": direction,
                "confidence": float(angle.get("confidence", 0.0)),
                "avg": float(averages[stat]),
                "minutes": float(averages.get("minutes") or 0.0),
            }
        )
    return out


def _base_leg(game: GameSummary, cand: dict, *, line: float, side: str) -> dict:
    label = STAT_LABEL[cand["stat"]]
    return {
        "game_id": game.id,
        "sport": game.sport,
        "matchup": f"{game.away_team} @ {game.home_team}",
        "market": "player_prop",
        "selection": f"{cand['player']} {side.title()} {line:g} {label}",
        "model_source": "model",
        "player": cand["player"],
        "player_id": str(cand["player_id"]) if cand.get("player_id") is not None else None,
        "stat": cand["stat"],
        "prop_line": line,
        "prop_side": side,
    }


async def suggest_prop_anchors(
    game: GameSummary, ctx: dict, *, max_props: int = 3
) -> List[dict]:
    """Build prop anchor legs from cached Gemini angles + player averages."""
    # Soccer (World Cup): model-derived goalscorer/shots props from the Poisson
    # goals model + roster positions — ESPN has no soccer player averages.
    if espn.is_soccer(game.sport):
        return await soccer_props.suggest_soccer_prop_anchors(game, ctx, max_props=max_props)

    if game.sport != "nba":
        return []

    candidates = await _player_averages_for_angles(game, await _candidate_angles(game, ctx))

    legs: List[dict] = []
    for cand in candidates:
        avg, stat, direction, conf = cand["avg"], cand["stat"], cand["direction"], cand["confidence"]
        line = _round_half(avg)
        # Gemini confidence nudges the projection in its stated direction.
        projection = avg + (conf * 0.15 * avg) * (1 if direction == "over" else -1)
        win_prob = round(_model_prob(projection, line, stat, direction, avg), 4)
        implied = round(_implied(PLACEHOLDER_ODDS), 4)
        edge = round(win_prob - implied, 4)
        if edge <= 0:
            continue

        label = STAT_LABEL[stat]
        legs.append(
            {
                **_base_leg(game, cand, line=line, side=direction),
                "odds_american": PLACEHOLDER_ODDS,
                "implied_prob": implied,
                "win_probability": win_prob,
                "confidence": round(min(0.9, 0.4 + conf * 0.5), 4),
                "score": round(edge * 3 + conf, 4),
                "edge": edge,
                "rationale": (
                    f"Season avg {avg:.1f} {label.lower()}; model projects {direction} "
                    f"{line:g} (~{win_prob:.0%}). Line is model-derived — confirm your book."
                ),
            }
        )

    legs.sort(key=lambda x: x["score"], reverse=True)
    return legs[:max_props]


async def prop_parlay_candidates(game: GameSummary, ctx: dict, *, risk: str) -> List[dict]:
    """High-probability alt-line over legs for the props-only parlay mode.

    One leg per (player, stat): the line sits ``z`` standard deviations below
    the (confidence-nudged) projection, snapped to a .5 line, with odds derived
    from the model probability plus typical vig. Unders are skipped — stacking
    is about volume stats clearing a low bar, not fade spots.
    """
    if game.sport != "nba":
        return []

    z = RISK_Z.get(risk, RISK_Z["balanced"])
    min_prob = RISK_MIN_PROB.get(risk, RISK_MIN_PROB["balanced"])
    candidates = await _player_averages_for_angles(game, await _candidate_angles(game, ctx))

    legs: List[dict] = []
    seen = set()
    for cand in candidates:
        avg, stat, conf = cand["avg"], cand["stat"], cand["confidence"]
        key = (cand["player"].lower(), stat)
        if key in seen:
            continue
        seen.add(key)
        if avg < STAT_MIN_AVG[stat] or cand["minutes"] < MIN_MINUTES:
            continue

        sigma = stat_sigma(stat, avg)
        # An "under" angle means the research pass expects a down game — shade
        # the projection down instead of flipping the bet.
        projection = avg + (conf * 0.15 * avg) * (1 if cand["direction"] == "over" else -1)
        line = half_line(projection - z * sigma)
        if line >= projection:  # sanity: the alt line must sit below the projection
            continue
        win_prob = round(_model_prob(projection, line, stat, "over", avg), 4)
        if win_prob < min_prob:
            continue

        odds = american_from_prob(min(0.96, win_prob + PROP_VIG))
        implied = round(_implied(odds), 4)
        label = STAT_LABEL[stat]
        legs.append(
            {
                **_base_leg(game, cand, line=line, side="over"),
                "odds_american": odds,
                "implied_prob": implied,
                "win_probability": win_prob,
                "confidence": round(min(0.9, 0.5 + conf * 0.4), 4),
                "score": round(win_prob + conf * 0.1, 4),
                "edge": round(win_prob - implied, 4),
                "rationale": (
                    f"Averages {avg:.1f} {label.lower()}; needs only {line:g} "
                    f"(~{win_prob:.0%} by the model). Odds estimated with book vig — "
                    f"confirm the alt line at your book."
                ),
            }
        )

    legs.sort(key=lambda x: (x["score"], x["win_probability"]), reverse=True)
    return legs
