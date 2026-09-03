"""Player season averages + box scores from ESPN (free, no key).

BallDontLie's free tier no longer exposes /stats or /season_averages (both 401),
so player data comes from ESPN like the rest of the app:
- projections: athlete season "averages" category
- grading: game summary box score

Only the specific players named by the Gemini research pass are looked up
(2-3 per game), so this stays within a handful of cached requests.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

from app.services import espn

logger = logging.getLogger(__name__)

SITE = "https://site.api.espn.com/apis/site/v2/sports"
COMMON = "https://site.api.espn.com/apis/common/v3/sports"

# Our prop stat -> ESPN season-average field name.
AVG_FIELDS = {"points": "avgPoints", "rebounds": "avgRebounds", "assists": "avgAssists"}
# 3PM lives in a combined "made-attempted" field.
THREE_AVG_FIELD = "avgThreePointFieldGoalsMade-avgThreePointFieldGoalsAttempted"

_cache: Dict[str, Any] = {}
_cache_ts: Dict[str, datetime] = {}


def _cache_get(key: str, ttl: timedelta) -> Any:
    ts = _cache_ts.get(key)
    if ts and datetime.now(timezone.utc) - ts < ttl:
        return _cache.get(key)
    return None


def _cache_put(key: str, value: Any) -> None:
    _cache[key] = value
    _cache_ts[key] = datetime.now(timezone.utc)


async def _get(url: str, params: Optional[dict] = None, ttl: timedelta = timedelta(hours=6)) -> Optional[dict]:
    key = url + "?" + "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
    cached = _cache_get(key, ttl)
    if cached is not None:
        return cached
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url, params=params)
            if resp.status_code != 200:
                return _cache.get(key)
            data = resp.json()
    except Exception:
        logger.exception("ESPN player request failed: %s", url)
        return _cache.get(key)
    _cache_put(key, data)
    return data


def _first_made(value: str) -> Optional[float]:
    """Parse '0.9-2.5' (made-attempted) -> 0.9."""
    try:
        return float(str(value).split("-")[0])
    except (ValueError, AttributeError):
        return None


async def _team_id(sport: str, team_name: str) -> Optional[str]:
    path = espn.SPORT_PATHS.get(sport)
    if not path:
        return None
    data = await _get(f"{SITE}/{path[0]}/{path[1]}/teams", ttl=timedelta(days=3))
    if not data:
        return None
    try:
        teams = data["sports"][0]["leagues"][0]["teams"]
    except (KeyError, IndexError):
        return None
    for t in teams:
        team = t.get("team", {})
        if espn.team_match(team.get("displayName", ""), team_name):
            return str(team.get("id"))
    return None


async def _roster(sport: str, team_id: str) -> List[dict]:
    path = espn.SPORT_PATHS.get(sport)
    if not path:
        return []
    data = await _get(f"{SITE}/{path[0]}/{path[1]}/teams/{team_id}/roster", ttl=timedelta(days=1))
    if not data:
        return []
    out = []
    for a in data.get("athletes", []) or []:
        # Some sports nest athletes under position groups.
        if "items" in a:
            for sub in a["items"]:
                out.append({"id": str(sub.get("id")), "name": sub.get("displayName", "")})
        else:
            out.append({"id": str(a.get("id")), "name": a.get("displayName", "")})
    return out


async def _athlete_id(sport: str, home_team: str, away_team: str, player_name: str) -> Optional[str]:
    last = player_name.lower().split()[-1] if player_name else ""
    for team in (home_team, away_team):
        tid = await _team_id(sport, team)
        if not tid:
            continue
        for a in await _roster(sport, tid):
            n = a["name"].lower()
            if n == player_name.lower() or (last and last in n):
                return a["id"]
    return None


async def _athlete_averages(sport: str, athlete_id: str) -> Optional[Dict[str, float]]:
    path = espn.SPORT_PATHS.get(sport)
    if not path:
        return None
    data = await _get(f"{COMMON}/{path[0]}/{path[1]}/athletes/{athlete_id}/stats", ttl=timedelta(hours=12))
    if not data:
        return None

    cat = next((c for c in data.get("categories", []) if c.get("name") == "averages"), None)
    if not cat:
        return None
    names = cat.get("names") or []
    rows = cat.get("statistics") or []
    if not rows:
        return None
    # Latest season = highest year.
    latest = max(rows, key=lambda r: int((r.get("season") or {}).get("year", 0)))
    stats = dict(zip(names, latest.get("stats", [])))

    def _num(field: str) -> Optional[float]:
        try:
            return float(stats[field])
        except (KeyError, ValueError, TypeError):
            return None

    return {
        "points": _num(AVG_FIELDS["points"]),
        "rebounds": _num(AVG_FIELDS["rebounds"]),
        "assists": _num(AVG_FIELDS["assists"]),
        "3pm": _first_made(stats.get(THREE_AVG_FIELD)),
        "minutes": _num("avgMinutes") or 0.0,
    }


async def player_season_averages(
    sport: str, home_team: str, away_team: str, player_name: str
) -> Optional[Dict[str, float]]:
    """Latest-season per-game averages for a named player (projection input)."""
    if sport != "nba":
        return None
    athlete_id = await _athlete_id(sport, home_team, away_team, player_name)
    if not athlete_id:
        return None
    averages = await _athlete_averages(sport, athlete_id)
    if averages:
        averages["player_id"] = athlete_id
    return averages


# --- Box score for grading -------------------------------------------------
BOX_NAME_MAP = {
    "points": "points",
    "rebounds": "rebounds",
    "assists": "assists",
}


async def _event_id_for(sport: str, game_id: str, home_team: str, away_team: str, ref_date: datetime) -> Optional[str]:
    if game_id and game_id.startswith("espn_"):
        return game_id.split("_")[-1]
    # Resolve via scoreboard match for non-ESPN game ids.
    path = espn.SPORT_PATHS.get(sport)
    if not path:
        return None
    for off in (0, -1, 1):
        day = ref_date + timedelta(days=off)
        for g in await espn.fetch_scoreboard(sport, day):
            if (
                g.get("home_team")
                and espn.team_match(g["home_team"], home_team)
                and espn.team_match(g["away_team"], away_team)
            ):
                return g.get("espn_id") or None
    return None


async def fetch_player_box_score(
    sport: str,
    game_id: str,
    home_team: str,
    away_team: str,
    ref_date: datetime,
    *,
    player: str,
    player_id: Optional[str] = None,
) -> Optional[Dict[str, float]]:
    """Final box-score stats {points, rebounds, assists, 3pm} for a player."""
    if sport != "nba":
        return None
    event_id = await _event_id_for(sport, game_id, home_team, away_team, ref_date)
    if not event_id:
        return None

    path = espn.SPORT_PATHS[sport]
    data = await _get(
        f"{SITE}/{path[0]}/{path[1]}/summary", {"event": event_id}, ttl=timedelta(minutes=10)
    )
    if not data:
        return None

    last = player.lower().split()[-1] if player else ""
    for team_block in (data.get("boxscore", {}) or {}).get("players", []) or []:
        for stat_block in team_block.get("statistics", []) or []:
            names = stat_block.get("names") or stat_block.get("keys") or []
            for ath in stat_block.get("athletes", []) or []:
                athlete = ath.get("athlete", {})
                name = (athlete.get("displayName") or "").lower()
                if not (name == player.lower() or (last and last in name)):
                    continue
                values = dict(zip(names, ath.get("stats", [])))

                def _num(*keys) -> Optional[float]:
                    for k in keys:
                        if k in values:
                            try:
                                return float(values[k])
                            except (ValueError, TypeError):
                                pass
                    return None

                three = values.get("threePointFieldGoalsMade-threePointFieldGoalsAttempted")
                return {
                    "points": _num("points", "PTS"),
                    "rebounds": _num("rebounds", "totalRebounds", "REB"),
                    "assists": _num("assists", "AST"),
                    "3pm": _first_made(three),
                }
    return None

# --- Per-game logs (empirical distributions) --------------------------------
# ESPN publishes a full game log per athlete, free and unkeyed. Counting how
# often a player ACTUALLY cleared a line beats assuming a bell curve around his
# season average — especially for a specific player/stat pair, where real
# consistency varies enormously (a big man's rebounds are far steadier than a
# guard's 3PM).
WEB_API = "https://site.web.api.espn.com/apis/common/v3/sports"

# Our stat key -> ESPN game-log column name.
LOG_FIELDS = {"points": "points", "rebounds": "totalRebounds", "assists": "assists"}
LOG_THREE = "threePointFieldGoalsMade-threePointFieldGoalsAttempted"

MIN_LOG_GAMES = 8  # below this the sample is too thin to trust on its own

# Reduced logs are cached separately: the raw payload is ~900KB per athlete and
# we only need a handful of numbers from it.
_log_cache: Dict[str, Any] = {}
_log_cache_ts: Dict[str, datetime] = {}


async def player_game_log(
    sport: str, athlete_id: str, *, limit: int = 25
) -> List[Dict[str, float]]:
    """Most-recent-first per-game stat lines for an athlete (games played only).

    Games with zero minutes (DNP) are excluded: whether he suits up is handled
    separately by the availability model, so counting DNPs here would penalise
    the stat distribution twice.
    """
    if sport != "nba" or not athlete_id:
        return []

    key = f"{sport}:{athlete_id}:{limit}"
    ts = _log_cache_ts.get(key)
    if ts and datetime.now(timezone.utc) - ts < timedelta(hours=12):
        return _log_cache.get(key, [])

    path = espn.SPORT_PATHS.get(sport)
    if not path:
        return []
    url = f"{WEB_API}/{path[0]}/{path[1]}/athletes/{athlete_id}/gamelog"
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return _log_cache.get(key, [])
            data = resp.json()
    except Exception:
        logger.exception("ESPN gamelog request failed for athlete %s", athlete_id)
        return _log_cache.get(key, [])

    names = data.get("names") or []
    idx = {name: i for i, name in enumerate(names)}

    def _col(row: List[str], name: str) -> Optional[float]:
        i = idx.get(name)
        if i is None or i >= len(row):
            return None
        try:
            return float(row[i])
        except (ValueError, TypeError):
            return None

    out: List[Dict[str, float]] = []
    for season in data.get("seasonTypes") or []:
        for cat in season.get("categories") or []:
            for event in cat.get("events") or []:
                row = event.get("stats") or []
                if not row:
                    continue
                minutes = _col(row, "minutes") or 0.0
                if minutes <= 0:  # DNP — availability model's problem, not ours
                    continue
                line = {
                    "minutes": minutes,
                    "points": _col(row, LOG_FIELDS["points"]),
                    "rebounds": _col(row, LOG_FIELDS["rebounds"]),
                    "assists": _col(row, LOG_FIELDS["assists"]),
                    "3pm": _first_made(row[idx[LOG_THREE]]) if LOG_THREE in idx and idx[LOG_THREE] < len(row) else None,
                }
                out.append(line)
                if len(out) >= limit:
                    break
            if len(out) >= limit:
                break
        if len(out) >= limit:
            break

    _log_cache[key] = out
    _log_cache_ts[key] = datetime.now(timezone.utc)
    return out


def stat_series(log: List[Dict[str, float]], stat: str) -> List[float]:
    """Just the values for one stat, dropping games where it is missing."""
    return [g[stat] for g in log if g.get(stat) is not None]
