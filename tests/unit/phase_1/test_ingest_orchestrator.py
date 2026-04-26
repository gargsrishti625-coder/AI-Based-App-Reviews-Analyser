"""Unit tests for the Phase 1 ingest() orchestrator — evaluations P1-E2, E3."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from pulse.phase_0.core.exceptions import PhaseFailure
from pulse.phase_0.core.types import McpEndpoints, ProductRegistryEntry, PulseConfig
from pulse.phase_1.ingestion import ingest
from pulse.phase_1.ingestion.base import IngestResult, RawReview

_UTC = timezone.utc


def _make_config(n_min: int = 5) -> PulseConfig:
    return PulseConfig(
        pulse_env="dev",
        window_weeks=8,
        n_min_reviews=n_min,
        llm_model="claude-sonnet-4-6",
        embedding_model="text-embedding-3-small",
        total_token_cap=200_000,
        max_reviews_per_source=500,
        mcp=McpEndpoints(
            docs_url="http://localhost:8080/sse",  # type: ignore[arg-type]
            gmail_url="http://localhost:8081/sse",  # type: ignore[arg-type]
        ),
        products={
            "groww": ProductRegistryEntry(
                slug="groww",
                display_name="Groww",
                app_store_id="1404379703",
                play_store_id="com.nextbillion.groww",
                pulse_doc_id="DOC1",
                email_recipients=["team@example.com"],
            )
        },
    )


def _make_review(review_id: str, source: str = "app_store") -> RawReview:
    return RawReview(
        source=source,  # type: ignore[arg-type]
        review_id=review_id,
        product="groww",
        rating=4,
        body="This is a solid investment application.",
        posted_at=datetime(2026, 4, 15, tzinfo=_UTC),
        fetched_at=datetime(2026, 4, 26, tzinfo=_UTC),
        raw={},
    )


def _ok_result(source: str, count: int = 10) -> IngestResult:
    return IngestResult(
        source=source,
        reviews=[_make_review(f"{source}_{i}", source) for i in range(count)],
        pages_fetched=1,
        status="ok",
    )


def _empty_result(source: str) -> IngestResult:
    return IngestResult(source=source, status="empty")


def _failed_result(source: str) -> IngestResult:
    return IngestResult(source=source, status="failed", error="connection refused")


def _make_plan(config: PulseConfig, sources: list[str] | None = None) -> object:
    from pulse.phase_0.core.runplan import build_runplan

    return build_runplan(
        config=config,
        product_slug="groww",
        iso_week="2026-W01",
    )


class TestIngestOrchestrator:
    # P1-E2: Play Store empty, App Store has reviews → run continues
    async def test_one_source_empty_other_ok(self) -> None:
        config = _make_config(n_min=5)
        plan = _make_plan(config)

        with (
            patch("pulse.phase_1.ingestion._INGESTERS", {
                "app_store": AsyncMock(fetch=AsyncMock(return_value=_ok_result("app_store", 10))),
                "play_store": AsyncMock(fetch=AsyncMock(return_value=_empty_result("play_store"))),
            }),
        ):
            results = await ingest(plan, config)  # type: ignore[arg-type]

        assert results["app_store"].status == "ok"
        assert results["play_store"].status == "empty"

    # P1-E3: Both sources empty → PhaseFailure(1, ...)
    async def test_both_sources_empty_raises_phase_failure(self) -> None:
        config = _make_config()
        plan = _make_plan(config)

        with (
            patch("pulse.phase_1.ingestion._INGESTERS", {
                "app_store": AsyncMock(fetch=AsyncMock(return_value=_empty_result("app_store"))),
                "play_store": AsyncMock(fetch=AsyncMock(return_value=_empty_result("play_store"))),
            }),
        ):
            with pytest.raises(PhaseFailure) as exc_info:
                await ingest(plan, config)  # type: ignore[arg-type]

        assert exc_info.value.phase == 1
        assert "empty_or_failed" in exc_info.value.reason

    async def test_both_sources_failed_raises_phase_failure(self) -> None:
        config = _make_config()
        plan = _make_plan(config)

        with (
            patch("pulse.phase_1.ingestion._INGESTERS", {
                "app_store": AsyncMock(fetch=AsyncMock(return_value=_failed_result("app_store"))),
                "play_store": AsyncMock(fetch=AsyncMock(return_value=_failed_result("play_store"))),
            }),
        ):
            with pytest.raises(PhaseFailure) as exc_info:
                await ingest(plan, config)  # type: ignore[arg-type]

        assert exc_info.value.phase == 1

    async def test_source_exception_caught_as_soft_failure(self) -> None:
        config = _make_config(n_min=5)
        plan = _make_plan(config)

        async def _raise(*_a: object, **_kw: object) -> None:
            raise RuntimeError("unexpected network failure")

        with (
            patch("pulse.phase_1.ingestion._INGESTERS", {
                "app_store": AsyncMock(fetch=_raise),
                "play_store": AsyncMock(fetch=AsyncMock(return_value=_ok_result("play_store", 10))),
            }),
        ):
            results = await ingest(plan, config)  # type: ignore[arg-type]

        assert results["app_store"].status == "failed"
        assert results["play_store"].status == "ok"

    async def test_returns_dict_keyed_by_source(self) -> None:
        config = _make_config(n_min=5)
        plan = _make_plan(config)

        with (
            patch("pulse.phase_1.ingestion._INGESTERS", {
                "app_store": AsyncMock(fetch=AsyncMock(return_value=_ok_result("app_store"))),
                "play_store": AsyncMock(fetch=AsyncMock(return_value=_ok_result("play_store"))),
            }),
        ):
            results = await ingest(plan, config)  # type: ignore[arg-type]

        assert set(results.keys()) == {"app_store", "play_store"}
