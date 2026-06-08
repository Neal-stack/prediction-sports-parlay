from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

import httpx

from app.db.supabase import get_supabase

# Approximate home venue coordinates for outdoor NFL/MLB weather context
TEAM_COORDS: Dict[str, Tuple[float, float]] = {
    "arizona cardinals": (33.5276, -112.2626),
    "atlanta falcons": (33.7553, -84.4006),
    "baltimore ravens": (39.2780, -76.6227),
    "buffalo bills": (42.7738, -78.7870),
    "carolina panthers": (35.2258, -80.8528),
    "chicago bears": (41.8623, -87.6167),
    "cincinnati bengals": (39.0954, -84.5160),
    "cleveland browns": (41.5061, -81.6995),
    "dallas cowboys": (32.7473, -97.0945),
    "denver broncos": (39.7439, -105.0201),
    "detroit lions": (42.3400, -83.0456),
    "green bay packers": (44.5013, -88.0622),
    "houston texans": (29.6847, -95.4107),
    "indianapolis colts": (39.7601, -86.1639),
    "jacksonville jaguars": (30.3239, -81.6373),
    "kansas city chiefs": (39.0489, -94.4839),
    "las vegas raiders": (36.0909, -115.1833),
    "los angeles chargers": (33.9533, -118.3390),
    "los angeles rams": (33.9533, -118.3390),
    "miami dolphins": (25.9580, -80.2389),
    "minnesota vikings": (44.9735, -93.2575),
    "new england patriots": (42.0909, -71.2643),
    "new orleans saints": (29.9511, -90.0812),
    "new york giants": (40.8128, -74.0742),
    "new york jets": (40.8128, -74.0742),
    "philadelphia eagles": (39.9008, -75.1675),
    "pittsburgh steelers": (40.4468, -80.0158),
    "san francisco 49ers": (37.4030, -121.9698),
    "seattle seahawks": (47.5952, -122.3316),
    "tampa bay buccaneers": (27.9759, -82.5033),
    "tennessee titans": (36.1665, -86.7713),
    "washington commanders": (38.9077, -76.8645),
    "boston red sox": (42.3467, -71.0972),
    "new york yankees": (40.8296, -73.9262),
    "chicago cubs": (41.9484, -87.6553),
    "chicago white sox": (41.8300, -87.6338),
    "colorado rockies": (39.7560, -104.9942),
}


def _coords_for_team(team: str) -> Optional[Tuple[float, float]]:
    key = team.lower().strip()
    if key in TEAM_COORDS:
        return TEAM_COORDS[key]
    for name, coords in TEAM_COORDS.items():
        if name in key or key in name:
            return coords
    return None


async def fetch_weather(lat: float, lon: float) -> Optional[dict]:
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "temperature_2m,wind_speed_10m,precipitation_probability",
        "forecast_days": 1,
        "timezone": "UTC",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get("https://api.open-meteo.com/v1/forecast", params=params)
        if resp.status_code != 200:
            return None
        return resp.json()


def _weather_factor(temp_c: float, wind_kmh: float, precip: float, sport: str) -> float:
    """Positive favors scoring (totals); negative favors unders / run game."""
    wind_mph = wind_kmh * 0.621371
    factor = 0.0
    if sport in ("nfl", "mlb"):
        if wind_mph >= 15:
            factor -= 0.06
        elif wind_mph >= 10:
            factor -= 0.03
        if precip >= 50:
            factor -= 0.04
        if temp_c <= 5:
            factor -= 0.02
        if temp_c >= 28 and sport == "mlb":
            factor += 0.02
    return round(factor, 4)


async def sync_weather_for_game(game_id: str, home_team: str, sport: str, is_outdoor: bool) -> Optional[dict]:
    if sport not in ("nfl", "mlb") and not is_outdoor:
        return None

    coords = _coords_for_team(home_team)
    if not coords:
        return None

    data = await fetch_weather(coords[0], coords[1])
    if not data or "hourly" not in data:
        return None

    hourly = data["hourly"]
    temp = float(hourly["temperature_2m"][0])
    wind = float(hourly["wind_speed_10m"][0])
    precip = float(hourly.get("precipitation_probability", [0])[0])
    temp_f = temp * 9 / 5 + 32
    wind_mph = wind * 0.621371

    row = {
        "game_id": game_id,
        "temp_f": round(temp_f, 1),
        "wind_mph": round(wind_mph, 1),
        "conditions": f"temp {round(temp_f)}°F, wind {round(wind_mph)} mph, precip {precip}%",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }

    sb = get_supabase()
    if sb:
        sb.table("weather_cache").upsert(row).execute()

    return {
        "weather_factor": _weather_factor(temp, wind, precip, sport),
        "temp_f": temp_f,
        "wind_mph": wind_mph,
    }
