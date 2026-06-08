import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.models.schemas import HealthResponse
from app.routers import chat, games, parlay, status
from app.services.scheduler import start_scheduler, stop_scheduler

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(
    title="Prediction Parlay API",
    description="Odds pipeline + parlay generation for prediction markets",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(games.router)
app.include_router(parlay.router)
app.include_router(status.router)
app.include_router(chat.router)


@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(status="ok", timestamp=datetime.now(timezone.utc))
