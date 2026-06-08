from __future__ import annotations

import itertools
import random
from datetime import datetime, timezone
from typing import Dict, List, Tuple

from app.models.schemas import GameSummary, ParlayRequest, ParlayResponse, PickLeg, RiskLevel
from app.services.ai_assistant import explain_parlay
from app.services.context import get_game_context
from app.services.odds import get_todays_games

RISK_PROFILES: Dict[RiskLevel, dict] = {
    "safe": {
        "target_implied": 0.58,
        "min_leg_implied": 0.52,
        "max_leg_implied": 0.72,
        "min_combined_american": 180,
        "max_combined_implied": 0.32,
        "min_win_prob": 0.22,
        "context_weight": 2.2,
        "price_weight": 1.6,
        "label": "Safe",
    },
    "balanced": {
        "target_implied": 0.48,
        "min_leg_implied": 0.43,
        "max_leg_implied": 0.60,
        "min_combined_american": 350,
        "max_combined_implied": 0.20,
        "min_win_prob": 0.14,
        "context_weight": 1.6,
        "price_weight": 1.0,
        "label": "Balanced",
    },
    "bold": {
        "target_implied": 0.40,
        "min_leg_implied": 0.36,
        "max_leg_implied": 0.52,
        "min_combined_american": 650,
        "max_combined_implied": 0.11,
        "min_win_prob": 0.07,
        "context_weight": 1.2,
        "price_weight": 0.7,
        "label": "Bold",
    },
}


def american_to_implied(odds: int) -> float:
    if odds > 0:
        return 100 / (odds + 100)
    return abs(odds) / (abs(odds) + 100)


def combine_american_odds(legs: List[PickLeg]) -> Tuple[int, float]:
    decimal = 1.0
    for leg in legs:
        if leg.odds_american > 0:
            decimal *= 1 + leg.odds_american / 100
        else:
            decimal *= 1 + 100 / abs(leg.odds_american)
    combined_implied = 1 / decimal
    if decimal >= 2:
        combined = int(round((decimal - 1) * 100))
    else:
        combined = int(round(-100 / (decimal - 1)))
    return combined, combined_implied


def payout_on_100(american: int) -> float:
    if american > 0:
        return round(american, 2)
    return round(100 * (100 / abs(american)), 2)


def _win_probability(implied: float, ctx: dict, *, home_side: bool, market: str) -> Tuple[float, float]:
    """Estimate true win chance above market implied using context signals."""
    boost = 0.0
    line_move = ctx.get("line_move", 0.0)
    if home_side:
        boost += line_move * 1.5
        boost -= ctx.get("injury_penalty_home", 0.0) * 1.2
        boost += ctx.get("injury_penalty_away", 0.0) * 0.8
        boost += ctx.get("news_sentiment", 0.0) * 1.0
    else:
        boost -= line_move * 1.5
        boost -= ctx.get("injury_penalty_away", 0.0) * 1.2
        boost += ctx.get("injury_penalty_home", 0.0) * 0.8
        boost -= ctx.get("news_sentiment", 0.0) * 1.0

    if market == "total":
        boost += ctx.get("weather_factor", 0.0) * 0.8

    win_prob = max(0.05, min(0.92, implied + boost))
    confidence = min(0.95, 0.45 + abs(boost) * 2 + (0.1 if abs(line_move) > 0.02 else 0))
    return round(win_prob, 4), round(confidence, 4)


def _score_leg(
    implied: float,
    win_prob: float,
    confidence: float,
    profile: dict,
    *,
    favorite: bool,
) -> float:
    price_score = 1 - abs(implied - profile["target_implied"]) * profile["price_weight"]
    edge = (win_prob - implied) * profile["context_weight"] * 3
    conf_bonus = confidence * 0.25
    fav_penalty = -0.06 if favorite and implied > 0.65 else 0
    return round(price_score + edge + conf_bonus + fav_penalty, 4)


