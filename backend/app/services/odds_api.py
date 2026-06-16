"""The Odds API client (optional, richer multi-book odds).

Free tier is 500 credits/month, so this is only used when ODDS_API_KEY is set.
ESPN scoreboard odds remain the free default. We sync only in-season leagues
and read the `x-requests-remaining` header to surface remaining budget.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

ODDS_BASE = "https://api.the-odds-api.com/v4"
SPORT_KEYS = {
    "basketball_nba": "nba",
    "americanfootball_nfl": "nfl",
    "baseball_mlb": "mlb",
    "icehockey_nhl": "nhl",
}
OUTDOOR_SPORTS = {"nfl", "mlb"}
PREFERRED_BOOKS = ["draftkings", "fanduel", "betmgm", "caesars", "pinnacle"]

_requests_remaining: Optional[int] = None


def requests_remaining() -> Optional[int]:
    return _requests_remaining


def _int(value) -> Optional[int]:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _book_rank(key: str) -> int:
    try:
        return PREFERRED_BOOKS.index((key or "").lower())
    except ValueError:
        return len(PREFERRED_BOOKS)


def _normalize_event(raw: dict) -> Optional[dict]:
    sport = SPORT_KEYS.get(raw.get("sport_key", ""))
    if not sport:
        return None
    home = raw.get("home_team")
    away = raw.get("away_team")
    if not home or not away:
        return None

    ev = {
        "id": f"oddsapi_{raw.get('id')}",
        "sport": sport,
        "home_team": home,
        "away_team": away,
        "start_time": raw.get("commence_time"),
        "venue": None,
        "is_outdoor": sport in OUTDOOR_SPORTS,
        "moneyline_home": None,
        "moneyline_away": None,
        "spread_home": None,
        "spread_home_odds": -110,
        "spread_away_odds": -110,
        "total": None,
        "over_odds": -110,
        "under_odds": -110,
    }

    for book in sorted(raw.get("bookmakers", []) or [], key=lambda b: _book_rank(b.get("key", ""))):
        for market in book.get("markets", []) or []:
            key = market.get("key")
            outcomes = market.get("outcomes", []) or []
            if key == "h2h":
                for o in outcomes:
                    if o.get("name") == home and ev["moneyline_home"] is None:
                        ev["moneyline_home"] = _int(o.get("price"))
                    elif o.get("name") == away and ev["moneyline_away"] is None:
                        ev["moneyline_away"] = _int(o.get("price"))
            elif key == "spreads":
                for o in outcomes:
                    if o.get("name") == home and ev["spread_home"] is None:
                        ev["spread_home"] = _float(o.get("point"))
                        ev["spread_home_odds"] = _int(o.get("price")) or -110
                    elif o.get("name") == away and ev["spread_away_odds"] == -110:
                        ev["spread_away_odds"] = _int(o.get("price")) or -110
            elif key == "totals":
                for o in outcomes:
                    name = (o.get("name") or "").lower()
                    if name == "over":
                        if ev["total"] is None:
                            ev["total"] = _float(o.get("point"))
                        ev["over_odds"] = _int(o.get("price")) or -110
                    elif name == "under":
                        ev["under_odds"] = _int(o.get("price")) or -110

    if ev["moneyline_home"] or ev["spread_home"] is not None or ev["total"] is not None:
        return ev
    return None


async def fetch_live_events() -> List[dict]:
    global _requests_remaining
    if not settings.odds_api_key:
        return []

    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=36)
    events: List[dict] = []

    async with httpx.AsyncClient(timeout=30.0) as client:
        for sport_key in SPORT_KEYS:
            try:
                resp = await client.get(
                    f"{ODDS_BASE}/sports/{sport_key}/odds",
                    params={
                        "apiKey": settings.odds_api_key,
                        "regions": "us",
                        "markets": "h2h,spreads,totals",
                        "oddsFormat": "american",
                    },
                )
                remaining = resp.headers.get("x-requests-remaining")
                if remaining is not None:
                    _requests_remaining = _int(remaining)
                if resp.status_code == 401:
                    raise RuntimeError("The Odds API key rejected — check ODDS_API_KEY")
                if resp.status_code == 422:
                    # League out of season; skip quietly.
                    continue
                resp.raise_for_status()
                rows = resp.json()
            except RuntimeError:
                raise
            except Exception:
                logger.exception("Odds API fetch failed for %s", sport_key)
                continue

            for raw in rows or []:
                ev = _normalize_event(raw)
                if not ev or not ev.get("start_time"):
                    continue
                start = datetime.fromisoformat(str(ev["start_time"]).replace("Z", "+00:00"))
                if start < now - timedelta(hours=2) or start > horizon:
                    continue
                events.append(ev)

    return events
