from __future__ import annotations

import asyncio
import itertools
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from app.models.schemas import GameSummary, ParlayRequest, ParlayResponse, PickLeg, RiskLevel
from app.config import settings
from app.services import espn, power_model, props
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

    # Soccer: 3-way result (home / draw / away) + total goals. No spread.
    if espn.is_soccer(game.sport):
        draw_prob = ctx.get("draw_prob")
        away_win_prob = ctx.get("away_win_prob")
        over_p = ctx.get("over_prob")

        def _wp(prob: Optional[float], odds: int) -> float:
            return prob if prob is not None else american_to_implied(odds)

        if game.moneyline_home is not None:
            await add("moneyline", game.home_team, game.moneyline_home, _wp(home_win_prob, game.moneyline_home))
        if game.draw_odds is not None:
            await add("moneyline", "Draw", game.draw_odds, _wp(draw_prob, game.draw_odds))
        if game.moneyline_away is not None:
            await add("moneyline", game.away_team, game.moneyline_away, _wp(away_win_prob, game.moneyline_away))
        if game.total is not None:
            tc = float(ctx.get("total_confidence", 0.0)) * 0.2
            if over_p is not None:
                await add("total", f"Over {game.total}", game.over_odds, over_p, extra_conf=tc)
                await add("total", f"Under {game.total}", game.under_odds, round(1 - over_p, 4), extra_conf=tc)
            else:
                await add("total", f"Over {game.total}", game.over_odds, american_to_implied(game.over_odds))
                await add("total", f"Under {game.total}", game.under_odds, american_to_implied(game.under_odds))
        return legs

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


def _soccer_sgp_is_valid(combo: List[dict]) -> bool:
    """Soccer same-game: at most one result (home/draw/away) and one total side.

    Result + total combos (e.g. Home + Over 2.5) are allowed; two results or
    over+under are not.
    """
    results = 0
    has_over = has_under = False
    for leg in combo:
        market = leg.get("market", "")
        sel = leg.get("selection", "").lower()
        if market == "moneyline":  # home / Draw / away are mutually exclusive
            results += 1
            if results > 1:
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
    return True


def _sgp_combo_is_valid(combo: List[dict]) -> bool:
    """Reject same-game combos with conflicting legs (both sides or impossible margins)."""
    if any(espn.is_soccer(leg.get("sport", "")) for leg in combo):
        return _soccer_sgp_is_valid(combo)

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


MAX_PROP_LEGS_PER_GAME = 2

# --- Props-first policy ------------------------------------------------------
# A game market (who wins / spread / total) only earns a spot on a slip when the
# model disagrees with the market by a wide margin. Otherwise the slip is built
# from player-stat legs, which is the product this engine is tuned for.
GAME_LEG_MIN_EDGE = 0.055
GAME_LEG_MIN_WIN = 0.58
GAME_LEG_MAX = 1

# Legs from the same game are NOT independent: a blowout benches everyone and a
# slow pace suppresses every counting stat at once. Multiplying probabilities as
# if independent overstates the slip, so haircut each extra shared-game leg.
SAME_GAME_CORRELATION = 0.97


def correlated_win_prob(legs: List[dict]) -> float:
    """Combined probability with a penalty for legs sharing a game."""
    prob = 1.0
    per_game: Dict[str, int] = {}
    for leg in legs:
        prob *= leg["win_probability"]
        per_game[leg["game_id"]] = per_game.get(leg["game_id"], 0) + 1
    extra = sum(c - 1 for c in per_game.values() if c > 1)
    return round(prob * (SAME_GAME_CORRELATION ** extra), 4)


def parlay_ev_per_100(win_prob: float, combined_american: int) -> float:
    """Expected profit on a $100 stake at the quoted price."""
    return round(win_prob * payout_on_100(combined_american) - (1 - win_prob) * 100.0, 2)


def fair_american(win_prob: float) -> int:
    """Break-even price for the whole slip."""
    p = max(0.005, min(0.995, win_prob))
    return -int(round(100 * p / (1 - p))) if p >= 0.5 else int(round(100 * (1 - p) / p))


