"""Phase 6 data models."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class DeliveryReceipt(BaseModel):
    doc_id: str
    doc_section_anchor: str
    doc_revision_id: str
    gmail_message_id: str | None = None
    gmail_draft_id: str | None = None
    sent_at: datetime
    dry_run: bool = False
    doc_status: Literal["appended", "skipped_existing_anchor", "dry_run"] = "appended"
    email_status: Literal[
        "sent", "drafted", "skipped_already_sent", "failed", "dry_run"
    ] = "sent"
