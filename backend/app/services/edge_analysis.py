from __future__ import annotations

from app.models.schemas import EdgeAnalysisRequest, EdgeAnalysisResponse, PickLeg


def analyze_user_edges(req: EdgeAnalysisRequest) -> EdgeAnalysisResponse:
    overrides = {e.leg_index: e.user_probability for e in req.user_edges}
    legs: list[PickLeg] = []
    win_prob = 1.0
    edges: list[float] = []

    for i, leg in enumerate(req.parlay.legs):
        user_p = overrides.get(i, leg.win_probability)
        edge = round(user_p - leg.implied_prob, 4)
        edges.append(edge)
        win_prob *= user_p
        legs.append(
            leg.model_copy(
                update={
                    "user_probability": round(user_p, 4),
                    "edge_vs_implied": edge,
                }
            )
        )

    avg_edge = sum(edges) / len(edges) if edges else 0.0
    user_win = round(win_prob, 4)
    model_win = req.parlay.estimated_win_prob

    if user_win > model_win + 0.02:
        tone = "Your probabilities are more optimistic than our model."
    elif user_win < model_win - 0.02:
        tone = "Your probabilities are more conservative than our model."
    else:
        tone = "Your probabilities align closely with our model."

    summary = (
        f"{tone} User-estimated parlay win rate: {user_win:.1%} "
        f"(model: {model_win:.1%}). Average edge vs implied: {avg_edge:+.1%} per leg."
    )

    return EdgeAnalysisResponse(
        legs=legs,
        user_estimated_win_prob=user_win,
        model_estimated_win_prob=model_win,
        avg_edge_vs_implied=round(avg_edge, 4),
        summary=summary,
    )