def _ev_note(legs: List[dict], ev: float) -> Optional[str]:
    """Say plainly what the EV number can and cannot prove."""
    if all(l["market"] == "player_prop" for l in legs):
        return (
            "Prices shown are model-derived, not real book lines, so this cannot tell "
            "you the slip is +EV. Compare each leg to your book: beat the break-even "
            "price listed on it and that leg is worth playing. Stacking more legs "
            "multiplies the vig — it never creates an edge."
        )
    if ev < 0:
        return f"At the quoted price this is -EV: about ${ev:.0f} expected per $100 staked."
    return None


def _select_props_first(pool: List[dict], leg_count: int, risk: RiskLevel) -> List[dict]:
    """Fill with player-stat legs; admit a game market only at a wide edge."""
    prop_pool = [c for c in pool if c["market"] == "player_prop"]
    chosen = _select_prop_legs(prop_pool, leg_count)

    if len(chosen) >= leg_count:
        return chosen

    standout = [
        c
        for c in pool
        if c["market"] != "player_prop"
        and (c.get("edge") or 0.0) >= GAME_LEG_MIN_EDGE
        and c["win_probability"] >= GAME_LEG_MIN_WIN
    ]
    used_games = {c["game_id"] for c in chosen}
    added = 0
    for cand in sorted(standout, key=lambda x: (x["edge"], x["win_probability"]), reverse=True):
        if len(chosen) >= leg_count or added >= GAME_LEG_MAX:
            break
        if cand["game_id"] in used_games:  # don't correlate a game leg with its own props
            continue
        chosen.append(cand)
        used_games.add(cand["game_id"])
        added += 1
    return chosen



def _select_prop_legs(pool: List[dict], leg_count: int) -> List[dict]:
    """Greedy pick: best score first, one leg per player, capped per game."""
    ranked = sorted(pool, key=lambda x: (x["score"], x["win_probability"]), reverse=True)
    chosen: List[dict] = []
    players: set = set()
    per_game: Dict[str, int] = {}
    for cand in ranked:
        if len(chosen) >= leg_count:
            break
        player = (cand.get("player") or "").lower()
        if player in players:
            continue
        if per_game.get(cand["game_id"], 0) >= MAX_PROP_LEGS_PER_GAME:
            continue
        players.add(player)
        per_game[cand["game_id"]] = per_game.get(cand["game_id"], 0) + 1
        chosen.append(cand)
    return chosen


