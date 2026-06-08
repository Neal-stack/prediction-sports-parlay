from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from app.db.supabase import get_supabase
from app.models.schemas import LineMovementPoint


async def get_line_movement(game_id: str, limit: int = 24) -> List[LineMovementPoint]:
    sb = get_supabase()
    if not sb:
        return []

    resp = (
        sb.table("odds_snapshots")
        .select("moneyline_home,moneyline_away,spread_home,total,captured_at")
        .eq("game_id", game_id)
        .order("captured_at", desc=True)
        .limit(limit)
        .execute()
    )
    rows = list(reversed(resp.data or []))
    points: List[LineMovementPoint] = []
    for row in rows:
        captured = row.get("captured_at")
        if isinstance(captured, str):
            captured = datetime.fromisoformat(captured.replace("Z", "+00:00"))
        points.append(
            LineMovementPoint(
                captured_at=captured or datetime.now(timezone.utc),
                moneyline_home=row.get("moneyline_home"),
                moneyline_away=row.get("moneyline_away"),
                spread_home=float(row["spread_home"]) if row.get("spread_home") is not None else None,
                total=float(row["total"]) if row.get("total") is not None else None,
            )
        )
    return points
