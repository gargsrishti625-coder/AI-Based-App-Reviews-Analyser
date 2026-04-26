from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AnyUrl, BaseModel, ConfigDict, EmailStr, Field


class McpEndpoints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    docs_url: AnyUrl
    gmail_url: AnyUrl
    probe_timeout_seconds: float = Field(default=5.0, gt=0)
    required_docs_tools: list[str] = Field(
        default=["docs.get", "docs.batchUpdate"]
    )
    required_gmail_tools: list[str] = Field(
        default=["gmail.messages.send", "gmail.drafts.create"]
    )


class ProductRegistryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    display_name: str
    app_store_id: str | None = None
    play_store_id: str | None = None
    pulse_doc_id: str
    email_recipients: list[EmailStr]


class PulseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    products: dict[str, ProductRegistryEntry]
    window_weeks: Annotated[int, Field(ge=1, le=52)] = 8
    n_min_reviews: Annotated[int, Field(ge=1)] = 20
    llm_model: str = "llama-3.3-70b-versatile"
    embedding_model: str = "text-embedding-3-small"
    mcp: McpEndpoints
    total_token_cap: Annotated[int, Field(ge=1000)] = 200_000
    max_reviews_per_source: Annotated[int, Field(ge=1)] = 500
    pulse_env: Literal["dev", "staging", "prod"] = "dev"


class RunPlan(BaseModel):
    """Frozen, validated plan for a single pipeline run.

    Constructed once in Phase 0 and passed read-only to every subsequent phase.
    The frozen=True config means any mutation attempt raises a ValidationError.
    """

    model_config = ConfigDict(frozen=True)

    run_id: UUID
    product: ProductRegistryEntry
    iso_week: str
    window_start: datetime   # UTC, inclusive
    window_end: datetime     # UTC, inclusive
    sources: list[Literal["app_store", "play_store"]]
    llm_model: str
    embedding_model: str
    mcp_docs_url: AnyUrl
    mcp_gmail_url: AnyUrl
    dry_run: bool
    draft_only: bool
    force_resend: bool = False
