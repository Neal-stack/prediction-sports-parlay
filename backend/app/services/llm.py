"""Low-level LLM callers shared by research + chat.

Gemini 2.0 Flash is the primary free engine (1,500 req/day). OpenAI is an
optional fallback only used when a key is present. Both return None on any
failure so callers can degrade gracefully.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

OPENAI_URL = "https://api.openai.com/v1/chat/completions"


def _gemini_url() -> str:
    return (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.gemini_model}:generateContent"
    )


async def gemini_text(prompt: str, *, max_tokens: int = 400, temperature: float = 0.4) -> Optional[str]:
    if not settings.gemini_api_key:
        return None
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                _gemini_url(), params={"key": settings.gemini_api_key}, json=payload
            )
            if resp.status_code != 200:
                logger.warning("Gemini returned %s", resp.status_code)
                return None
            candidates = resp.json().get("candidates") or []
            if not candidates:
                return None
            parts = candidates[0].get("content", {}).get("parts") or []
            return parts[0].get("text") if parts else None
    except Exception:
        logger.exception("Gemini request failed")
        return None


async def openai_text(prompt: str, *, max_tokens: int = 350, temperature: float = 0.4) -> Optional[str]:
    if not settings.openai_api_key:
        return None
    payload = {
        "model": settings.openai_model,
        "messages": [
            {"role": "system", "content": "You are a concise, honest sports betting analyst."},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
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
            choices = resp.json().get("choices") or []
            return choices[0].get("message", {}).get("content") if choices else None
    except Exception:
        logger.exception("OpenAI request failed")
        return None


async def llm_text(prompt: str, *, max_tokens: int = 400, temperature: float = 0.4) -> Optional[str]:
    """Try Gemini first (free), fall back to OpenAI if configured."""
    reply = await gemini_text(prompt, max_tokens=max_tokens, temperature=temperature)
    if reply:
        return reply
    return await openai_text(prompt, max_tokens=max_tokens, temperature=temperature)


def parse_json(text: Optional[str]) -> Optional[dict]:
    """Extract a JSON object from an LLM reply (handles ```json fences)."""
    if not text:
        return None
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1)
    else:
        brace = cleaned.find("{")
        last = cleaned.rfind("}")
        if brace != -1 and last != -1:
            cleaned = cleaned[brace : last + 1]
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return None
