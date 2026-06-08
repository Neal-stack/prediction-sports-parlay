from fastapi import APIRouter

from app.models.schemas import ChatRequest, ChatResponse
from app.services.ai_assistant import chat_about_parlay

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
async def chat(body: ChatRequest):
    return await chat_about_parlay(body, body.parlay)
