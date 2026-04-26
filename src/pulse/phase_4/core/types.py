"""Phase 4 data models: Quote and Theme."""
from __future__ import annotations

from pydantic import BaseModel


class Quote(BaseModel):
    text: str       # verbatim substring of CleanReview.text, validated
    review_id: str  # source-prefixed id of the review this came from


class Theme(BaseModel):
    title: str                      # ≤ 60 chars
    summary: str                    # 1–2 sentences
    quotes: list[Quote]             # all validated; never empty
    action_ideas: list[str]         # each ≤ 12 words
    supporting_review_ids: list[str]
    cluster_id: int
