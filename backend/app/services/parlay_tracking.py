from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional
from uuid import uuid4

from app.db.async_db import run_sync
from app.db.supabase import get_supabase
from app.models.schemas import (
    ConfirmSettlementRequest,
    LegSettlementSuggestion,
    ParlayResponse,
    PerformanceStats,
    SavedParlayRecord,
    SaveParlayRequest,
    SetLegOutcomeRequest,
    SettlementSuggestionResponse,
    SuggestSettlementRequest,
)
from app.services import espn, player_stats, soccer_player_stats
from app.services.calibration import compute_parlay_outcome, record_leg_outcome
from app.services.scores import get_game_result, sync_scores_for_game_ids
from app.services.settlement import grade_leg, grade_player_prop

logger = logging.getLogger(__name__)


def _row_to_record(row: dict) -> SavedParlayRecord:
    legs_data = row["legs"]
    leg_outcomes = row.get("leg_outcomes") or ["pending"] * len(legs_data)
    return SavedParlayRecord(
        id=str(row["id"]),
        session_id=row["session_id"],
        stake=float(row.get("stake") or 0),
        combined_american=row["combined_american"],
        combined_implied_prob=float(row.get("combined_implied_prob") or 0),
        estimated_win_prob=float(row.get("estimated_win_prob") or 0),
        risk=row["risk"],
        same_game=bool(row.get("same_game")),
        outcome=row["outcome"],
        legs=legs_data,
        leg_outcomes=leg_outcomes,
        summary=row.get("summary"),
        generated_at=row["generated_at"],
        saved_at=row.get("saved_at") or row["generated_at"],
        settled_at=row.get("settled_at"),
    )


async def save_parlay(session_id: str, req: SaveParlayRequest) -> SavedParlayRecord:
    parlay = req.parlay
    leg_outcomes = ["pending"] * len(parlay.legs)
    record_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()

    row = {
        "id": record_id,
        "session_id": session_id,
        "stake": req.stake,
        "combined_american": parlay.combined_american,
        "combined_implied_prob": parlay.combined_implied_prob,
        "estimated_win_prob": parlay.estimated_win_prob,
        "risk": parlay.risk,
        "same_game": parlay.same_game,
        "outcome": "pending",
        "legs": [leg.model_dump() for leg in parlay.legs],
        "leg_outcomes": leg_outcomes,
        "summary": parlay.summary,
        "generated_at": parlay.generated_at.isoformat(),
        "saved_at": now,
    }

    sb = get_supabase()
    if sb:
        def _insert():
            sb.table("saved_parlays").insert(row).execute()

        try:
            await run_sync(_insert)
        except Exception:
            logger.exception("Failed to save parlay to Supabase")

    return SavedParlayRecord(
        id=record_id,
        session_id=session_id,
        stake=req.stake,
        combined_american=parlay.combined_american,
        combined_implied_prob=parlay.combined_implied_prob,
        estimated_win_prob=parlay.estimated_win_prob,
        risk=parlay.risk,
        same_game=parlay.same_game,
        outcome="pending",
        legs=[leg.model_dump() for leg in parlay.legs],
        leg_outcomes=leg_outcomes,
        summary=parlay.summary,
        generated_at=parlay.generated_at,
        saved_at=datetime.fromisoformat(now.replace("Z", "+00:00")),
    )


async def list_parlays(session_id: str, limit: int = 50) -> List[SavedParlayRecord]:
    sb = get_supabase()
    if not sb:
        return []

    def _fetch():
        return (
            sb.table("saved_parlays")
            .select("*")
            .eq("session_id", session_id)
            .order("saved_at", desc=True)
            .limit(limit)
            .execute()
        )

    try:
        resp = await run_sync(_fetch)
        return [_row_to_record(r) for r in (resp.data or [])]
    except Exception:
        logger.exception("Failed to list parlays")
        return []


