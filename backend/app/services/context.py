from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from app.config import settings
from app.db.supabase import get_supabase
from app.models.schemas import GameSummary
from app.services import espn, power_model, soccer_model
from app.services.demo_data import CONTEXT as DEMO_CONTEXT
from app.services.injuries import injury_context_for_teams
from app.services.news import news_headlines_for_game
from app.services.research import gemini_research
from app.services.weather import sync_weather_for_game

logger = logging.getLogger(__name__)

_context_cache: Dict[str, dict] = {}
_cache_ts: Dict[str, datetime] = {}


def _ttl() -> timedelta:
    return timedelta(minutes=max(10, settings.research_ttl_minutes))


# Back-to-back fatigue, in scoring-margin points. Only NBA/NHL play B2Bs;
# NFL is weekly and MLB plays daily, so rest there is a non-signal.
B2B_PENALTY = {"nba": 2.5, "nhl": 1.2, "mlb": 0.0, "nfl": 0.0}


def _american_to_implied(odds: int) -> float:
    if odds > 0:
        return 100 / (odds + 100)
    return abs(odds) / (abs(odds) + 100)


async def _rest_adjustment(game: GameSummary) -> Tuple[float, Optional[int], Optional[int]]:
    """Points adjustment to the home margin from rest (positive favors home)."""
    penalty = B2B_PENALTY.get(game.sport.lower(), 0.0)
    if penalty == 0.0:
        return 0.0, None, None
    home_rest = await espn.rest_days(game.sport, game.home_team, game.start_time)
    away_rest = await espn.rest_days(game.sport, game.away_team, game.start_time)
    home_pen = penalty if (home_rest is not None and home_rest <= 1) else 0.0
    away_pen = penalty if (away_rest is not None and away_rest <= 1) else 0.0
    return round(away_pen - home_pen, 2), home_rest, away_rest


def _line_move_from_snapshots(game_id: str) -> float:
    """Positive = line moved toward home side (home getting more respect)."""
    sb = get_supabase()
    if not sb:
        return 0.0
    since = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
    try:
        resp = (
            sb.table("odds_snapshots")
            .select("moneyline_home,spread_home,captured_at")
            .eq("game_id", game_id)
            .gte("captured_at", since)
            .order("captured_at", desc=False)
            .limit(50)
            .execute()
        )
        rows = resp.data or []
    except Exception:
        return 0.0
    if len(rows) < 2:
        return 0.0
    first, last = rows[0], rows[-1]
    move = 0.0
    if first.get("moneyline_home") and last.get("moneyline_home"):
        move += _american_to_implied(int(last["moneyline_home"])) - _american_to_implied(
            int(first["moneyline_home"])
        )
    if first.get("spread_home") is not None and last.get("spread_home") is not None:
        move += (float(last["spread_home"]) - float(first["spread_home"])) * -0.02
    return round(max(-0.12, min(0.12, move)), 4)


async def _build_context(game: GameSummary) -> dict:
    if settings.use_demo_data:
        return DEMO_CONTEXT.get(game.id, _empty_context())

    line_move = _line_move_from_snapshots(game.id)
    team_stats: Dict[str, dict] = {}
    injuries: dict = {}
    news: dict = {}
    weather: Optional[dict] = None

    try:
        team_stats, injuries, news, weather = await asyncio.gather(
            espn.fetch_team_stats(game.sport),
            injury_context_for_teams(game.sport, game.home_team, game.away_team, game.id),
            news_headlines_for_game(game.sport, game.home_team, game.away_team),
            sync_weather_for_game(
                game.id, game.home_team, game.sport, game.is_outdoor or game.sport in ("nfl", "mlb")
            ),
            return_exceptions=True,
        )
    except Exception:
        logger.exception("Context gather failed for %s", game.id)

    team_stats = team_stats if isinstance(team_stats, dict) else {}
    injuries = injuries if isinstance(injuries, dict) else {}
    news = news if isinstance(news, dict) else {}
    weather = weather if isinstance(weather, dict) else None

    if espn.is_soccer(game.sport):
        return await _build_soccer_context(game, team_stats, injuries, news, line_move)

    # 1) Independent base win probability from team strength + rest.
    rest_adj, home_rest, away_rest = await _rest_adjustment(game)
    base_prob, base_reason, debug = power_model.base_win_probability(
        game,
        team_stats,
        rest_adj=rest_adj,
        injury_margin_home=float(injuries.get("injury_margin_home", 0.0)),
        injury_margin_away=float(injuries.get("injury_margin_away", 0.0)),
    )
    expected_margin = debug.get("expected_margin", 0.0)
    lean, projected_total, _ = power_model.total_points_lean(game, team_stats)

    # 2) Gemini research nudges the numeric model and surfaces props.
    research = await gemini_research(
        game,
        base_prob=base_prob,
        projected_total=projected_total,
        injuries=injuries,
        news=news,
    )

    # 3) Final independent home win prob = base + bounded LLM adjustment.
    home_win_prob = None
    if base_prob is not None:
        home_win_prob = max(0.05, min(0.95, base_prob + float(research.get("home_win_prob_adj", 0.0))))
        home_win_prob = round(home_win_prob, 4)

    return {
        "base_home_win_prob": base_prob,
        "base_reason": base_reason,
        "home_win_prob": home_win_prob,
        "expected_margin": round(expected_margin, 2),
        "rest_adj": rest_adj,
        "home_rest_days": home_rest,
        "away_rest_days": away_rest,
        "projected_total": projected_total,
        "total_lean": research.get("total_lean", lean or "neutral"),
        "total_confidence": research.get("total_confidence", 0.0),
        "model_source": "model" if home_win_prob is not None else "market_fallback",
        "weather_factor": (weather or {}).get("weather_factor", 0.0),
        "line_move": line_move,
        "injury_margin_home": float(injuries.get("injury_margin_home", 0.0)),
        "injury_margin_away": float(injuries.get("injury_margin_away", 0.0)),
        "injuries_home": injuries.get("injuries_home", []),
        "injuries_away": injuries.get("injuries_away", []),
        "home_news": news.get("home_news", []),
        "away_news": news.get("away_news", []),
        "key_factors": research.get("key_factors", []),
        "prop_angles": research.get("prop_angles", []),
        "narrative": research.get("narrative", ""),
        "research_source": research.get("source", "none"),
    }


