"""Phase 6 — MCP Delivery.

Public API:
    deliver(plan, doc_report, email_report) -> DeliveryReceipt
"""
from pulse.phase_6.delivery.orchestrator import deliver
from pulse.phase_6.types import DeliveryReceipt

__all__ = ["deliver", "DeliveryReceipt"]
