"""Phase 7 data models."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from pulse.phase_2.core.types import CorpusStats


class AuditRecord(BaseModel):
    run_id: UUID
    product: str
    iso_week: str
    started_at: datetime
    ended_at: datetime | None = None
    status: Literal["ok", "partial", "failed", "skipped"]
    window_start: datetime | None = None
    window_end: datetime | None = None
    corpus_stats: CorpusStats | None = None
    cluster_count: int | None = None
    theme_count: int | None = None
    llm_model: str | None = None
    total_tokens: int | None = None
    total_cost_usd: float | None = None
    doc_id: str | None = None
    doc_section_anchor: str | None = None
    doc_revision_id: str | None = None
    gmail_message_id: str | None = None
    gmail_draft_id: str | None = None
    failed_phase: int | None = None
    error: str | None = None
    forced: bool = False
    dry_run: bool = False