def _rationale(team: str, ctx: Dict, label: str, win_prob: float, implied: float) -> str:
    parts = [f"{label} on {team}."]
    edge = win_prob - implied
    if edge > 0.03:
        parts.append(f"Model win rate ~{win_prob:.0%} vs {implied:.0%} implied (+{edge:.0%} edge).")
    if ctx.get("line_move", 0) > 0.02:
        parts.append("Sharp line movement supports this side.")
    if ctx.get("injury_penalty_home", 0) > 0.05 or ctx.get("injury_penalty_away", 0) > 0.05:
        parts.append("Injury report tilts the matchup.")
    if ctx.get("news_sentiment", 0) > 0.02:
        parts.append("Recent news flow is favorable.")
    elif ctx.get("news_sentiment", 0) < -0.02:
        parts.append("News headwinds priced into the other side.")
    return " ".join(parts)


def _total_rationale(game: GameSummary, ctx: Dict, side: str, win_prob: float) -> str:
    if side == "over" and ctx.get("weather_factor", 0) > 0.02:
        return f"Weather favors scoring; Over {game.total} (~{win_prob:.0%} model win rate)."
    if side == "under" and ctx.get("weather_factor", 0) < -0.02:
        return f"Wind/weather suppresses scoring; Under {game.total} (~{win_prob:.0%} model win rate)."
    return f"Matchup + market price favor {side} {game.total} (~{win_prob:.0%} model win rate)."


async def _candidate_legs(game: GameSummary, profile: dict) -> List[dict]:
    ctx = await get_game_context(game)
    legs: List[dict] = []
    matchup = f"{game.away_team} @ {game.home_team}"

    def maybe_add(**kwargs):
        implied = kwargs["implied_prob"]
        if implied < profile["min_leg_implied"] or implied > profile["max_leg_implied"]:
            return
        legs.append(kwargs)

    if game.moneyline_home is not None:
        imp = american_to_implied(game.moneyline_home)
        win_prob, confidence = _win_probability(imp, ctx, home_side=True, market="moneyline")
        maybe_add(
            game_id=game.id,
            sport=game.sport,
            matchup=matchup,
            market="moneyline",
            selection=game.home_team,
            odds_american=game.moneyline_home,
            implied_prob=imp,
            win_probability=win_prob,
            confidence=confidence,
            score=_score_leg(imp, win_prob, confidence, profile, favorite=imp > 0.55),
            rationale=_rationale(game.home_team, ctx, "Home ML", win_prob, imp),
        )

    if game.moneyline_away is not None:
        imp = american_to_implied(game.moneyline_away)
        win_prob, confidence = _win_probability(imp, ctx, home_side=False, market="moneyline")
        maybe_add(
            game_id=game.id,
            sport=game.sport,
            matchup=matchup,
            market="moneyline",
            selection=game.away_team,
            odds_american=game.moneyline_away,
            implied_prob=imp,
            win_probability=win_prob,
            confidence=confidence,
            score=_score_leg(imp, win_prob, confidence, profile, favorite=imp > 0.55),
            rationale=_rationale(game.away_team, ctx, "Away ML", win_prob, imp),
        )

    if game.spread_home is not None:
        for side, odds, selection, home_side in (
            ("home", game.spread_home_odds, f"{game.home_team} {game.spread_home:+.1f}", True),
            ("away", game.spread_away_odds, f"{game.away_team} {-game.spread_home:+.1f}", False),
        ):
            imp = american_to_implied(odds)
            win_prob, confidence = _win_probability(imp, ctx, home_side=home_side, market="spread")
            maybe_add(
                game_id=game.id,
                sport=game.sport,
                matchup=matchup,
                market="spread",
                selection=selection,
                odds_american=odds,
                implied_prob=imp,
                win_probability=win_prob,
                confidence=confidence,
                score=_score_leg(imp, win_prob, confidence, profile, favorite=False) + 0.03,
                rationale=f"Spread value on {selection}; model ~{win_prob:.0%} cover probability.",
            )

    if game.total is not None:
        weather = ctx.get("weather_factor", 0.0)
        over_first = weather >= 0
        sides = [
            ("over", game.over_odds, f"Over {game.total}"),
            ("under", game.under_odds, f"Under {game.total}"),
        ]
        if not over_first:
            sides.reverse()
        for side, odds, selection in sides:
            imp = american_to_implied(odds)
            win_prob, confidence = _win_probability(imp, ctx, home_side=True, market="total")
            maybe_add(
                game_id=game.id,
                sport=game.sport,
                matchup=matchup,
                market="total",
                selection=selection,
                odds_american=odds,
                implied_prob=imp,
                win_probability=win_prob,
                confidence=confidence,
                score=_score_leg(imp, win_prob, confidence, profile, favorite=False),
                rationale=_total_rationale(game, ctx, side, win_prob),
            )

    return legs


