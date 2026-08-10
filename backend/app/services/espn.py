"""ESPN unofficial API client — free, no key required.

Covers scores, injuries, news, and team power stats for NBA/NFL/MLB/NHL.
Replaces the paid API-Sports and GNews integrations. All responses are
cached in-process with a short TTL so a single slate refresh costs only a
handful of requests.

Endpoint shapes are best-effort parsed defensively: ESPN occasionally
reshapes payloads, so every accessor tolerates missing/renamed fields.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

# sport key -> ESPN (sport, league) path segments
SPORT_PATHS: Dict[str, Tuple[str, str]] = {
    "nba": ("basketball", "nba"),
    "nfl": ("football", "nfl"),
    "mlb": ("baseball", "mlb"),
    "nhl": ("hockey", "nhl"),
    "wc": ("soccer", "fifa.world"),  # FIFA World Cup
}

# Sports with a 3-way result (win/draw/loss) instead of 2-way.
SOCCER_SPORTS = {"wc"}

SITE_BASE = "https://site.api.espn.com/apis/site/v2/sports"
STANDINGS_BASE = "https://site.api.espn.com/apis/v2/sports"

FINAL_STATES = {"post"}
OUTDOOR_SPORTS = {"nfl", "mlb", "wc"}


def is_soccer(sport: str) -> bool:
    return sport.lower() in SOCCER_SPORTS


def _implied_from_american(american: int) -> float:
    return abs(american) / (abs(american) + 100) if american < 0 else 100 / (american + 100)


def _american_from_implied(implied: float) -> int:
    implied = max(0.02, min(0.95, implied))
    if implied >= 0.5:
        return -int(round(implied / (1 - implied) * 100))
    return int(round((1 - implied) / implied * 100))

_cache: Dict[str, Any] = {}
_cache_ts: Dict[str, datetime] = {}
_DEFAULT_TTL = timedelta(minutes=20)


async def _get(url: str, params: Optional[dict] = None, ttl: timedelta = _DEFAULT_TTL) -> Optional[dict]:
    key = url + "?" + "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
    now = datetime.now(timezone.utc)
    cached_at = _cache_ts.get(key)
    if cached_at and now - cached_at < ttl and key in _cache:
        return _cache[key]

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url, params=params)
            if resp.status_code != 200:
                logger.warning("ESPN %s returned %s", url, resp.status_code)
                return _cache.get(key)
            data = resp.json()
    except Exception:
        logger.exception("ESPN request failed: %s", url)
        return _cache.get(key)

    _cache[key] = data
    _cache_ts[key] = now
    return data


def _norm(name: str) -> str:
    return (name or "").lower().strip()


def team_match(a: str, b: str) -> bool:
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    # last token (nickname) match: "Boston Celtics" ~ "Celtics"
    return a.split()[-1] == b.split()[-1]


# --------------------------------------------------------------------------- #
# Scoreboard / scores
# --------------------------------------------------------------------------- #
async def fetch_scoreboard(sport: str, date: Optional[datetime] = None) -> List[dict]:
    """Return normalized games for a day: teams, scores, status."""
    path = SPORT_PATHS.get(sport)
    if not path:
        return []
    params = {}
    if date:
        params["dates"] = date.strftime("%Y%m%d")

    data = await _get(f"{SITE_BASE}/{path[0]}/{path[1]}/scoreboard", params, ttl=timedelta(minutes=3))
    if not data:
        return []

    games: List[dict] = []
    for event in data.get("events", []) or []:
        comps = event.get("competitions") or []
        if not comps:
            continue
        comp = comps[0]
        competitors = comp.get("competitors") or []
        home = away = None
        home_score = away_score = None
        for c in competitors:
            name = (c.get("team") or {}).get("displayName") or ""
            score = c.get("score")
            try:
                score = int(score) if score is not None else None
            except (TypeError, ValueError):
                score = None
            if c.get("homeAway") == "home":
                home, home_score = name, score
            elif c.get("homeAway") == "away":
                away, away_score = name, score

        status = ((comp.get("status") or {}).get("type") or {})
        state = str(status.get("state") or "").lower()  # pre | in | post
        completed = bool(status.get("completed"))
        game_status = "final" if (state in FINAL_STATES or completed) else "live" if state == "in" else "scheduled"

        games.append(
            {
                "espn_id": str(event.get("id") or ""),
                "home_team": home,
                "away_team": away,
                "home_score": home_score,
                "away_score": away_score,
                "game_status": game_status,
                "start_time": event.get("date"),
            }
        )
    return games


def _parse_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _parse_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _best_odds_block(odds: List[dict]) -> Optional[dict]:
    """Pick the highest-priority odds provider that has usable numbers."""
    odds = [o for o in (odds or []) if isinstance(o, dict)]
    if not odds:
        return None
    ranked = sorted(odds, key=lambda o: (o.get("provider") or {}).get("priority", 99))
    for block in ranked:
        if (
            block.get("overUnder") is not None
            or block.get("spread") is not None
            or (block.get("homeTeamOdds") or {}).get("moneyLine") is not None
        ):
            return block
    return ranked[0]


async def fetch_odds_events(sport: str, date: Optional[datetime] = None) -> List[dict]:
    """Build normalized odds events from the ESPN scoreboard (free, unlimited).

    Returns the same dict shape the odds pipeline persists, including
    moneyline / spread / total when ESPN exposes a betting provider.
    """
    path = SPORT_PATHS.get(sport)
    if not path:
        return []
    params = {}
    if date:
        params["dates"] = date.strftime("%Y%m%d")

    data = await _get(
        f"{SITE_BASE}/{path[0]}/{path[1]}/scoreboard", params, ttl=timedelta(minutes=10)
    )
    if not data:
        return []

    events: List[dict] = []
    for event in data.get("events", []) or []:
        comps = event.get("competitions") or []
        if not comps:
            continue
        comp = comps[0]
        competitors = comp.get("competitors") or []
        home = away = None
        home_abbr = away_abbr = ""
        for c in competitors:
            team = c.get("team") or {}
            name = team.get("displayName") or ""
            abbr = team.get("abbreviation") or ""
            if c.get("homeAway") == "home":
                home, home_abbr = name, abbr
            elif c.get("homeAway") == "away":
                away, away_abbr = name, abbr
        if not home or not away:
            continue

        venue = (comp.get("venue") or {}).get("fullName")
        indoor = (comp.get("venue") or {}).get("indoor")
        is_outdoor = (not indoor) if indoor is not None else sport in OUTDOOR_SPORTS

        ev = {
            "id": f"espn_{sport}_{event.get('id')}",
            "sport": sport,
            "home_team": home,
            "away_team": away,
            "start_time": event.get("date"),
            "venue": venue,
            "is_outdoor": is_outdoor,
            "moneyline_home": None,
            "moneyline_away": None,
            "draw_odds": None,
            "spread_home": None,
            "spread_home_odds": -110,
            "spread_away_odds": -110,
            "total": None,
            "over_odds": -110,
            "under_odds": -110,
        }

        block = _best_odds_block(comp.get("odds") or [])
        if block:
            if is_soccer(sport):
                _fill_soccer_odds(ev, block, home_abbr, away_abbr)
            else:
                home_odds = block.get("homeTeamOdds") or {}
                away_odds = block.get("awayTeamOdds") or {}
                ev["moneyline_home"] = _parse_int(home_odds.get("moneyLine"))
                ev["moneyline_away"] = _parse_int(away_odds.get("moneyLine"))
                ev["spread_home"] = _parse_float(block.get("spread"))
                ev["spread_home_odds"] = _parse_int(home_odds.get("spreadOdds")) or -110
                ev["spread_away_odds"] = _parse_int(away_odds.get("spreadOdds")) or -110
                ev["total"] = _parse_float(block.get("overUnder"))

        if (
            ev["moneyline_home"]
            or ev["draw_odds"]
            or ev["spread_home"] is not None
            or ev["total"] is not None
        ):
            events.append(ev)
    return events


def _fill_soccer_odds(ev: dict, block: dict, home_abbr: str, away_abbr: str) -> None:
    """Reconstruct 3-way prices from ESPN soccer odds.

    ESPN gives the total (overUnder), the draw moneyline, and `details` like
    "SUI -175" — the listed side's abbreviation + price. The third price is
    derived from a typical ~7% overround so every outcome has an implied number.
    """
    ev["total"] = _parse_float(block.get("overUnder"))
    draw_ml = _parse_int((block.get("drawOdds") or {}).get("moneyLine"))
    ev["draw_odds"] = draw_ml

    details = str(block.get("details") or "").strip()  # e.g. "SUI -175"
    listed_ml = None
    listed_side = None
    parts = details.rsplit(" ", 1)
    if len(parts) == 2:
        abbr, price = parts
        listed_ml = _parse_int(price.replace("+", ""))
        if abbr and home_abbr and abbr.upper() == home_abbr.upper():
            listed_side = "home"
        elif abbr and away_abbr and abbr.upper() == away_abbr.upper():
            listed_side = "away"

    if listed_ml is not None and listed_side and draw_ml is not None:
        if listed_side == "home":
            ev["moneyline_home"] = listed_ml
        else:
            ev["moneyline_away"] = listed_ml
        # Derive the missing side from a ~7% overround.
        known = _implied_from_american(listed_ml) + _implied_from_american(draw_ml)
        other_implied = max(0.05, 1.07 - known)
        other_ml = _american_from_implied(other_implied)
        if listed_side == "home":
            ev["moneyline_away"] = other_ml
        else:
            ev["moneyline_home"] = other_ml


LEADER_STAT = {
    "pointsPerGame": "points",
    "reboundsPerGame": "rebounds",
    "assistsPerGame": "assists",
}


async def _resolve_event_id(
    sport: str, game_id: str, home_team: str, away_team: str, date: Optional[datetime]
) -> Optional[str]:
    if game_id and game_id.startswith("espn_"):
        return game_id.split("_")[-1]
    days = [date] if date else [datetime.now(timezone.utc)]
    if date:
        days += [date - timedelta(days=1), date + timedelta(days=1)]
    for day in days:
        for g in await fetch_scoreboard(sport, day):
            if (
                g.get("home_team")
                and team_match(g["home_team"], home_team)
                and team_match(g["away_team"], away_team)
            ):
                return g.get("espn_id") or None
    return None


async def game_leaders(
    sport: str,
    game_id: str,
    home_team: str,
    away_team: str,
    date: Optional[datetime] = None,
) -> List[dict]:
    """Per-team season stat leaders for a matchup (free fallback for prop angles).

    Uses the per-event summary, which carries leaders even for upcoming games.
    Returns [{player, player_id, stat, avg}], no AI needed.
    """
    path = SPORT_PATHS.get(sport)
    if not path:
        return []
    event_id = await _resolve_event_id(sport, game_id, home_team, away_team, date)
    if not event_id:
        return []

    data = await _get(
        f"{SITE_BASE}/{path[0]}/{path[1]}/summary", {"event": event_id}, ttl=timedelta(minutes=30)
    )
    if not data:
        return []

    out: List[dict] = []
    seen = set()
    for team_block in data.get("leaders", []) or []:
        for cat in team_block.get("leaders", []) or []:
            stat = LEADER_STAT.get(cat.get("name", ""))
            if not stat:
                continue
            top = (cat.get("leaders") or [{}])[0]
            athlete = top.get("athlete") or {}
            name = athlete.get("displayName")
            if not name or (name, stat) in seen:
                continue
            seen.add((name, stat))
            try:
                avg = float(top.get("value"))
            except (TypeError, ValueError):
                avg = None
            out.append(
                {"player": name, "player_id": str(athlete.get("id")), "stat": stat, "avg": avg}
            )
    return out


async def teams_playing_on(sport: str, date: datetime) -> List[str]:
    """Team display names with a game on the given date (for rest/B2B calc)."""
    teams: List[str] = []
    for g in await fetch_scoreboard(sport, date):
        if g.get("home_team"):
            teams.append(g["home_team"])
        if g.get("away_team"):
            teams.append(g["away_team"])
    return teams


async def rest_days(sport: str, team: str, before: datetime, *, look_back: int = 4) -> Optional[int]:
    """Days since a team's previous game. None if none found in the window.

    1 means the team played the day before `before` (a back-to-back).
    """
    for d in range(1, look_back + 1):
        day = before - timedelta(days=d)
        if any(team_match(t, team) for t in await teams_playing_on(sport, day)):
            return d
    return None


async def find_game_result(
    sport: str, home_team: str, away_team: str, start_time: datetime
) -> Optional[dict]:
    """Look up a finished/live game's score by matching teams on the day."""
    for offset in (0, -1, 1):
        day = start_time + timedelta(days=offset)
        for g in await fetch_scoreboard(sport, day):
            if (
                g.get("home_team")
                and team_match(g["home_team"], home_team)
                and team_match(g["away_team"], away_team)
            ):
                if g.get("home_score") is None or g.get("away_score") is None:
                    return None
                return {
                    "game_id": None,
                    "home_team": home_team,
                    "away_team": away_team,
                    "home_score": g["home_score"],
                    "away_score": g["away_score"],
                    "game_status": g["game_status"],
                    "score_display": (
                        f"{away_team} {g['away_score']} @ {home_team} {g['home_score']}"
                    ),
                }
    return None


