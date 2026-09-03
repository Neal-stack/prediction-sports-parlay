"""Real player-prop lines from The Odds API.

Until now prop prices were derived from our own probability, which made the
"edge" on every prop equal to the vig by construction — we had no independent
number to disagree with. This module fetches what books are actually offering,
so a leg can be judged the only way that matters: our probability against a
de-vigged market price.

Cost note: props live on the per-EVENT endpoint, and the quota charge is
[markets returned] x [regions]. One market for one game is 1 credit, so a
4-market pull across a 10-game slate is ~40. Results are cached hard.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

ODDS_BASE = "https://api.the-odds-api.com/v4"

SPORT_KEYS = {"nba": "basketball_nba", "nfl": "americanfootball_nfl",
              "mlb": "baseball_mlb", "nhl": "icehockey_nhl"}

# The Odds API market key <-> our stat key.
MARKET_STATS = {
    "player_points": "points",
    "player_rebounds": "rebounds",
    "player_assists": "assists",
    "player_threes": "3pm",
}
STAT_MARKETS = {v: k for k, v in MARKET_STATS.items()}

_cache: Dict[str, object] = {}
_cache_ts: Dict[str, datetime] = {}
_requests_remaining: Optional[int] = None


def requests_remaining() -> Optional[int]:
    return _requests_remaining


def american_to_prob(odds: int) -> float:
    return abs(odds) / (abs(odds) + 100) if odds < 0 else 100 / (odds + 100)


def devig(over_price: int, under_price: int) -> Optional[float]:
    """True probability of the OVER with the book's hold removed.

    Raw implied prices sum to >1; the excess is the book's margin. Treating raw
    implied as the market's opinion would read the hold as our own edge.
    """
    if over_price is None or under_price is None:
        return None
    o, u = american_to_prob(over_price), american_to_prob(under_price)
    total = o + u
    if total <= 0:
        return None
    return round(o / total, 4)


def normalize_event_props(payload: dict) -> List[dict]:
    """Flatten one event's bookmaker payload into per (player, stat, line) rows.

    Keeps the BEST available price on each side across books — line shopping is
    the most reliable edge in the system and costs nothing extra, since every
    book already came back in the same response.
    """
    best: Dict[Tuple[str, str, float], dict] = {}
    for book in payload.get("bookmakers") or []:
        bkey = book.get("key", "")
        for market in book.get("markets") or []:
            stat = MARKET_STATS.get(market.get("key", ""))
            if not stat:
                continue
            for out in market.get("outcomes") or []:
                player = (out.get("description") or "").strip()
                side = (out.get("name") or "").strip().lower()
                point, price = out.get("point"), out.get("price")
                if not player or side not in ("over", "under") or point is None or price is None:
                    continue
                key = (player, stat, float(point))
                row = best.setdefault(key, {
                    "player": player, "stat": stat, "line": float(point),
                    "over_odds": None, "under_odds": None,
                    "over_book": None, "under_book": None,
                })
                field, bookfield = f"{side}_odds", f"{side}_book"
                # Higher American number is always the better price for the bettor.
                if row[field] is None or int(price) > row[field]:
                    row[field] = int(price)
                    row[bookfield] = bkey

    rows = []
    for row in best.values():
        row["devig_over_prob"] = devig(row["over_odds"], row["under_odds"])
        rows.append(row)
    rows.sort(key=lambda r: (r["player"], r["stat"], r["line"]))
    return rows


async def _get(url: str, params: dict, ttl: timedelta) -> Optional[dict]:
    global _requests_remaining
    # Cache key excludes the API key so secrets never land in cache state.
    key = url + repr({k: v for k, v in sorted(params.items()) if k != "apiKey"})
    ts = _cache_ts.get(key)
    if ts and datetime.now(timezone.utc) - ts < ttl:
        return _cache.get(key)
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.get(url, params=params)
        remaining = resp.headers.get("x-requests-remaining")
        if remaining is not None:
            try:
                _requests_remaining = int(remaining)
            except ValueError:
                pass
        if resp.status_code != 200:
            logger.warning("Odds API prop request failed %s: %s", resp.status_code, resp.text[:200])
            return _cache.get(key)
        data = resp.json()
    except Exception:
        logger.exception("Odds API prop request errored: %s", url)
        return _cache.get(key)
    _cache[key] = data
    _cache_ts[key] = datetime.now(timezone.utc)
    return data


async def list_events(sport: str) -> List[dict]:
    """Upcoming events for a sport. The events endpoint is free (0 credits)."""
    sk = SPORT_KEYS.get(sport)
    if not sk or not settings.odds_api_key:
        return []
    data = await _get(f"{ODDS_BASE}/sports/{sk}/events",
                      {"apiKey": settings.odds_api_key}, timedelta(hours=1))
    return data if isinstance(data, list) else []


def match_event(events: List[dict], home_team: str, away_team: str) -> Optional[str]:
    """Odds API event id for a matchup, matched on team names."""
    def norm(s: str) -> str:
        return (s or "").lower().replace(".", "").strip()

    def same(a: str, b: str) -> bool:
        a, b = norm(a), norm(b)
        return bool(a) and bool(b) and (a in b or b in a or a.split()[-1] == b.split()[-1])

    for ev in events:
        if same(ev.get("home_team", ""), home_team) and same(ev.get("away_team", ""), away_team):
            return ev.get("id")
    return None


async def fetch_event_props(
    sport: str, event_id: str, stats: Tuple[str, ...] = ("points", "rebounds", "assists", "3pm")
) -> List[dict]:
    """Book prop lines for one event. Costs [markets returned] credits."""
    sk = SPORT_KEYS.get(sport)
    if not sk or not settings.odds_api_key or not event_id:
        return []
    markets = ",".join(STAT_MARKETS[s] for s in stats if s in STAT_MARKETS)
    if not markets:
        return []
    data = await _get(
        f"{ODDS_BASE}/sports/{sk}/events/{event_id}/odds",
        {"apiKey": settings.odds_api_key, "regions": "us",
         "markets": markets, "oddsFormat": "american"},
        timedelta(minutes=20),
    )
    return normalize_event_props(data) if isinstance(data, dict) else []
