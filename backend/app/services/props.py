"""Player-prop suggestions (anchors).

Free data sources don't expose live player-prop *lines* (those sit behind paid
odds tiers), so we don't fabricate book odds and feed them into bankroll math.
Instead props are surfaced as optional *anchors*: we take Gemini's prop angle,
ground it in the player's real ESPN season average, and project an over/under
lean with a transparent normal model. The UI labels these as model projections
to confirm against the user's book.

When a real prop-line source is added later, these become fully priced legs.
"""
from __future__ import annotations

import math
from typing import List

from app.models.schemas import GameSummary
from app.services import espn, player_stats

# Standard deviation of a single-game stat, used for over/under probability.
STAT_STD = {"points": 9.0, "rebounds": 3.5, "assists": 3.0, "3pm": 1.6}
STAT_LABEL = {"points": "Points", "rebounds": "Rebounds", "assists": "Assists", "3pm": "3PM"}

PLACEHOLDER_ODDS = -115  # typical prop juice; implied ~53.5%


def _implied(odds: int) -> float:
    return abs(odds) / (abs(odds) + 100) if odds < 0 else 100 / (odds + 100)


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _round_half(value: float) -> float:
    return round(value * 2) / 2


def _model_prob(projection: float, line: float, stat: str, direction: str) -> float:
    sigma = STAT_STD.get(stat, 5.0)
    over = 1.0 - _normal_cdf((line - projection) / sigma)
    prob = over if direction == "over" else 1.0 - over
    return max(0.05, min(0.95, prob))


async def suggest_prop_anchors(
    game: GameSummary, ctx: dict, *, max_props: int = 3
) -> List[dict]:
    """Build prop anchor legs from cached Gemini angles + player averages."""
    if game.sport != "nba":
        return []

    angles = ctx.get("prop_angles") or []
    # Fallback: when the research pass produced no angles (e.g. Gemini offline or
    # rate-limited), derive candidates from ESPN season leaders — free, no AI.
    if not angles:
        angles = [
            {"player": ldr["player"], "stat": ldr["stat"], "direction": "over", "confidence": 0.5}
            for ldr in await espn.game_leaders(
                game.sport, game.id, game.home_team, game.away_team, game.start_time
            )
            if ldr.get("avg")
        ]
    if not angles:
        return []

    legs: List[dict] = []
    for angle in angles:
        player = (angle.get("player") or "").strip()
        stat = angle.get("stat")
        direction = angle.get("direction")
        if not player or stat not in STAT_STD or direction not in ("over", "under"):
            continue

        averages = await player_stats.player_season_averages(
            game.sport, game.home_team, game.away_team, player
        )
        if not averages:
            continue
        avg = averages.get(stat)
        if not avg:
            continue
        avg = float(avg)
        player_id = averages.get("player_id")
        line = _round_half(avg)
        # Gemini confidence nudges the projection in its stated direction.
        conf = float(angle.get("confidence", 0.0))
        projection = avg + (conf * 0.15 * avg) * (1 if direction == "over" else -1)
        win_prob = round(_model_prob(projection, line, stat, direction), 4)
        implied = round(_implied(PLACEHOLDER_ODDS), 4)
        edge = round(win_prob - implied, 4)
        if edge <= 0:
            continue

        label = STAT_LABEL[stat]
        selection = f"{player} {direction.title()} {line:g} {label}"
        legs.append(
            {
                "game_id": game.id,
                "sport": game.sport,
                "matchup": f"{game.away_team} @ {game.home_team}",
                "market": "player_prop",
                "selection": selection,
                "odds_american": PLACEHOLDER_ODDS,
                "implied_prob": implied,
                "win_probability": win_prob,
                "confidence": round(min(0.9, 0.4 + conf * 0.5), 4),
                "score": round(edge * 3 + conf, 4),
                "edge": edge,
                "model_source": "model",
                "player": player,
                "player_id": str(player_id) if player_id is not None else None,
                "stat": stat,
                "prop_line": line,
                "prop_side": direction,
                "rationale": (
                    f"Season avg {avg:.1f} {label.lower()}; model projects {direction} "
                    f"{line:g} (~{win_prob:.0%}). Line is model-derived — confirm your book."
                ),
            }
        )

    legs.sort(key=lambda x: x["score"], reverse=True)
    return legs[:max_props]
