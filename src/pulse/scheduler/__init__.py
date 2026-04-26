"""Phase 8 — Scheduling & CLI orchestration.

Public surface:
    execute_pipeline — run phases 1–7 for one (product, iso_week)
    PipelineOutcome  — structured result (status, exit_code, run_id)
    weekly.main      — entrypoint for the scheduled GitHub Action
"""
from pulse.scheduler.pipeline import PipelineOutcome, execute_pipeline

__all__ = ["PipelineOutcome", "execute_pipeline"]