async def set_leg_outcome(
    session_id: str,
    parlay_id: str,
    body: SetLegOutcomeRequest,
) -> Optional[SavedParlayRecord]:
    sb = get_supabase()
    if not sb:
        return None

    def _fetch():
        return (
            sb.table("saved_parlays")
            .select("*")
            .eq("id", parlay_id)
            .eq("session_id", session_id)
            .limit(1)
            .execute()
        )

    try:
        resp = await run_sync(_fetch)
        rows = resp.data or []
        if not rows:
            return None

        row = rows[0]
        legs = row["legs"]
        leg_outcomes = list(row.get("leg_outcomes") or ["pending"] * len(legs))

        if body.leg_index < 0 or body.leg_index >= len(legs):
            return None

        leg_outcomes[body.leg_index] = body.outcome
        parlay_outcome = compute_parlay_outcome(leg_outcomes)
        settled_at = (
            datetime.now(timezone.utc).isoformat()
            if parlay_outcome != "pending"
            else None
        )

        def _update():
            sb.table("saved_parlays").update(
                {
                    "leg_outcomes": leg_outcomes,
                    "outcome": parlay_outcome,
                    "settled_at": settled_at,
                }
            ).eq("id", parlay_id).execute()

        await run_sync(_update)

        leg = legs[body.leg_index]
        await record_leg_outcome(
            parlay_id=parlay_id,
            session_id=session_id,
            leg_index=body.leg_index,
            game_id=leg.get("game_id"),
            sport=leg.get("sport", "unknown"),
            market=leg.get("market", "moneyline"),
            selection=leg.get("selection", ""),
            odds_american=leg.get("odds_american", -110),
            implied_prob=float(leg.get("implied_prob", 0.5)),
            predicted_win_prob=float(leg.get("win_probability", 0.5)),
            confidence=leg.get("confidence"),
            score=leg.get("score"),
            risk=row["risk"],
            outcome=body.outcome,
        )

        row["leg_outcomes"] = leg_outcomes
        row["outcome"] = parlay_outcome
        row["settled_at"] = settled_at
        return _row_to_record(row)
    except Exception:
        logger.exception("Failed to set leg outcome")
        return None


async def get_parlay(session_id: str, parlay_id: str) -> Optional[SavedParlayRecord]:
    sb = get_supabase()
    if not sb:
        return None

    def _fetch():
        return (
            sb.table("saved_parlays")
            .select("*")
            .eq("id", parlay_id)
            .eq("session_id", session_id)
            .limit(1)
            .execute()
        )

    try:
        resp = await run_sync(_fetch)
        rows = resp.data or []
        return _row_to_record(rows[0]) if rows else None
    except Exception:
        logger.exception("Failed to get parlay %s", parlay_id)
        return None


def _coerce_dt(value) -> datetime:
    if isinstance(value, datetime):
        return value
    if value:
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


async def _grade_prop_leg(index: int, leg: dict, result: dict) -> LegSettlementSuggestion:
    """Grade a player_prop leg from a box score once its game is final."""
    sport = leg.get("sport") or result.get("sport") or ""
    stat = leg.get("stat")
    prop_line = leg.get("prop_line")
    prop_side = leg.get("prop_side")
    player = leg.get("player", "")
    if not (stat and prop_line is not None and prop_side):
        return LegSettlementSuggestion(
            leg_index=index, reason="Prop leg missing data — settle manually", ready=False
        )

    if espn.is_soccer(sport):
        box = await soccer_player_stats.fetch_player_match_stats(
            leg.get("game_id", ""),
            result["home_team"],
            result["away_team"],
            _coerce_dt(result.get("start_time")),
            player=player,
            player_id=leg.get("player_id"),
        )
    else:
        box = await player_stats.fetch_player_box_score(
            sport,
            leg.get("game_id", ""),
            result["home_team"],
            result["away_team"],
            _coerce_dt(result.get("start_time")),
            player=player,
            player_id=leg.get("player_id"),
        )
    if not box or box.get(stat) is None:
        return LegSettlementSuggestion(
            leg_index=index,
            reason=f"Box score for {player} not available yet — check back or settle manually",
            score_display=result.get("score_display"),
            ready=False,
        )

    try:
        outcome = grade_player_prop(
            stat=stat, prop_line=float(prop_line), prop_side=prop_side, box_score=box
        )
    except (ValueError, TypeError):
        return LegSettlementSuggestion(
            leg_index=index, reason="Could not grade prop — settle manually", ready=False
        )

    actual = box.get(stat)
    return LegSettlementSuggestion(
        leg_index=index,
        outcome=outcome,
        score_display=f"{player}: {actual} {stat}",
        reason=f"{player} had {actual} {stat} (line {prop_line:g} {prop_side}) → {outcome}",
        ready=True,
    )