def _best_per_game(candidates: List[dict]) -> List[dict]:
    by_game: Dict[str, List[dict]] = {}
    for c in candidates:
        by_game.setdefault(c["game_id"], []).append(c)
    return [max(legs, key=lambda x: (x["score"], x["win_probability"])) for legs in by_game.values()]


def _parlay_metrics(legs: List[dict]) -> Tuple[int, float, float]:
    pick_legs = [PickLeg(**c) for c in legs]
    combined_american, combined_implied = combine_american_odds(pick_legs)
    win_prob = 1.0
    for leg in legs:
        win_prob *= leg["win_probability"]
    return combined_american, combined_implied, round(win_prob, 4)


def _select_parlay(candidates: List[dict], leg_count: int, risk: RiskLevel) -> List[dict]:
    profile = RISK_PROFILES[risk]
    pool = sorted(_best_per_game(candidates), key=lambda x: (x["score"], x["win_probability"]), reverse=True)

    if len(pool) < leg_count:
        leg_count = len(pool)
    if leg_count < 2:
        raise ValueError("Not enough quality legs today — try another sport or check back later")

    top = pool[: min(len(pool), leg_count + 4)]
    best_combo: List[dict] = []
    best_key = (-1.0, -1.0, -1)

    for combo in itertools.combinations(top, leg_count):
        combined_american, combined_implied, win_prob = _parlay_metrics(list(combo))
        if combined_american < profile["min_combined_american"]:
            continue
        if combined_implied > profile["max_combined_implied"]:
            continue
        if win_prob < profile["min_win_prob"]:
            continue
        avg_conf = sum(c["confidence"] for c in combo) / len(combo)
        key = (win_prob, avg_conf, combined_american)
        if key > best_key:
            best_key = key
            best_combo = list(combo)

    if best_combo:
        return best_combo

    # Relax payout floor slightly but still maximize win probability
    fallback_key = (-1.0, -1.0)
    fallback: List[dict] = []
    for combo in itertools.combinations(top, leg_count):
        combined_american, combined_implied, win_prob = _parlay_metrics(list(combo))
        if combined_implied > profile["max_combined_implied"] * 1.15:
            continue
        key = (win_prob, combined_american)
        if key > fallback_key:
            fallback_key = key
            fallback = list(combo)

    if fallback:
        return fallback

    # Last resort: highest win-prob legs from distinct games
    head = pool[0]
    tail = pool[1:]
    random.shuffle(tail)
    return [head] + tail[: leg_count - 1]


SGP_PAYOUT_FLOOR: Dict[RiskLevel, int] = {"safe": 140, "balanced": 200, "bold": 280}


def _sgp_profile(profile: dict) -> dict:
    """Wider leg window for same-game legs (spreads/totals are usually -110)."""
    return {
        **profile,
        "min_leg_implied": 0.38,
        "max_leg_implied": 0.68,
    }


