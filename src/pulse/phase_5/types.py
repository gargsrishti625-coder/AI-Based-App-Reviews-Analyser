"""Phase 5 data models: DocBlock, DocReport, EmailReport."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class DocBlock(BaseModel):
    type: Literal["heading_2", "heading_3", "paragraph", "bullet", "blockquote"]
    text: str
    anchor: str | None = None        # set on the H2 only
    attribution: str | None = None   # for blockquote: the source review_id


class DocReport(BaseModel):
    anchor: str               # e.g. "pulse-groww-2026-W17"
    blocks: list[DocBlock]
    metadata: dict            # run metadata for audit traceability


class EmailReport(BaseModel):
    subject: str
    html_body: str   # contains exactly one {{PULSE_DEEP_LINK}}
    text_body: str   # contains exactly one {{PULSE_DEEP_LINK}}
