from fastapi import APIRouter, Request

from app.config import settings
from app.db.supabase import get_supabase
from app.rate_limit import limiter
from app.models.schemas import ChatRequest, ChatResponse
from app.services.ai_assistant import chat_about_parlay

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
@limiter.limit("15/minute")
async def chat(request: Request, body: ChatRequest):
    if not body.parlay and not settings.gemini_api_key:
        return ChatResponse(
            reply="Generate a parlay first, then ask questions about it.",
            provider=None,
        )
    return await chat_about_parlay(body, body.parlay)