def _select_same_game_parlay(candidates: List[dict], leg_count: int, risk: RiskLevel) -> List[dict]:
    profile = RISK_PROFILES[risk]
    pool = sorted(candidates, key=lambda x: (x["score"], x["win_probability"]), reverse=True)
    if len(pool) < 2:
        raise ValueError("Not enough markets for a same-game parlay on this matchup")

    leg_count = min(leg_count, len(pool))

    # Prefer best leg per market (ML, spread, total) for variety
    by_market: Dict[str, dict] = {}
    for c in pool:
        if c["market"] not in by_market:
            by_market[c["market"]] = c
    diverse = sorted(
        by_market.values(),
        key=lambda x: (x["score"], x["win_probability"]),
        reverse=True,
    )

    best_combo: List[dict] = []
    best_key = (-1.0, -1.0)
    search_pool = pool if len(pool) <= 6 else diverse + [c for c in pool if c not in diverse]

    for combo in itertools.combinations(search_pool, leg_count):
        markets = [c["market"] for c in combo]
        variety_bonus = len(set(markets)) * 0.02
        combined_american, combined_implied, win_prob = _parlay_metrics(list(combo))
        if combined_american < SGP_PAYOUT_FLOOR[risk]:
            continue
        key = (win_prob + variety_bonus, combined_american)
        if key > best_key:
            best_key = key
            best_combo = list(combo)

    if best_combo:
        return best_combo

    return diverse[:leg_count] if len(diverse) >= leg_count else pool[:leg_count]


async def _generate_same_game_parlay(req: ParlayRequest) -> ParlayResponse:
    profile = _sgp_profile(RISK_PROFILES[req.risk])
    games = await get_todays_games(req.sport)
    game = next((g for g in games if g.id == req.game_id), None)
    if not game:
        raise ValueError("Game not found — refresh the board and pick a listed matchup")

    candidates = await _candidate_legs(game, profile)
    if len(candidates) < 2:
        raise ValueError(
            "This game doesn't have enough markets (need ML, spread, and/or total)"
        )

    chosen = _select_same_game_parlay(candidates, req.legs, req.risk)
    legs = [PickLeg(**c) for c in chosen]
    combined_american, combined_implied, estimated_win_prob = _parlay_metrics(chosen)
    matchup = f"{game.away_team} @ {game.home_team}"

    summary = (
        f"{RISK_PROFILES[req.risk]['label']} {len(legs)}-leg same-game parlay on {matchup}. "
        f"Stacks ML, spread, and total picks for this matchup (~{estimated_win_prob:.1%} est. win, "
        f"{format(combined_american, '+d')}). Legs are correlated — higher risk than multi-game slips."
    )

    return ParlayResponse(
        legs=legs,
        combined_american=combined_american,
        combined_implied_prob=round(combined_implied, 4),
        estimated_win_prob=estimated_win_prob,
        payout_on_100=payout_on_100(combined_american),
        risk=req.risk,
        same_game=True,
        summary=summary,
        generated_at=datetime.now(timezone.utc),
    )


async def generate_parlay(req: ParlayRequest) -> ParlayResponse:
    if req.game_id:
        response = await _generate_same_game_parlay(req)
        insight = await explain_parlay(response)
        if insight:
            response.ai_insight = insight
        return response

    profile = RISK_PROFILES[req.risk]
    games = await get_todays_games(req.sport)
    candidates: List[dict] = []
    for game in games:
        candidates.extend(await _candidate_legs(game, profile))

    if not candidates:
        raise ValueError("No qualifying legs for this risk level — try Balanced or another sport")

    chosen = _select_parlay(candidates, req.legs, req.risk)
    legs = [PickLeg(**c) for c in chosen]
    combined_american, combined_implied, estimated_win_prob = _parlay_metrics(chosen)

    sports = ", ".join(sorted({l.sport.upper() for l in legs}))
    summary = (
        f"{profile['label']} {len(legs)}-leg parlay across {sports}. "
        f"Built to maximize win probability (~{estimated_win_prob:.1%}) while targeting "
        f"{format(combined_american, '+d')} payout — uncorrelated legs only, no same-game conflicts."
    )

    response = ParlayResponse(
        legs=legs,
        combined_american=combined_american,
        combined_implied_prob=round(combined_implied, 4),
        estimated_win_prob=estimated_win_prob,
        payout_on_100=payout_on_100(combined_american),
        risk=req.risk,
        same_game=False,
        summary=summary,
        generated_at=datetime.now(timezone.utc),
    )

    insight = await explain_parlay(response)
    if insight:
        response.ai_insight = insight

    return response
