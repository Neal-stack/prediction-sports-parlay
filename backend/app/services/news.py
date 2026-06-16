"""Team news headlines from ESPN (free, no key).

We deliberately do NOT score sentiment with a keyword bag anymore — that was
noisy and often wrong ("returns from injury" read as negative). Instead we hand
the raw headlines to the Gemini research pass, which reads them in context and
produces a structured signal. This keeps Gemini's free budget focused on
actual analysis rather than wasting it on data the model can read itself.
"""
from __future__ import annotations

from typing import Dict, List

from app.services import espn


async def news_headlines_for_game(
    sport: str, home_team: str, away_team: str
) -> Dict[str, List[str]]:
    """Return recent headlines mentioning each team, for the research prompt."""
    home_articles = await espn.news_for_team(sport, home_team)
    away_articles = await espn.news_for_team(sport, away_team)

    def _titles(articles: List[dict]) -> List[str]:
        out = []
        for a in articles:
            title = a.get("headline") or ""
            if title:
                out.append(title.strip())
        return out[:5]

    return {"home_news": _titles(home_articles), "away_news": _titles(away_articles)}