async def suggest_settlement(body: SuggestSettlementRequest) -> SettlementSuggestionResponse:
    legs = body.legs
    leg_outcomes = body.leg_outcomes or ["pending"] * len(legs)
    if len(leg_outcomes) < len(legs):
        leg_outcomes = leg_outcomes + ["pending"] * (len(legs) - len(leg_outcomes))

    pending_ids = list(
        {
            leg["game_id"]
            for i, leg in enumerate(legs)
            if i < len(leg_outcomes) and leg_outcomes[i] == "pending"
        }
    )
    if pending_ids:
        await sync_scores_for_game_ids(pending_ids)

    suggestions: List[LegSettlementSuggestion] = []
    all_ready = True
    pending_count = 0

    for i, leg in enumerate(legs):
        if i < len(leg_outcomes) and leg_outcomes[i] != "pending":
            continue
        pending_count += 1
        game_id = leg.get("game_id", "")
        result = await get_game_result(game_id) if game_id else None

        if not result or result.get("game_status") != "final":
            all_ready = False
            suggestions.append(
                LegSettlementSuggestion(
                    leg_index=i,
                    reason="Waiting for final score"
                    if not result
                    else "Game still in progress",
                    score_display=result.get("score_display") if result else None,
                    ready=False,
                )
            )
            continue

        # Player props grade from the box score, not the game score.
        if leg.get("market") == "player_prop":
            graded = await _grade_prop_leg(i, leg, result)
            suggestions.append(graded)
            if not graded.ready:
                all_ready = False
            continue

        try:
            outcome = grade_leg(
                market=leg.get("market", "moneyline"),
                selection=leg.get("selection", ""),
                matchup=leg.get("matchup", ""),
                home_score=int(result["home_score"]),
                away_score=int(result["away_score"]),
                sport=leg.get("sport"),
            )
            suggestions.append(
                LegSettlementSuggestion(
                    leg_index=i,
                    outcome=outcome,
                    score_display=result["score_display"],
                    reason=f"Final: {result['score_display']} → {outcome}",
                    ready=True,
                )
            )
        except ValueError as exc:
            all_ready = False
            suggestions.append(
                LegSettlementSuggestion(
                    leg_index=i,
                    reason=str(exc),
                    score_display=result.get("score_display"),
                    ready=False,
                )
            )

    if pending_count == 0:
        return SettlementSuggestionResponse(
            ready=False,
            suggestions=[],
            message="All legs already settled",
        )

    return SettlementSuggestionResponse(
        ready=all_ready and bool(suggestions),
        suggestions=suggestions,
        message=None if all_ready else "Some games are not final yet — check back soon",
    )


async def confirm_settlement(
    session_id: str,
    parlay_id: str,
    body: ConfirmSettlementRequest,
) -> Optional[SavedParlayRecord]:
    record: Optional[SavedParlayRecord] = None
    for item in body.outcomes:
        record = await set_leg_outcome(session_id, parlay_id, item)
        if not record:
            return None
    return record


async def get_performance(session_id: str) -> PerformanceStats:
    parlays = await list_parlays(session_id, limit=100)
    settled = [p for p in parlays if p.outcome != "pending"]
    wins = sum(1 for p in settled if p.outcome == "win")
    losses = sum(1 for p in settled if p.outcome == "loss")
    pending = sum(1 for p in parlays if p.outcome == "pending")

    predicted_sum = 0.0
    actual_sum = 0.0
    leg_count = 0
    for p in parlays:
        for i, leg in enumerate(p.legs):
            outcome = p.leg_outcomes[i] if i < len(p.leg_outcomes) else "pending"
            if outcome == "pending":
                continue
            leg_count += 1
            predicted_sum += leg.get("win_probability", 0.5)
            actual_sum += 1.0 if outcome == "win" else 0.0

    hit_rate = actual_sum / leg_count if leg_count else None
    predicted_rate = predicted_sum / leg_count if leg_count else None

    return PerformanceStats(
        total_parlays=len(parlays),
        pending=pending,
        wins=wins,
        losses=losses,
        pushes=len(settled) - wins - losses,
        leg_hit_rate=round(hit_rate, 4) if hit_rate is not None else None,
        model_predicted_rate=round(predicted_rate, 4) if predicted_rate is not None else None,
        calibration_gap=(
            round(hit_rate - predicted_rate, 4)
            if hit_rate is not None and predicted_rate is not None
            else None
        ),
    )
