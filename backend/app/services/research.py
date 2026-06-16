"""Gemini pre-generation research pass.

This is the key shift the roadmap calls for: the LLM contributes BEFORE picks
are made, not just as post-hoc prose. Given the independent model's base
numbers plus injuries and headlines, Gemini returns a small, bounded structured
signal that nudges (never overrides) the numeric model and surfaces prop angles.

Output is cached per game by the context layer, so chat reuses the same signal
without extra API calls.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from app.models.schemas import GameSummary
from app.services.llm import llm_text, parse_json

# Hard bound on how much the LLM may move the numeric model (probability).
MAX_LLM_ADJ = 0.08

NEUTRAL_SIGNALS: Dict[str, object] = {
    "home_win_prob_adj": 0.0,
    "total_lean": "neutral",
    "total_confidence": 0.0,
    "key_factors": [],
    "prop_angles": [],
    "narrative": "",
    "source": "none",
}


def _clamp(value, lo: float, hi: float, default: float = 0.0) -> float:
    try:
        return max(lo, min(hi, float(value)))
    except (TypeError, ValueError):
        return default


def _build_prompt(
    game: GameSummary,
    base_prob: Optional[float],
    projected_total: Optional[float],
    injuries: Dict[str, object],
    news: Dict[str, List[str]],
) -> str:
    base_txt = f"{base_prob:.0%}" if base_prob is not None else "unknown (no team stats)"
    inj_home = ", ".join(
        f"{i['player']} ({i.get('position','')} {i['status']})" for i in injuries.get("injuries_home", [])
    ) or "none reported"
    inj_away = ", ".join(
        f"{i['player']} ({i.get('position','')} {i['status']})" for i in injuries.get("injuries_away", [])
    ) or "none reported"
    home_news = "; ".join(news.get("home_news", [])) or "none"
    away_news = "; ".join(news.get("away_news", [])) or "none"

    return f"""You are a quantitative sports analyst. Analyze this {game.sport.upper()} game and output ONLY valid JSON (no prose, no markdown fences).

Game: {game.away_team} (away) @ {game.home_team} (home)
Model base home win probability (independent of betting line): {base_txt}
Projected total (model): {projected_total if projected_total is not None else "unknown"} | Posted total: {game.total}
Home injuries: {inj_home}
Away injuries: {inj_away}
Home news: {home_news}
Away news: {away_news}

Return exactly this JSON shape:
{{
  "home_win_prob_adj": 0.0,        // adjustment to the base home win prob, between -0.08 and 0.08
  "total_lean": "over|under|neutral",
  "total_confidence": 0.0,          // 0.0 to 1.0
  "key_factors": ["short factor", "short factor"],   // 2-3 items
  "prop_angles": [
    {{"player": "Name", "stat": "points|rebounds|assists|3pm", "direction": "over|under", "confidence": 0.0}}
  ],
  "narrative": "one honest sentence; no guarantees"
}}

Base your adjustment on injuries, rest, and matchup context — not on guessing the betting market. If unsure, use 0.0 and "neutral"."""


async def gemini_research(
    game: GameSummary,
    *,
    base_prob: Optional[float],
    projected_total: Optional[float],
    injuries: Dict[str, object],
    news: Dict[str, List[str]],
) -> Dict[str, object]:
    prompt = _build_prompt(game, base_prob, projected_total, injuries, news)
    raw = await llm_text(prompt, max_tokens=420, temperature=0.3)
    data = parse_json(raw)
    if not data:
        return dict(NEUTRAL_SIGNALS)

    lean = str(data.get("total_lean", "neutral")).lower()
    if lean not in ("over", "under", "neutral"):
        lean = "neutral"

    props: List[dict] = []
    for p in (data.get("prop_angles") or [])[:5]:
        if not isinstance(p, dict):
            continue
        stat = str(p.get("stat", "")).lower()
        direction = str(p.get("direction", "")).lower()
        if stat in ("points", "rebounds", "assists", "3pm") and direction in ("over", "under"):
            props.append(
                {
                    "player": str(p.get("player", "")).strip(),
                    "stat": stat,
                    "direction": direction,
                    "confidence": _clamp(p.get("confidence"), 0.0, 1.0),
                }
            )

    factors = [str(f).strip() for f in (data.get("key_factors") or [])[:3] if str(f).strip()]

    return {
        "home_win_prob_adj": round(_clamp(data.get("home_win_prob_adj"), -MAX_LLM_ADJ, MAX_LLM_ADJ), 4),
        "total_lean": lean,
        "total_confidence": round(_clamp(data.get("total_confidence"), 0.0, 1.0), 3),
        "key_factors": factors,
        "prop_angles": props,
        "narrative": str(data.get("narrative", "")).strip()[:280],
        "source": "gemini" if raw else "none",
    }