def _devig_3way(game: GameSummary) -> Optional[Tuple[float, float, float]]:
    """De-vigged (home, draw, away) market probabilities, or None if incomplete."""
    if game.moneyline_home is None or game.moneyline_away is None or game.draw_odds is None:
        return None
    h = _american_to_implied(game.moneyline_home)
    d = _american_to_implied(game.draw_odds)
    a = _american_to_implied(game.moneyline_away)
    total = h + d + a
    if total <= 0:
        return None
    return h / total, d / total, a / total


async def _build_soccer_context(
    game: GameSummary, team_stats: dict, injuries: dict, news: dict, line_move: float
) -> dict:
    """3-way + totals probabilities from the independent Poisson model."""
    probs = soccer_model.match_probabilities_for(game, team_stats)

    research = await gemini_research(
        game,
        base_prob=probs.get("home_win") if probs else None,
        projected_total=probs.get("projected_total") if probs else None,
        injuries=injuries,
        news=news,
    )

    ctx = _empty_context()
    ctx.update(
        {
            "home_news": news.get("home_news", []),
            "away_news": news.get("away_news", []),
            "injuries_home": injuries.get("injuries_home", []),
            "injuries_away": injuries.get("injuries_away", []),
            "line_move": line_move,
            "key_factors": research.get("key_factors", []),
            "narrative": research.get("narrative", ""),
            "research_source": research.get("source", "none"),
            "is_soccer": True,
        }
    )

    if not probs:
        ctx["base_reason"] = "no_team_stats"
        return ctx

    home = probs["home_win"]
    draw = probs["draw"]
    away = probs["away_win"]

    # World Cup samples are tiny (1-3 group games), so a pure goals model
    # over-regresses to even and invents huge underdog "edges". Use the de-vigged
    # market as a Bayesian PRIOR and weight the model by games played: early
    # tournament leans market, later leans model. (Unlike the season sports, we
    # don't have enough data here for the model to stand alone yet.)
    market = _devig_3way(game)
    if market:
        h_stats = espn.find_team_stats(team_stats, game.home_team) or {}
        a_stats = espn.find_team_stats(team_stats, game.away_team) or {}
        gp = (h_stats.get("games_played", 0) or 0) + (a_stats.get("games_played", 0) or 0)
        w_model = gp / (gp + 4)  # 1 game each -> ~0.33 model; full group -> ~0.6
        home = w_model * home + (1 - w_model) * market[0]
        draw = w_model * draw + (1 - w_model) * market[1]
        away = w_model * away + (1 - w_model) * market[2]

    # Apply the bounded LLM nudge to the home result, rebalancing draw/away.
    adj = float(research.get("home_win_prob_adj", 0.0))
    if adj:
        new_home = max(0.02, min(0.95, home + adj))
        rest = draw + away
        if rest > 0:
            scale = (1 - new_home) / rest
            draw, away = draw * scale, away * scale
        home = new_home

    over_p = probs["over"]
    ctx.update(
        {
            "base_home_win_prob": probs["home_win"],
            "base_reason": "ok",
            "home_win_prob": round(home, 4),
            "draw_prob": round(draw, 4),
            "away_win_prob": round(away, 4),
            "over_prob": round(over_p, 4),
            "btts_prob": probs["btts"],
            "projected_total": probs["projected_total"],
            "total_line": probs["total_line"],
            "total_lean": "over" if over_p > 0.53 else "under" if over_p < 0.47 else "neutral",
            "total_confidence": round(abs(over_p - 0.5) * 2, 3),
            "model_source": "model",
            "lambda_home": probs.get("lambda_home"),
            "lambda_away": probs.get("lambda_away"),
        }
    )
    return ctx


def _empty_context() -> dict:
    return {
        "base_home_win_prob": None,
        "base_reason": "no_team_stats",
        "home_win_prob": None,
        "expected_margin": 0.0,
        "rest_adj": 0.0,
        "home_rest_days": None,
        "away_rest_days": None,
        "projected_total": None,
        "total_lean": "neutral",
        "total_confidence": 0.0,
        "model_source": "market_fallback",
        "weather_factor": 0.0,
        "line_move": 0.0,
        "injury_margin_home": 0.0,
        "injury_margin_away": 0.0,
        "injuries_home": [],
        "injuries_away": [],
        "home_news": [],
        "away_news": [],
        "key_factors": [],
        "prop_angles": [],
        "narrative": "",
        "research_source": "none",
    }


async def get_game_context(game: GameSummary) -> dict:
    now = datetime.now(timezone.utc)
    cached_at = _cache_ts.get(game.id)
    if cached_at and now - cached_at < _ttl() and game.id in _context_cache:
        return _context_cache[game.id]

    ctx = await _build_context(game)
    _context_cache[game.id] = ctx
    _cache_ts[game.id] = now
    return ctx


async def refresh_all_context(games: List[GameSummary]) -> None:
    await asyncio.gather(*[get_game_context(g) for g in games[:12]])


def cached_context(game_id: str) -> Optional[dict]:
    """Return already-computed context for a game without triggering a fetch."""
    return _context_cache.get(game_id)
