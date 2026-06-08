from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

from app.db.async_db import run_sync
from app.db.supabase import get_supabase

logger = logging.getLogger(__name__)

MIN_SAMPLES = 5
MAX_ADJUSTMENT = 0.08

_cache: Dict[Tuple[str, str, str, str], float] = {}
_cache_loaded = False


def prob_bucket(predicted: float) -> str:
    """Bucket predicted win probability into 5% bands."""
    low = int(predicted * 20) / 20
    high = low + 0.05
    return f"{low:.2f}-{high:.2f}"


def calibration_adjustment(
    sport: str,
    market: str,
    risk: str,
    predicted: float,
) -> float:
    """Return additive adjustment from historical leg outcomes."""
    bucket = prob_bucket(predicted)
    key = (sport.lower(), market.lower(), risk.lower(), bucket)
    if key in _cache:
        return _cache[key]

    # Shrink toward zero when bucket not in cache
    return 0.0


async def load_calibration_cache() -> None:
    global _cache, _cache_loaded
    sb = get_supabase()
    if not sb:
        _cache_loaded = True
        return

    def _fetch():
        return sb.table("calibration_stats").select("*").gte("sample_count", MIN_SAMPLES).execute()

    try:
        resp = await run_sync(_fetch)
        rows = resp.data or []
        _cache = {}
        for row in rows:
            predicted_avg = float(row["predicted_avg"])
            actual_hit = float(row["actual_hit_rate"])
            adj = max(-MAX_ADJUSTMENT, min(MAX_ADJUSTMENT, actual_hit - predicted_avg))
            key = (
                row["sport"].lower(),
                row["market"].lower(),
                row["risk"].lower(),
                row["prob_bucket"],
            )
            _cache[key] = adj
        _cache_loaded = True
    except Exception:
        logger.exception("Failed to load calibration cache")
        _cache_loaded = True


async def ensure_calibration_loaded() -> None:
    if not _cache_loaded:
        await load_calibration_cache()


async def record_leg_outcome(
    *,
    parlay_id: str,
    session_id: str,
    leg_index: int,
    game_id: Optional[str],
    sport: str,
    market: str,
    selection: str,
    odds_american: int,
    implied_prob: float,
    predicted_win_prob: float,
    confidence: Optional[float],
    score: Optional[float],
    risk: str,
    outcome: str,
) -> None:
    sb = get_supabase()
    if not sb:
        return

    bucket = prob_bucket(predicted_win_prob)

    def _upsert():
        sb.table("leg_outcomes").upsert(
            {
                "parlay_id": parlay_id,
                "session_id": session_id,
                "leg_index": leg_index,
                "game_id": game_id,
                "sport": sport.lower(),
                "market": market.lower(),
                "selection": selection,
                "odds_american": odds_american,
                "implied_prob": implied_prob,
                "predicted_win_prob": predicted_win_prob,
                "confidence": confidence,
                "score": score,
                "risk": risk.lower(),
                "outcome": outcome,
            },
            on_conflict="parlay_id,leg_index",
        ).execute()

        stats_resp = (
            sb.table("calibration_stats")
            .select("*")
            .eq("sport", sport.lower())
            .eq("market", market.lower())
            .eq("risk", risk.lower())
            .eq("prob_bucket", bucket)
            .limit(1)
            .execute()
        )
        existing = (stats_resp.data or [None])[0]
        hit = 1.0 if outcome == "win" else 0.0

        if existing:
            n = int(existing["sample_count"]) + 1
            new_predicted = (
                float(existing["predicted_avg"]) * int(existing["sample_count"])
                + predicted_win_prob
            ) / n
            new_actual = (
                float(existing["actual_hit_rate"]) * int(existing["sample_count"]) + hit
            ) / n
            sb.table("calibration_stats").update(
                {
                    "predicted_avg": round(new_predicted, 4),
                    "actual_hit_rate": round(new_actual, 4),
                    "sample_count": n,
                }
            ).eq("id", existing["id"]).execute()
        else:
            sb.table("calibration_stats").insert(
                {
                    "sport": sport.lower(),
                    "market": market.lower(),
                    "risk": risk.lower(),
                    "prob_bucket": bucket,
                    "predicted_avg": round(predicted_win_prob, 4),
                    "actual_hit_rate": round(hit, 4),
                    "sample_count": 1,
                }
            ).execute()

    try:
        await run_sync(_upsert)
        await load_calibration_cache()
    except Exception:
        logger.exception("Failed to record leg outcome for calibration")


async def get_calibration_summary() -> dict:
    sb = get_supabase()
    if not sb:
        return {"sample_count": 0, "buckets": []}

    def _fetch():
        legs = sb.table("leg_outcomes").select("id", count="exact").execute()
        stats = sb.table("calibration_stats").select("*").order("sample_count", desc=True).limit(10).execute()
        return legs, stats

    try:
        legs_resp, stats_resp = await run_sync(_fetch)
        return {
            "sample_count": legs_resp.count or 0,
            "buckets": stats_resp.data or [],
        }
    except Exception:
        logger.exception("Failed to fetch calibration summary")
        return {"sample_count": 0, "buckets": []}


def compute_parlay_outcome(leg_outcomes: List[str]) -> str:
    """Derive parlay outcome from per-leg results."""
    if not leg_outcomes or any(o == "pending" for o in leg_outcomes):
        return "pending"
    if any(o == "loss" for o in leg_outcomes):
        return "loss"
    if all(o == "win" for o in leg_outcomes):
        return "win"
    return "push"