# --------------------------------------------------------------------------- #
# Injuries (league-wide, one request per sport)
# --------------------------------------------------------------------------- #
async def fetch_injuries(sport: str) -> Dict[str, List[dict]]:
    """Return {team_display_name: [{player, status, position}]} for a league."""
    path = SPORT_PATHS.get(sport)
    if not path:
        return {}

    data = await _get(f"{SITE_BASE}/{path[0]}/{path[1]}/injuries", ttl=timedelta(minutes=30))
    if not data:
        return {}

    result: Dict[str, List[dict]] = {}
    for team_block in data.get("injuries", []) or []:
        team_name = team_block.get("displayName") or (team_block.get("team") or {}).get("displayName")
        if not team_name:
            continue
        entries: List[dict] = []
        for inj in team_block.get("injuries", []) or []:
            athlete = inj.get("athlete") or {}
            status = inj.get("status") or (inj.get("type") or {}).get("description") or "Unknown"
            position = (athlete.get("position") or {}).get("abbreviation") or ""
            entries.append(
                {
                    "player": athlete.get("displayName") or "Unknown",
                    "status": str(status),
                    "position": position,
                }
            )
        if entries:
            result[team_name] = entries
    return result


# --------------------------------------------------------------------------- #
# News (league-wide headlines, used to surface narrative to the LLM)
# --------------------------------------------------------------------------- #
async def fetch_news(sport: str, limit: int = 25) -> List[dict]:
    path = SPORT_PATHS.get(sport)
    if not path:
        return []
    data = await _get(
        f"{SITE_BASE}/{path[0]}/{path[1]}/news", {"limit": limit}, ttl=timedelta(minutes=30)
    )
    if not data:
        return []
    out: List[dict] = []
    for art in data.get("articles", []) or []:
        out.append(
            {
                "headline": art.get("headline") or art.get("title") or "",
                "description": art.get("description") or "",
                "published": art.get("published"),
            }
        )
    return out


