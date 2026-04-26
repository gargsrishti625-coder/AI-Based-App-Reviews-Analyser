"""Phase 6 delivery orchestrator: 6a (Docs) → 6b (Gmail) → DeliveryReceipt."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

import structlog
from fastmcp import Client

from pulse.phase_0.core.exceptions import PhaseFailure
from pulse.phase_6.delivery.docs_adapter import (
    anchor_exists,
    deep_link,
    docs_batch_update,
    docs_get,
)
from pulse.phase_6.delivery.gmail_adapter import (
    idempotency_key,
    inject_deep_link,
    gmail_drafts_create,
    gmail_messages_list,
    gmail_messages_send,
)
from pulse.phase_6.types import DeliveryReceipt

if TYPE_CHECKING:
    from pulse.phase_0.core.types import RunPlan
    from pulse.phase_5.types import DocReport, EmailReport

log = structlog.get_logger()


async def deliver(
    plan: RunPlan,
    doc_report: DocReport,
    email_report: EmailReport,
) -> DeliveryReceipt:
    """Run sub-phase 6a (Docs append) then 6b (Gmail send/draft).

    6a failure raises PhaseFailure(6) immediately — no email is sent.
    6b failure returns a partial receipt (doc:ok, email:failed) so the
    audit layer can record a partial outcome and the operator can retry.
    """
    doc_id = plan.product.pulse_doc_id
    anchor = doc_report.anchor
    recipients = list(plan.product.email_recipients)

    # ── Sub-phase 6a — Docs ─────────────────────────────────────────────────
    doc_status: str
    doc_revision_id: str

    if plan.dry_run:
        doc_status = "dry_run"
        doc_revision_id = "dry_run_rev"
        link = deep_link(doc_id, anchor)
        log.info("phase_6a_dry_run", doc_id=doc_id, anchor=anchor)
    else:
        try:
            async with Client(str(plan.mcp_docs_url)) as docs_client:
                doc_info = await docs_get(docs_client, doc_id)

                if anchor_exists(doc_info, anchor):
                    doc_revision_id = doc_info["revision_id"]
                    doc_status = "skipped_existing_anchor"
                    log.info(
                        "phase_6a_skipped",
                        doc_id=doc_id,
                        anchor=anchor,
                        revision_id=doc_revision_id,
                    )
                else:
                    doc_revision_id = await docs_batch_update(
                        docs_client, doc_id, anchor, doc_report.blocks
                    )
                    # Verify the anchor landed
                    verify = await docs_get(docs_client, doc_id)
                    if not anchor_exists(verify, anchor):
                        raise PhaseFailure(6, "doc_append_unverified")
                    doc_status = "appended"
                    log.info(
                        "phase_6a_appended",
                        doc_id=doc_id,
                        anchor=anchor,
                        revision_id=doc_revision_id,
                    )

        except PhaseFailure:
            raise
        except Exception as exc:
            log.error("phase_6a_failed", error=str(exc))
            raise PhaseFailure(6, f"docs:{exc}") from exc

        link = deep_link(doc_id, anchor)

    # ── Sub-phase 6b — Gmail ─────────────────────────────────────────────────
    gmail_message_id: str | None = None
    gmail_draft_id: str | None = None
    email_status: str

    if plan.dry_run:
        email_status = "dry_run"
        log.info("phase_6b_dry_run")
    else:
        idem_key = idempotency_key(plan.product.slug, plan.iso_week, doc_revision_id)
        headers = {
            "X-Pulse-Idempotency-Key": idem_key,
            "X-Pulse-Run-ID": str(plan.run_id),
        }

        html_body = inject_deep_link(email_report.html_body, link)
        text_body = inject_deep_link(email_report.text_body, link)

        try:
            async with Client(str(plan.mcp_gmail_url)) as gmail_client:
                # Idempotency pre-check
                prior = await gmail_messages_list(
                    gmail_client, f"X-Pulse-Idempotency-Key:{idem_key}", limit=1
                )
                if prior:
                    gmail_message_id = prior[0]["id"]
                    email_status = "skipped_already_sent"
                    log.info(
                        "phase_6b_skipped_already_sent",
                        prior_message_id=gmail_message_id,
                    )
                elif plan.draft_only:
                    gmail_draft_id = await gmail_drafts_create(
                        gmail_client,
                        to=recipients,
                        subject=email_report.subject,
                        html_body=html_body,
                        text_body=text_body,
                        headers=headers,
                    )
                    email_status = "drafted"
                else:
                    gmail_message_id = await gmail_messages_send(
                        gmail_client,
                        to=recipients,
                        subject=email_report.subject,
                        html_body=html_body,
                        text_body=text_body,
                        headers=headers,
                    )
                    email_status = "sent"

        except Exception as exc:
            log.error("phase_6b_failed", error=str(exc))
            email_status = "failed"
            # Don't raise — return partial receipt so audit can record doc:ok email:failed

    log.info(
        "phase_6_done",
        doc_status=doc_status,
        email_status=email_status,
        doc_revision_id=doc_revision_id,
    )

    return DeliveryReceipt(
        doc_id=doc_id,
        doc_section_anchor=anchor,
        doc_revision_id=doc_revision_id,
        gmail_message_id=gmail_message_id,
        gmail_draft_id=gmail_draft_id,
        sent_at=datetime.now(timezone.utc),
        dry_run=plan.dry_run,
        doc_status=doc_status,
        email_status=email_status,
    )
