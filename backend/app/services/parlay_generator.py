from __future__ import annotations

import itertools
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from app.models.schemas import GameSummary, ParlayRequest, ParlayResponse, PickLeg, RiskLevel
from app.config import settings
from app.services import power_model, props
from app.services.ai_assistant import explain_parlay
from app.services.calibration import calibration_adjustment, ensure_calibration_loaded
from app.services.context import get_game_context
from app.services.odds import get_todays_games
from app.services.settlement import parse_matchup, parse_spread, team_match

# Risk profiles now key off MODEL edge and win probability, not implied-odds
# bands. Safe favors high win probability; Bold favors edge/payout. Odds are
# used only for payout math, never to drive the pick.
RISK_PROFILES: Dict[RiskLevel, dict] = {
    "safe": {
        "min_leg_win": 0.55,
        "min_combined_win": 0.18,
        "w_win": 1.0,
        "w_edge": 1.2,
        "label": "Safe",
    },
    "balanced": {
        "min_leg_win": 0.48,
        "min_combined_win": 0.10,
        "w_win": 0.6,
        "w_edge": 2.2,
        "label": "Balanced",
    },
    "bold": {
        "min_leg_win": 0.40,
        "min_combined_win": 0.05,
        "w_win": 0.3,
        "w_edge": 3.0,
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


def _confidence(edge: float, model_source: str, extra: float = 0.0) -> float:
    base = 0.55 if model_source == "model" else 0.38
    return round(min(0.95, base + min(0.3, abs(edge) * 4) + extra), 4)


async def _maybe_calibrate(win_prob: float, sport: str, market: str, risk: RiskLevel) -> float:
    await ensure_calibration_loaded()
    adj = calibration_adjustment(sport, market, risk, win_prob)
    if adj:
        win_prob = max(0.05, min(0.95, win_prob + adj))
    return round(win_prob, 4)


def _score(win_prob: float, edge: float, confidence: float, profile: dict, bonus: float = 0.0) -> float:
    return round(
        win_prob * profile["w_win"] + edge * profile["w_edge"] + confidence * 0.3 + bonus, 4
    )


async def _candidate_legs(game: GameSummary, profile: dict, risk: RiskLevel) -> List[dict]:
    ctx = await get_game_context(game)
    matchup = f"{game.away_team} @ {game.home_team}"
    home_win_prob: Optional[float] = ctx.get("home_win_prob")
    expected_margin = float(ctx.get("expected_margin", 0.0))
    projected_total = ctx.get("projected_total")
    model_source = ctx.get("model_source", "market_fallback")
    legs: List[dict] = []

    async def add(market: str, selection: str, odds: int, win_prob: float, *, bonus=0.0, rationale="", extra_conf=0.0, **kw):
        implied = american_to_implied(odds)
        win_prob = await _maybe_calibrate(win_prob, game.sport, market, risk)
        edge = round(win_prob - implied, 4)
        conf = _confidence(edge, model_source, extra_conf)
        legs.append(
            {
                "game_id": game.id,
                "sport": game.sport,
                "matchup": matchup,
                "market": market,
                "selection": selection,
                "odds_american": odds,
                "implied_prob": round(implied, 4),
                "win_probability": win_prob,
                "confidence": conf,
                "edge": edge,
                "model_source": model_source,
                "score": _score(win_prob, edge, conf, profile, bonus),
                "rationale": rationale or _edge_rationale(selection, win_prob, implied, ctx),
                **kw,
            }
        )

    # Moneyline
    if game.moneyline_home is not None:
        wp = home_win_prob if home_win_prob is not None else american_to_implied(game.moneyline_home)
        await add("moneyline", game.home_team, game.moneyline_home, wp)
    if game.moneyline_away is not None:
        wp = (1 - home_win_prob) if home_win_prob is not None else american_to_implied(game.moneyline_away)
        await add("moneyline", game.away_team, game.moneyline_away, wp)

    # Spread
    if game.spread_home is not None:
        for home_side, odds, selection in (
            (True, game.spread_home_odds, f"{game.home_team} {game.spread_home:+.1f}"),
            (False, game.spread_away_odds, f"{game.away_team} {-game.spread_home:+.1f}"),
        ):
            if home_win_prob is not None:
                wp = power_model.spread_cover_prob(
                    game.sport, expected_margin, game.spread_home, home_side=home_side
                )
            else:
                wp = american_to_implied(odds)
            await add("spread", selection, odds, wp, bonus=0.02)

    # Total
    if game.total is not None:
        lean = ctx.get("total_lean", "neutral")
        total_conf = float(ctx.get("total_confidence", 0.0))
        for side, odds, selection in (
            ("over", game.over_odds, f"Over {game.total}"),
            ("under", game.under_odds, f"Under {game.total}"),
        ):
            if projected_total is not None:
                over_p = power_model.over_prob(game.sport, projected_total, game.total)
                wp = over_p if side == "over" else 1 - over_p
            else:
                wp = american_to_implied(odds)
            extra = 0.1 if lean == side and total_conf > 0.4 else 0.0
            await add("total", selection, odds, wp, extra_conf=extra)

    return legs


def _edge_rationale(selection: str, win_prob: float, implied: float, ctx: dict) -> str:
    edge = win_prob - implied
    parts = [f"{selection}: model {win_prob:.0%} vs market {implied:.0%}"]
    if edge > 0.02:
        parts.append(f"(+{edge:.0%} edge).")
    elif edge < -0.02:
        parts.append(f"({edge:.0%} vs market — thin).")
    else:
        parts.append("(in line with market).")
    factors = ctx.get("key_factors") or []
    if factors:
        parts.append(factors[0] + ".")
    if ctx.get("model_source") == "market_fallback":
        parts.append("No team stats available — anchored to market.")
    return " ".join(parts)


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
    pool = _best_per_game(candidates)
    # Prefer legs meeting the risk floor; keep the rest as fallback.
    eligible = [c for c in pool if c["win_probability"] >= profile["min_leg_win"]]
    ranked = sorted(eligible or pool, key=lambda x: (x["score"], x["win_probability"]), reverse=True)

    leg_count = min(leg_count, len(ranked))
    if leg_count < 2:
        raise ValueError("Not enough quality legs today — try another sport or check back later")

    chosen = ranked[:leg_count]
    # If combined win prob is below the floor, swap in higher-prob legs.
    _, _, win_prob = _parlay_metrics(chosen)
    if win_prob < profile["min_combined_win"] and len(ranked) > leg_count:
        by_win = sorted(ranked, key=lambda x: x["win_probability"], reverse=True)
        chosen = by_win[:leg_count]
    return chosen


SGP_MIN_LEGS = 2


def _sgp_combo_is_valid(combo: List[dict]) -> bool:
    """Reject same-game combos with conflicting legs (both sides or impossible margins)."""
    ml_count = 0
    spread_count = 0
    has_over = False
    has_under = False

    for leg in combo:
        market = leg.get("market", "")
        sel = leg.get("selection", "").lower()
        if market == "moneyline":
            ml_count += 1
            if ml_count > 1:
                return False
        elif market == "spread":
            spread_count += 1
            if spread_count > 1:
                return False
        elif market == "total":
            if sel.startswith("over"):
                if has_over or has_under:
                    return False
                has_over = True
            elif sel.startswith("under"):
                if has_over or has_under:
                    return False
                has_under = True

    if not combo:
        return True

    matchup = combo[0].get("matchup", "")
    home_team, away_team = parse_matchup(matchup)
    if not home_team:
        return True

    # Books disallow ML + spread on the same team (correlated / dependent legs)
    ml_side: Optional[str] = None
    spread_side: Optional[str] = None
    for leg in combo:
        if leg.get("market") == "moneyline":
            if team_match(leg.get("selection", ""), home_team):
                ml_side = "home"
            elif team_match(leg.get("selection", ""), away_team):
                ml_side = "away"
        elif leg.get("market") == "spread":
            try:
                team, _ = parse_spread(leg.get("selection", ""))
                if team_match(team, home_team):
                    spread_side = "home"
                elif team_match(team, away_team):
                    spread_side = "away"
            except ValueError:
                return False
    if ml_side and spread_side and ml_side == spread_side:
        return False

    from app.services.settlement import grade_moneyline, grade_spread, grade_total

    for margin in range(-25, 26):
        home_score = 110 + max(margin, 0)
        away_score = 110 + max(-margin, 0)
        all_win = True
        for leg in combo:
            market = leg.get("market", "")
            try:
                if market == "moneyline":
                    result = grade_moneyline(leg["selection"], home_team, away_team, home_score, away_score)
                elif market == "spread":
                    result = grade_spread(leg["selection"], home_team, away_team, home_score, away_score)
                elif market == "total":
                    result = grade_total(leg["selection"], home_score, away_score)
                else:
                    continue
                if result != "win":
                    all_win = False
                    break
            except ValueError:
                all_win = False
                break
        if all_win:
            return True
    return False


def _select_same_game_parlay(candidates: List[dict], leg_count: int, risk: RiskLevel) -> List[dict]:
    pool = sorted(candidates, key=lambda x: (x["score"], x["win_probability"]), reverse=True)
    if len(pool) < 2:
        raise ValueError("Not enough markets for a same-game parlay on this matchup")

    leg_count = min(leg_count, 3, len(pool))

    by_market: Dict[str, dict] = {}
    for c in pool:
        if c["market"] not in by_market:
            by_market[c["market"]] = c
    diverse = sorted(by_market.values(), key=lambda x: (x["score"], x["win_probability"]), reverse=True)

    best_combo: List[dict] = []
    best_key = (-1.0, -1.0)
    search_pool = pool if len(pool) <= 6 else diverse + [c for c in pool if c not in diverse]

    for combo in itertools.combinations(search_pool, leg_count):
        if not _sgp_combo_is_valid(combo):
            continue
        markets = [c["market"] for c in combo]
        variety_bonus = len(set(markets)) * 0.02
        _, _, win_prob = _parlay_metrics(list(combo))
        avg_edge = sum(c["edge"] for c in combo) / len(combo)
        key = (win_prob + variety_bonus + avg_edge, win_prob)
        if key > best_key:
            best_key = key
            best_combo = list(combo)

    if best_combo:
        return best_combo

    if len(diverse) >= leg_count and _sgp_combo_is_valid(diverse[:leg_count]):
        return diverse[:leg_count]

    chosen: List[dict] = []
    for c in pool:
        if len(chosen) >= leg_count:
            break
        if _sgp_combo_is_valid(chosen + [c]):
            chosen.append(c)
    if len(chosen) >= 2:
        return chosen

    raise ValueError("Could not build a valid same-game parlay — try 2 or 3 legs (ML, spread, total only)")


async def _collect_anchors(games: List[GameSummary], used_keys: set) -> List[PickLeg]:
    if not settings.enable_player_props:
        return []
    anchors: List[dict] = []
    for game in games:
        ctx = await get_game_context(game)
        for leg in await props.suggest_prop_anchors(game, ctx):
            key = (leg["game_id"], leg.get("player"), leg.get("stat"))
            if key in used_keys:
                continue
            anchors.append(leg)
    anchors.sort(key=lambda x: x["score"], reverse=True)
    return [PickLeg(**a) for a in anchors[:4]]


async def _generate_same_game_parlay(req: ParlayRequest) -> ParlayResponse:
    if req.legs > 3:
        raise ValueError("Same-game parlays support at most 3 legs (one ML, spread, and total pick)")

    profile = RISK_PROFILES[req.risk]
    games = await get_todays_games(req.sport)
    game = next((g for g in games if g.id == req.game_id), None)
    if not game:
        raise ValueError("Game not found — refresh the board and pick a listed matchup")

    candidates = await _candidate_legs(game, profile, req.risk)
    if len(candidates) < 2:
        raise ValueError("This game doesn't have enough markets (need ML, spread, and/or total)")

    chosen = _select_same_game_parlay(candidates, req.legs, req.risk)
    legs = [PickLeg(**c) for c in chosen]
    combined_american, combined_implied, estimated_win_prob = _parlay_metrics(chosen)
    matchup = f"{game.away_team} @ {game.home_team}"

    summary = (
        f"{profile['label']} {len(legs)}-leg same-game parlay on {matchup}. "
        f"Model win ~{estimated_win_prob:.1%} ({format(combined_american, '+d')}). "
        f"Legs are correlated — higher risk than multi-game slips."
    )

    used = {(c["game_id"], c.get("player"), c.get("stat")) for c in chosen}
    anchors = await _collect_anchors([game], used)

    return ParlayResponse(
        legs=legs,
        combined_american=combined_american,
        combined_implied_prob=round(combined_implied, 4),
        estimated_win_prob=estimated_win_prob,
        payout_on_100=payout_on_100(combined_american),
        risk=req.risk,
        same_game=True,
        summary=summary,
        anchors=anchors,
        book_check_passed=_sgp_combo_is_valid(chosen),
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
        candidates.extend(await _candidate_legs(game, profile, req.risk))

    if not candidates:
        raise ValueError("No qualifying legs right now — try Balanced or another sport")

    chosen = _select_parlay(candidates, req.legs, req.risk)
    legs = [PickLeg(**c) for c in chosen]
    combined_american, combined_implied, estimated_win_prob = _parlay_metrics(chosen)

    sports = ", ".join(sorted({l.sport.upper() for l in legs}))
    avg_edge = sum(c["edge"] for c in chosen) / len(chosen)
    fallback = any(c["model_source"] == "market_fallback" for c in chosen)
    summary = (
        f"{profile['label']} {len(legs)}-leg parlay across {sports}. "
        f"Model win ~{estimated_win_prob:.1%}, avg edge {avg_edge:+.1%} vs market "
        f"({format(combined_american, '+d')}). Uncorrelated legs, one per game."
        + (" Some legs lack team stats and are anchored to the market." if fallback else "")
    )

    chosen_games = {l.game_id: g for g in games for l in legs if g.id == l.game_id}
    used = {(c["game_id"], c.get("player"), c.get("stat")) for c in chosen}
    anchors = await _collect_anchors(list(chosen_games.values()), used)

    response = ParlayResponse(
        legs=legs,
        combined_american=combined_american,
        combined_implied_prob=round(combined_implied, 4),
        estimated_win_prob=estimated_win_prob,
        payout_on_100=payout_on_100(combined_american),
        risk=req.risk,
        same_game=False,
        summary=summary,
        anchors=anchors,
        book_check_passed=True,
        generated_at=datetime.now(timezone.utc),
    )

    insight = await explain_parlay(response)
    if insight:
        response.ai_insight = insight
    return response
