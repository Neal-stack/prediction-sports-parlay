from __future__ import annotations

import json
from typing import Optional

import httpx

from app.config import settings
from app.models.schemas import ChatRequest, ChatResponse, ParlayResponse

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.0-flash:generateContent"
)
OPENAI_URL = "https://api.openai.com/v1/chat/completions"

COMPLEX_CHAT_HINTS = (
    "should",
    "compare",
    "risk",
    "bold",
    "safe",
    "change",
    "instead",
    "better",
    "recommend",
    "guarantee",
    "strategy",
)


async def _openai(prompt: str, *, max_tokens: int = 350) -> Optional[str]:
    if not settings.openai_api_key:
        return None

    payload = {
        "model": settings.openai_model,
        "messages": [
            {"role": "system", "content": "You are a concise sports betting analyst."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.4,
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(OPENAI_URL, headers=headers, json=payload)
            if resp.status_code != 200:
                return None
            data = resp.json()
            choices = data.get("choices") or []
            if not choices:
                return None
            return choices[0].get("message", {}).get("content")
    except Exception:
        return None


async def _gemini(prompt: str, *, max_tokens: int = 400) -> Optional[str]:
    if not settings.gemini_api_key:
        return None

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": max_tokens},
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                GEMINI_URL,
                params={"key": settings.gemini_api_key},
                json=payload,
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            candidates = data.get("candidates") or []
            if not candidates:
                return None
            parts = candidates[0].get("content", {}).get("parts") or []
            return parts[0].get("text") if parts else None
    except Exception:
        return None


def _offline_message() -> str:
    return (
        "AI assistant is offline — add GEMINI_API_KEY and/or OPENAI_API_KEY to backend/.env."
    )


def _legs_summary(parlay: ParlayResponse) -> str:
    return "\n".join(
        f"- {l.selection} ({l.market}) @ {l.odds_american:+d}, "
        f"implied {l.implied_prob:.0%}, model {l.win_probability:.0%} — {l.rationale}"
        for l in parlay.legs
    )


async def _gemini_extract_signals(parlay: ParlayResponse) -> Optional[str]:
    """Lightweight pass: bullet signals only (free tier)."""
    prompt = f"""List 3-6 bullet points of key signals for this parlay. One line each.
Focus: line movement, injuries, news, price edge, correlation risk.
Risk: {parlay.risk}. Est. win: {parlay.estimated_win_prob:.1%}.

Legs:
{_legs_summary(parlay)}"""

    return await _gemini(prompt, max_tokens=220)


async def _openai_synthesize(
    parlay: ParlayResponse, gemini_signals: str, *, mode: str
) -> Optional[str]:
    """Shorter second pass: synthesize Gemini bullets into user-facing text."""
    prompt = f"""Using the signal analysis below, write a {mode} for the user.
3-4 sentences. Honest — no bet is guaranteed. Do not repeat every bullet verbatim.

Parlay: {parlay.risk} {len(parlay.legs)}-leg, {parlay.combined_american:+d}, est. win {parlay.estimated_win_prob:.1%}.

Signal analysis (from data layer):
{gemini_signals}"""

    return await _openai(prompt, max_tokens=280)


def _is_complex_chat(message: str) -> bool:
    lower = message.lower()
    return any(hint in lower for hint in COMPLEX_CHAT_HINTS)


async def explain_parlay(parlay: ParlayResponse) -> Optional[str]:
    if settings.dual_ai_enabled:
        signals = await _gemini_extract_signals(parlay)
        if signals:
            synthesis = await _openai_synthesize(parlay, signals, mode="parlay summary")
            if synthesis:
                return synthesis
        # fall through if either step fails

    if settings.gemini_api_key:
        reply = await _gemini(
            f"""Explain this parlay in 3-4 sentences. No guarantees.
Risk: {parlay.risk}. Win est: {parlay.estimated_win_prob:.1%}. Odds: {parlay.combined_american:+d}.

{_legs_summary(parlay)}""",
            max_tokens=350,
        )
        if reply:
            return reply

    if settings.openai_api_key:
        return await _openai(
            f"""Explain this parlay in 3-4 sentences. No guarantees.
Risk: {parlay.risk}. Win est: {parlay.estimated_win_prob:.1%}.

{_legs_summary(parlay)}""",
            max_tokens=350,
        )

    return None


async def chat_about_parlay(
    req: ChatRequest, parlay: Optional[ParlayResponse] = None
) -> ChatResponse:
    provider = settings.ai_provider
    context = json.dumps(parlay.model_dump(mode="json"), default=str) if parlay else "none"

    # Dual mode: Gemini handles facts (free), OpenAI only for synthesis on complex questions
    if settings.dual_ai_enabled:
        if _is_complex_chat(req.message) and parlay:
            signals = await _gemini_extract_signals(parlay)
            if signals:
                reply = await _openai(
                    f"""User question: {req.message}

Parlay signals:
{signals}

Answer in 2-5 sentences using the signals. Never claim guaranteed wins.""",
                    max_tokens=300,
                )
                if reply:
                    return ChatResponse(reply=reply, provider="gemini+openai")

        # Simple chat → Gemini only (saves OpenAI calls)
        reply = await _gemini(
            f"""Answer this sports parlay question in 2-5 sentences. No guarantees.
Parlay context: {context}
User: {req.message}""",
            max_tokens=350,
        )
        if reply:
            return ChatResponse(reply=reply, provider="gemini")

        # Gemini failed → try OpenAI alone
        reply = await _openai(
            f"""Parlay context: {context}
User: {req.message}
Answer in 2-5 sentences.""",
            max_tokens=350,
        )
        return ChatResponse(reply=reply or _offline_message(), provider="openai")

    if settings.gemini_api_key:
        reply = await _gemini(
            f"""Parlay context: {context}
User: {req.message}
Answer in 2-5 sentences.""",
            max_tokens=350,
        )
        if reply:
            return ChatResponse(reply=reply, provider="gemini")

    if settings.openai_api_key:
        reply = await _openai(
            f"""Parlay context: {context}
User: {req.message}
Answer in 2-5 sentences.""",
            max_tokens=350,
        )
        return ChatResponse(reply=reply or _offline_message(), provider="openai")

    return ChatResponse(reply=_offline_message(), provider=None)