async def _generate_props_parlay(req: ParlayRequest) -> ParlayResponse:
    if not settings.enable_player_props:
        raise ValueError("Player props are disabled — set ENABLE_PLAYER_PROPS=true")
    if req.sport and req.sport.lower() != "nba":
        raise ValueError("Player-prop parlays are NBA-only for now — pick NBA")

    profile = RISK_PROFILES[req.risk]
    games = await get_todays_games("nba")
    if req.game_id:
        games = [g for g in games if g.id == req.game_id] or games
    if not games:
        raise ValueError("No NBA games on the board — check back on a game day")

    async def _cands(game: GameSummary) -> List[dict]:
        ctx = await get_game_context(game)
        return await props.prop_parlay_candidates(game, ctx, risk=req.risk)

    results = await asyncio.gather(*(_cands(g) for g in games[:12]), return_exceptions=True)
    pool = [leg for r in results if isinstance(r, list) for leg in r]
    if not pool:
        raise ValueError(
            "No qualifying player props today — player averages may be unavailable (offseason?)"
        )

    chosen = _select_prop_legs(pool, req.legs)
    if len(chosen) < 2:
        raise ValueError("Not enough qualifying prop legs — try Balanced/Bold or fewer legs")

    legs = [PickLeg(**c) for c in chosen]
    combined_american, combined_implied, estimated_win_prob = _parlay_metrics(chosen)
    corr_prob = correlated_win_prob(chosen)
    ev = parlay_ev_per_100(corr_prob, combined_american)
    avg_leg = sum(c["win_probability"] for c in chosen) / len(chosen)
    summary = (
        f"{profile['label']} {len(legs)}-leg NBA player-prop parlay — stat overs at "
        f"alt lines the model puts at ~{avg_leg:.0%} each, so the slip lands about "
        f"{corr_prob:.0%} of the time ({format(combined_american, '+d')}). Each extra "
        f"leg raises the payout and lowers the hit rate — it does not add value. "
        f"Lines and odds are model-derived; confirm each price at your book."
    )

    return ParlayResponse(
        legs=legs,
        combined_american=combined_american,
        combined_implied_prob=round(combined_implied, 4),
        estimated_win_prob=estimated_win_prob,
        payout_on_100=payout_on_100(combined_american),
        risk=req.risk,
        same_game=len({c["game_id"] for c in chosen}) == 1,
        summary=summary,
        anchors=[],
        correlated_win_prob=corr_prob,
        fair_combined_american=fair_american(corr_prob),
        expected_value_per_100=ev,
        ev_warning=_ev_note(chosen, ev),
        book_check_passed=True,
        generated_at=datetime.now(timezone.utc),
    )


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
    if req.mode == "props":
        response = await _generate_props_parlay(req)
        insight = await explain_parlay(response)
        if insight:
            response.ai_insight = insight
        return response

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

    # Props-first: fold NBA player-stat legs into the pool so they are the
    # default building block, and team markets have to earn their place.
    if settings.enable_player_props:
        nba = [g for g in games[:12] if g.sport == "nba"]

        async def _props_for(game: GameSummary) -> List[dict]:
            ctx = await get_game_context(game)
            return await props.prop_parlay_candidates(game, ctx, risk=req.risk)

        for result in await asyncio.gather(*(_props_for(g) for g in nba), return_exceptions=True):
            if isinstance(result, list):
                candidates.extend(result)

    if not candidates:
        raise ValueError("No qualifying legs right now — try Balanced or another sport")

    note = ""
    chosen = _select_props_first(candidates, req.legs, req.risk)
    if len(chosen) < 2:
        # No props available (non-NBA slate): fall back to team markets, still
        # preferring wide edges over "whatever is on the board".
        standout = [c for c in candidates if (c.get("edge") or 0.0) >= GAME_LEG_MIN_EDGE]
        try:
            chosen = _select_parlay(standout, req.legs, req.risk)
        except ValueError:
            chosen = _select_parlay(candidates, req.legs, req.risk)
            note = " No standout edges on the board — these are the best available, not high-conviction plays."

    legs = [PickLeg(**c) for c in chosen]
    combined_american, combined_implied, estimated_win_prob = _parlay_metrics(chosen)
    corr_prob = correlated_win_prob(chosen)
    ev = parlay_ev_per_100(corr_prob, combined_american)

    sports = ", ".join(sorted({l.sport.upper() for l in legs}))
    n_props = sum(1 for c in chosen if c["market"] == "player_prop")
    n_game = len(chosen) - n_props
    fallback = any(c.get("model_source") == "market_fallback" for c in chosen)
    mix = (
        f"{n_props} player-stat leg{'s' if n_props != 1 else ''}"
        + (f" + {n_game} team market{'s' if n_game != 1 else ''} (wide model edge)" if n_game else "")
    )
    summary = (
        f"{profile['label']} {len(legs)}-leg parlay across {sports} — {mix}. "
        f"Lands about {corr_prob:.0%} of the time ({format(combined_american, '+d')})."
        + (" Some legs lack team stats and are anchored to the market." if fallback else "")
        + note
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
        correlated_win_prob=corr_prob,
        fair_combined_american=fair_american(corr_prob),
        expected_value_per_100=ev,
        ev_warning=_ev_note(chosen, ev),
        book_check_passed=True,
        generated_at=datetime.now(timezone.utc),
    )

    insight = await explain_parlay(response)
    if insight:
        response.ai_insight = insight
    return response
