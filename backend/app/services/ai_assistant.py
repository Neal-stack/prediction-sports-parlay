"""User-facing AI: parlay summaries and chat.

The heavy analytical lifting already happened in the research pass
(services/research.py), whose structured signals are cached per game. Here we
only synthesize honest, human-readable text and answer questions, reusing that
cache instead of making fresh research calls. Gemini is primary (free); OpenAI
is an optional fallback via llm_text.
"""
from __future__ import annotations

from typing import List, Optional

from app.config import settings
from app.models.schemas import ChatRequest, ChatResponse, ParlayResponse
from app.services.context import cached_context
from app.services.llm import llm_text


def _offline_message() -> str:
    return "AI assistant is offline — add GEMINI_API_KEY to backend/.env to enable it."


def _legs_summary(parlay: ParlayResponse) -> str:
    return "\n".join(
        f"- {l.selection} ({l.market}) @ {l.odds_american:+d} | "
        f"implied {l.implied_prob:.0%}, model {l.win_probability:.0%}, "
        f"edge {(l.win_probability - l.implied_prob):+.0%} — {l.rationale}"
        for l in parlay.legs
    )


def _research_context(parlay: ParlayResponse) -> str:
    """Pull cached research factors for the games in this slip."""
    lines: List[str] = []
    seen: set = set()
    for leg in parlay.legs:
        if leg.game_id in seen:
            continue
        seen.add(leg.game_id)
        ctx = cached_context(leg.game_id)
        if not ctx:
            continue
        factors = ctx.get("key_factors") or []
        narrative = ctx.get("narrative") or ""
        if factors or narrative:
            bits = "; ".join(factors)
            lines.append(f"{leg.matchup}: {bits}{(' — ' + narrative) if narrative else ''}")
    return "\n".join(lines) if lines else "No extra research signals cached."


async def explain_parlay(parlay: ParlayResponse) -> Optional[str]:
    if not settings.ai_enabled:
        return None
    prompt = f"""Write a 3-4 sentence summary of this parlay for the user. Be honest — no bet is guaranteed.
Highlight where the model sees edge vs the market and the main risk.

Parlay: {parlay.risk} {len(parlay.legs)}-leg, {parlay.combined_american:+d}, est. win {parlay.estimated_win_prob:.1%}.

Legs:
{_legs_summary(parlay)}

Research signals:
{_research_context(parlay)}"""
    return await llm_text(prompt, max_tokens=320)


async def chat_about_parlay(
    req: ChatRequest, parlay: Optional[ParlayResponse] = None
) -> ChatResponse:
    if not settings.ai_enabled:
        return ChatResponse(reply=_offline_message(), provider=None)

    if parlay:
        prompt = f"""You are a sports betting analyst. Answer the user's question in 2-5 sentences.
Use the parlay details and research below. Never claim a guaranteed win. Be specific and honest.

Parlay: {parlay.risk} {len(parlay.legs)}-leg, {parlay.combined_american:+d}, est. win {parlay.estimated_win_prob:.1%}.
Legs:
{_legs_summary(parlay)}

Research signals:
{_research_context(parlay)}

User question: {req.message}"""
    else:
        prompt = (
            "You are a sports betting analyst. Answer in 2-5 sentences, no guarantees.\n"
            f"User question: {req.message}"
        )

    reply = await llm_text(prompt, max_tokens=320)
    return ChatResponse(reply=reply or _offline_message(), provider=settings.ai_provider)
