from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List

import httpx

from app.config import settings
from app.db.supabase import get_supabase

POSITIVE = {"win", "healthy", "return", "dominant", "sharp", "hot", "streak", "upgrade", "cleared"}
NEGATIVE = {"injury", "out", "doubtful", "suspend", "loss", "struggle", "cold", "setback", "ruled"}


def _sentiment(text: str) -> float:
    lower = text.lower()
    score = 0.0
    for w in POSITIVE:
        if w in lower:
            score += 0.02
    for w in NEGATIVE:
        if w in lower:
            score -= 0.03
    return max(-0.15, min(0.15, score))


async def fetch_team_news(team: str) -> List[dict]:
    if not settings.gnews_api_key:
        return []

    params = {
        "q": f'"{team}" sports',
        "lang": "en",
        "max": 5,
        "apikey": settings.gnews_api_key,
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get("https://gnews.io/api/v4/search", params=params)
            if resp.status_code != 200:
                return []
            return resp.json().get("articles") or []
    except Exception:
        return []


async def news_context_for_game(home_team: str, away_team: str, game_id: str) -> Dict[str, float]:
    home_articles = await fetch_team_news(home_team)
    away_articles = await fetch_team_news(away_team)

    home_sent = sum(_sentiment(a.get("title", "")) for a in home_articles)
    away_sent = sum(_sentiment(a.get("title", "")) for a in away_articles)
    net = home_sent - away_sent

    sb = get_supabase()
    if sb:
        for articles, team in ((home_articles, home_team), (away_articles, away_team)):
            for art in articles[:3]:
                sb.table("team_news").insert(
                    {
                        "team": team,
                        "headline": art.get("title", "")[:500],
                        "url": art.get("url"),
                        "published_at": art.get("publishedAt"),
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                    }
                ).execute()

    return {"news_sentiment": round(net, 4), "home_news": round(home_sent, 4), "away_news": round(away_sent, 4)}
