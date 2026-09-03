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
import statistics
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

# Where the line sits, in standard deviations BELOW the player's own average.
# The engine's whole judgment is "does he hit his usual number tonight or not" —
# never "does he have a big night", which is noise we cannot price. Deep alt
# lines were dropped: a 90% line only exists at ~-1400, a price no book beats,
# so those legs are unplayable however safe they look.
RISK_Z = {"safe": 0.60, "balanced": 0.30, "bold": 0.0}  # bold sits AT the average
RISK_MIN_PROB = {"safe": 0.66, "balanced": 0.57, "bold": 0.50}

# Hard guard rails on line placement.
MAX_Z_BELOW = 1.0  # never post a line deeper than 1 sd under the average
# ...and never above it: asking a player to beat his own average is the
# overperformance bet we explicitly do not take.

# Empirical game-log settings.
LOG_PRIOR_WEIGHT = 6.0  # pseudo-games of shrinkage toward the normal-model prior

# A stat whose game-to-game swing is this large relative to its mean is too
# erratic for its average to mean anything — skip rather than dress up noise.
# (Wembanyama's real 2025-26 numbers: points cv 0.39, rebounds 0.47, 3pm 0.72.)
MAX_CV = {"points": 0.55, "rebounds": 0.62, "assists": 0.68, "3pm": 0.80}

# --- The part the stat model can't see -------------------------------------
# No prop is ever a lock. A listed player can be a late scratch, pick up two
# early fouls, get ejected, or sit the whole 4th in a blowout — none of which
# the season-average distribution knows about. This is a hard ceiling on how
# confident ANY leg is allowed to be, and it is the single biggest reason
# "guaranteed" prop parlays lose.
BASE_AVAILABILITY = 0.975  # ~2.5% of nights the leg dies for non-stat reasons
MAX_LEG_PROB = 0.97

# Injury statuses that disqualify a prop outright — never stack these.
BLOCKING_STATUS = ("out", "injured reserve", "suspension", "doubtful", "not with team")
# Listed but uncertain: playable, but the scratch risk is far above baseline.
RISKY_STATUS = ("questionable", "day-to-day", "game-time decision")
RISKY_AVAILABILITY = 0.82


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


def line_at_or_below(value: float) -> float:
    """Largest .5 line that does NOT exceed value.

    half_line() snaps to the nearest .5 and can round up (27.0 -> 27.5), which
    would quietly turn a "hit your average" bet into a "beat your average" bet.
    This is the guard rail that keeps the line at or under the average.
    """
    candidate = math.floor(value) + 0.5
    if candidate > value:
        candidate -= 1.0
    return max(0.5, candidate)


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


def empirical_prob(values: List[float], line: float, prior: float) -> float:
    """How often he ACTUALLY cleared this line, shrunk toward the model prior.

    A raw 20/20 would read as certainty; the prior keeps a thin sample honest
    while a full season of games effectively overrides it.
    """
    n = len(values)
    if n == 0:
        return prior
    hits = sum(1 for v in values if v > line)
    return (hits + LOG_PRIOR_WEIGHT * prior) / (n + LOG_PRIOR_WEIGHT)


def distribution_for(series: List[float], season_avg: float, stat: str):
    """(mean, sigma, source) — empirical when the log is deep enough."""
    if len(series) >= player_stats.MIN_LOG_GAMES:
        mean = statistics.mean(series)
        sigma = statistics.pstdev(series)
        if mean > 0 and sigma > 0:
            return mean, sigma, "gamelog"
    return season_avg, stat_sigma(stat, season_avg), "season_avg"


