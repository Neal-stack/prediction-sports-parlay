from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings

SHARP_BASE = "https://api.sharpapi.io/api/v1"
LEAGUES = ["nba", "nfl", "mlb", "nhl"]
SPORT_MAP = {"nba": "nba", "nfl": "nfl", "mlb": "mlb", "nhl": "nhl"}
MAIN_MARKETS = {"moneyline", "point_spread", "total_points"}
PREFERRED_BOOKS = ["draftkings", "fanduel", "pinnacle", "betmgm"]
OUTDOOR_SPORTS = {"nfl", "mlb"}


def _headers() -> Dict[str, str]:
    return {"X-API-Key": settings.sharpapi_key}


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return datetime.now(timezone.utc) + timedelta(hours=6)
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return datetime.now(timezone.utc) + timedelta(hours=6)


def _american(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _line(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_rows(payload: Any) -> List[dict]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "odds", "results", "items"):
            val = payload.get(key)
            if isinstance(val, list):
                return val
    return []


def _event_id(row: dict) -> str:
    if row.get("event_id"):
        return str(row["event_id"])
    home = row.get("home_team") or row.get("home") or "home"
    away = row.get("away_team") or row.get("away") or "away"
    league = row.get("league") or row.get("sport") or "game"
    return f"{league}_{away}_{home}".lower().replace(" ", "_")


def _book_rank(book: str) -> int:
    book = (book or "").lower()
    try:
        return PREFERRED_BOOKS.index(book)
    except ValueError:
        return len(PREFERRED_BOOKS)


def _should_replace(ev: dict, field: str, book: str) -> bool:
    meta_key = f"_{field}_book"
    if field not in ev or ev[field] is None:
        return True
    prev = ev.get(meta_key, "")
    return _book_rank(book) < _book_rank(prev)


def _side(row: dict) -> str:
    return str(row.get("team_side") or row.get("selection_type") or "").lower()


def _normalize_event(rows: List[dict]) -> Dict[str, dict]:
    """Group SharpAPI rows into one record per event with ML/spread/total."""
    events: Dict[str, dict] = {}

    for row in rows:
        if row.get("is_main_line") is False:
            continue

        market = str(row.get("market_type") or row.get("market") or "").lower()
        if market not in MAIN_MARKETS:
            continue

        eid = _event_id(row)
        league = str(row.get("league") or "nba").lower()
        sport = SPORT_MAP.get(league, league)

        ev = events.setdefault(
            eid,
            {
                "id": eid,
                "sport": sport,
                "home_team": row.get("home_team") or "Home",
                "away_team": row.get("away_team") or "Away",
                "start_time": _parse_dt(
                    row.get("event_start_time")
                    or row.get("commence_time")
                    or row.get("starts_at")
                    or row.get("start_time")
                ),
                "venue": row.get("venue") or row.get("stadium"),
                "is_outdoor": sport in OUTDOOR_SPORTS,
                "moneyline_home": None,
                "moneyline_away": None,
                "spread_home": None,
                "spread_home_odds": -110,
                "spread_away_odds": -110,
                "total": None,
                "over_odds": -110,
                "under_odds": -110,
            },
        )

        book = str(row.get("sportsbook") or "consensus")
        american = _american(row.get("odds_american"))
        line = _line(row.get("line"))
        side = _side(row)

        if market == "moneyline":
            field = "moneyline_home" if side == "home" else "moneyline_away"
            if _should_replace(ev, field, book):
                ev[field] = american
                ev[f"_{field}_book"] = book
        elif market == "point_spread":
            if side == "home":
                if _should_replace(ev, "spread_home", book):
                    ev["spread_home"] = line
                    ev["spread_home_odds"] = american or -110
                    ev["_spread_home_book"] = book
            elif side == "away":
                if _should_replace(ev, "spread_away_odds", book):
                    ev["spread_away_odds"] = american or -110
                    ev["_spread_away_odds_book"] = book
        elif market == "total_points":
            if line is not None and (ev["total"] is None or _should_replace(ev, "total", book)):
                ev["total"] = line
                ev["_total_book"] = book
            sel = str(row.get("selection_type") or side or row.get("selection") or "").lower()
            if sel == "over" or "over" in sel:
                if _should_replace(ev, "over_odds", book):
                    ev["over_odds"] = american or -110
                    ev["_over_odds_book"] = book
            elif sel == "under" or "under" in sel:
                if _should_replace(ev, "under_odds", book):
                    ev["under_odds"] = american or -110
                    ev["_under_odds_book"] = book

    return events


async def fetch_live_events() -> List[dict]:
    if not settings.sharpapi_key:
        return []

    params = {
        "league": ",".join(LEAGUES),
        "market": "main",
        "live": "false",
        "limit": 200,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(f"{SHARP_BASE}/odds", headers=_headers(), params=params)
        if resp.status_code == 401:
            raise RuntimeError("SharpAPI key rejected — check SHARPAPI_KEY")
        resp.raise_for_status()
        rows = _extract_rows(resp.json())

    events = _normalize_event(rows)
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=36)
    result = []
    for ev in events.values():
        if ev["start_time"] < now - timedelta(hours=2):
            continue
        if ev["start_time"] > horizon:
            continue
        if ev.get("moneyline_home") or ev.get("spread_home") or ev.get("total"):
            result.append(ev)
    return result


# Normalization + persistence now live in app.services.odds (source-agnostic).
# This module only fetches and shapes SharpAPI rows into event dicts.
