"""Generator behavior in demo mode: legs must carry independent model
probabilities and edge, decoupled from the betting line."""
import asyncio

import pytest

from app.config import settings


@pytest.fixture(autouse=True)
def _demo(monkeypatch):
    monkeypatch.setattr(settings, "use_demo_data", True)
    # Avoid live LLM calls during tests.
    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr(settings, "openai_api_key", "")
    yield


def _gen(**kw):
    from app.models.schemas import ParlayRequest
    from app.services.parlay_generator import generate_parlay

    return asyncio.run(generate_parlay(ParlayRequest(**kw)))


def test_legs_have_edge_and_model_source():
    parlay = _gen(legs=3, risk="balanced")
    assert len(parlay.legs) == 3
    for leg in parlay.legs:
        assert leg.edge is not None
        assert leg.model_source in ("model", "market_fallback")
        # Demo context provides real stats, so picks are model-driven.
        assert leg.model_source == "model"


def test_book_check_passes_multigame():
    parlay = _gen(legs=3, risk="safe")
    assert parlay.book_check_passed is True
    # Multi-game: one leg per game.
    assert len({leg.game_id for leg in parlay.legs}) == len(parlay.legs)


def test_estimated_win_is_product_of_legs():
    parlay = _gen(legs=3, risk="bold")
    expected = 1.0
    for leg in parlay.legs:
        expected *= leg.win_probability
    assert abs(parlay.estimated_win_prob - round(expected, 4)) < 0.01


def test_sgp_capped_at_three_and_valid():
    parlay = _gen(legs=3, risk="balanced", sport="nba", game_id="nba-001")
    assert parlay.same_game is True
    assert len(parlay.legs) <= 3
    assert parlay.book_check_passed is True