async def news_for_team(sport: str, team: str, limit: int = 6) -> List[dict]:
    """Filter league news down to headlines mentioning a team."""
    articles = await fetch_news(sport)
    nick = _norm(team).split()[-1] if team else ""
    hits = [
        a
        for a in articles
        if nick and (nick in _norm(a["headline"]) or nick in _norm(a["description"]))
    ]
    return hits[:limit]


# --------------------------------------------------------------------------- #
# Team power stats (from standings: scoring + win rate)
# --------------------------------------------------------------------------- #
def _stat_value(stats: List[dict], *names: str) -> Optional[float]:
    wanted = {n.lower() for n in names}
    for s in stats or []:
        label = str(s.get("name") or s.get("abbreviation") or s.get("type") or "").lower()
        if label in wanted:
            val = s.get("value")
            if val is None:
                val = s.get("displayValue")
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
    return None


def _walk_entries(node: Any, out: List[dict]) -> None:
    if isinstance(node, dict):
        if "standings" in node and isinstance(node["standings"], dict):
            for entry in node["standings"].get("entries", []) or []:
                out.append(entry)
        for child in node.get("children", []) or []:
            _walk_entries(child, out)
    elif isinstance(node, list):
        for item in node:
            _walk_entries(item, out)


async def fetch_team_stats(sport: str) -> Dict[str, dict]:
    """Return {team_name: {win_pct, avg_pf, avg_pa, games_played}}.

    Scoring averages drive the independent power-rating model. When ESPN omits
    per-game averages we derive them from season totals / games played.
    """
    path = SPORT_PATHS.get(sport)
    if not path:
        return {}

    data = await _get(f"{STANDINGS_BASE}/{path[0]}/{path[1]}/standings", ttl=timedelta(hours=6))
    if not data:
        return {}

    entries: List[dict] = []
    _walk_entries(data, entries)

    result: Dict[str, dict] = {}
    for entry in entries:
        team = (entry.get("team") or {}).get("displayName") or entry.get("displayName")
        if not team:
            continue
        stats = entry.get("stats") or []
        gp = _stat_value(stats, "gamesPlayed") or 0
        win_pct = _stat_value(stats, "winPercent", "winpercent")
        if win_pct is None:
            wins = _stat_value(stats, "wins") or 0
            losses = _stat_value(stats, "losses") or 0
            total = wins + losses
            win_pct = wins / total if total else 0.5

        avg_pf = _stat_value(stats, "avgPointsFor", "pointsForPerGame")
        avg_pa = _stat_value(stats, "avgPointsAgainst", "pointsAgainstPerGame")
        if avg_pf is None:
            pf = _stat_value(stats, "pointsFor")
            avg_pf = pf / gp if pf and gp else None
        if avg_pa is None:
            pa = _stat_value(stats, "pointsAgainst")
            avg_pa = pa / gp if pa and gp else None

        result[team] = {
            "win_pct": round(win_pct, 4) if win_pct is not None else 0.5,
            "avg_pf": round(avg_pf, 2) if avg_pf is not None else None,
            "avg_pa": round(avg_pa, 2) if avg_pa is not None else None,
            "games_played": int(gp),
        }
    return result


def find_team_stats(stats: Dict[str, dict], team: str) -> Optional[dict]:
    if team in stats:
        return stats[team]
    for name, row in stats.items():
        if team_match(name, team):
            return row
    return None
