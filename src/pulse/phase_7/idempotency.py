"""Pre-run idempotency check for Phase 7.

Decision flow:
  1. force_resend → FORCE_RESEND (always proceed, bypass email dedup)
  2. prior successful send exists → SKIP_ALREADY_SENT
  3. prior partial run exists (doc ok, email failed) → RETRY_EMAIL_ONLY
  4. else → PROCEED
"""
from __future__ import annotations

from enum import Enum, auto
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from pulse.phase_0.core.types import RunPlan
    from pulse.phase_7.store import AuditStore

log = structlog.get_logger()


class Decision(Enum):
    PROCEED = auto()
    SKIP_ALREADY_SENT = auto()
    RETRY_EMAIL_ONLY = auto()
    FORCE_RESEND = auto()


def check_before_run(store: AuditStore, plan: RunPlan) -> Decision:
    """Determine what the run should do based on prior audit history."""
    if plan.force_resend:
        log.info("idempotency_force_resend", product=plan.product.slug, iso_week=plan.iso_week)
        return Decision.FORCE_RESEND

    prior_send = store.find_prior_send(plan.product.slug, plan.iso_week)
    if prior_send is not None:
        log.info(
            "idempotency_already_sent",
            product=plan.product.slug,
            iso_week=plan.iso_week,
            prior_run_id=str(prior_send.run_id),
        )
        return Decision.SKIP_ALREADY_SENT

    partial = store.find_partial(plan.product.slug, plan.iso_week)
    if partial is not None:
        log.info(
            "idempotency_retry_email_only",
            product=plan.product.slug,
            iso_week=plan.iso_week,
            prior_run_id=str(partial.run_id),
        )
        return Decision.RETRY_EMAIL_ONLY

    return Decision.PROCEED