def player_availability(player: str, ctx: dict) -> Optional[float]:
    """Probability the player actually takes the floor enough to have a shot.

    Returns None when the player is ruled out (leg must be dropped). Otherwise a
    multiplier applied to the stat-model probability, so a 95% stat line on a
    questionable player reports ~78%, not 95%.
    """
    last = player.lower().split()[-1] if player else ""
    for side_key in ("injuries_home", "injuries_away"):
        for inj in ctx.get(side_key) or []:
            name = (inj.get("player") or "").lower()
            if not name or not (name == player.lower() or (last and last in name)):
                continue
            status = (inj.get("status") or "").lower()
            if any(b in status for b in BLOCKING_STATUS):
                return None
            if any(r in status for r in RISKY_STATUS):
                return RISKY_AVAILABILITY
    return BASE_AVAILABILITY


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
    """Player-stat legs set AT or slightly BELOW the player's own average.

    The engine makes exactly one call per leg: does this player hit his usual
    number tonight, or underperform it? Overperformance is never bet — the line
    is capped at his average, so no leg needs a career night to cash. Guard
    rails keep the line inside [avg - 1 sd, avg]; deeper lines only exist at
    prices no book will offer, and shallower ones are just the market's own bet.

    Where a game log is available the hit rate is empirical — how often he
    actually cleared this number — rather than assumed from a bell curve, which
    matters most for exactly the player/stat pairs that are worth betting.
    """
    if game.sport != "nba":
        return []

    z = RISK_Z.get(risk, RISK_Z["balanced"])
    min_prob = RISK_MIN_PROB.get(risk, RISK_MIN_PROB["balanced"])
    candidates = await _player_averages_for_angles(game, await _candidate_angles(game, ctx))

    legs: List[dict] = []
    seen = set()
    for cand in candidates:
        season_avg, stat, conf = cand["avg"], cand["stat"], cand["confidence"]
        key = (cand["player"].lower(), stat)
        if key in seen:
            continue
        seen.add(key)
        if season_avg < STAT_MIN_AVG[stat] or cand["minutes"] < MIN_MINUTES:
            continue

        availability = player_availability(cand["player"], ctx)
        if availability is None:  # ruled out — never stack him
            continue

        log = await player_stats.player_game_log(game.sport, str(cand.get("player_id") or ""))
        series = player_stats.stat_series(log, stat)
        mean, sigma, source = distribution_for(series, season_avg, stat)

        # Too erratic for his own average to carry information.
        if mean <= 0 or sigma / mean > MAX_CV[stat]:
            continue

        # The engine's decision. A research angle pointing DOWN shades the
        # expectation down; one pointing up is deliberately ignored, because
        # overperformance is the variance we refuse to price.
        lean = -(conf * 0.12) if cand["direction"] == "under" else 0.0
        target = mean * (1 + lean)

        line = half_line(target - z * sigma)
        line = max(line, half_line(mean - MAX_Z_BELOW * sigma))  # not too deep
        line = min(line, line_at_or_below(mean))  # never above his average
        if line < 0.5:
            continue

        prior = _model_prob(target, line, stat, "over", mean)
        stat_prob = empirical_prob(series, line, prior)
        win_prob = round(min(MAX_LEG_PROB, stat_prob * availability), 4)
        if win_prob < min_prob:
            continue

        odds = american_from_prob(min(0.96, win_prob + PROP_VIG))
        implied = round(_implied(odds), 4)
        fair_odds = american_from_prob(win_prob)
        label = STAT_LABEL[stat]
        if source == "gamelog":
            hits = sum(1 for v in series if v > line)
            evidence = f"cleared {line:g} in {hits} of his last {len(series)}"
        else:
            evidence = f"season avg {mean:.1f}, no game log yet"
        legs.append(
            {
                **_base_leg(game, cand, line=line, side="over"),
                "odds_american": odds,
                "implied_prob": implied,
                "win_probability": win_prob,
                "fair_odds_american": fair_odds,
                "availability": round(availability, 4),
                "stat_source": source,
                "sample_games": len(series),
                "confidence": round(min(0.9, 0.5 + conf * 0.4), 4),
                "score": round(win_prob + conf * 0.1, 4),
                "edge": round(win_prob - implied, 4),
                "rationale": (
                    f"Averages {mean:.1f} {label.lower()}; {evidence} "
                    f"(~{win_prob:.0%} after scratch/blowout risk). Line sits at or under "
                    f"his own average — no career night required. "
                    f"Worth playing only better than {fair_odds:+d} at your book."
                ),
            }
        )

    legs.sort(key=lambda x: (x["score"], x["win_probability"]), reverse=True)
    return legs
