"""Phase 8 — PipelineOutcome and execute_pipeline contract."""
from __future__ import annotations

from uuid import uuid4

from pulse.scheduler.pipeline import PipelineOutcome


class TestPipelineOutcome:
    def test_ok_constructs_with_zero_exit(self) -> None:
        rid = uuid4()
        out = PipelineOutcome.ok(rid)
        assert out.status == "ok"
        assert out.exit_code == 0
        assert out.run_id == rid
        assert out.failed_phase is None
        assert out.error is None

    def test_skipped_zero_exit_with_reason(self) -> None:
        out = PipelineOutcome.skipped(uuid4(), "already_sent")
        assert out.status == "skipped"
        assert out.exit_code == 0
        assert out.error == "already_sent"

    def test_partial_exit_two(self) -> None:
        out = PipelineOutcome.partial(uuid4(), "email_status=failed")
        assert out.status == "partial"
        assert out.exit_code == 2
        assert out.error == "email_status=failed"

    def test_failed_carries_phase_and_reason(self) -> None:
        out = PipelineOutcome.failed(uuid4(), 4, "budget_exhausted")
        assert out.status == "failed"
        assert out.exit_code == 1
        assert out.failed_phase == 4
        assert out.error == "budget_exhausted"

    def test_outcome_is_frozen(self) -> None:
        out = PipelineOutcome.ok(uuid4())
        try:
            out.status = "failed"  # type: ignore[misc]
        except Exception:
            return
        raise AssertionError("PipelineOutcome should be frozen")
