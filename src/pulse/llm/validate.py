"""Quote validator — the hard gate that prevents hallucinated quotes from shipping.

Every quote in a Theme must be a verbatim substring (after normalization) of
the CleanReview.text it claims to come from.  Uses normalize_for_match from
util/text.py — the same function Phase 2 used when building the corpus — so
whitespace, invisible characters, and Unicode forms are handled identically.
"""
from __future__ import annotations

import structlog

from pulse.phase_2.core.types import CleanReview
from pulse.phase_3.core.types import Cluster
from pulse.phase_4.core.types import Quote
from pulse.util.text import normalize_for_match

log = structlog.get_logger()


def _unescape_html(text: str) -> str:
    """Reverse the HTML entity escaping applied in build_user_prompt."""
    return text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")


def validate_quote(
    quote: Quote,
    cluster: Cluster,
    reviews_by_id: dict[str, CleanReview],
) -> bool:
    """Return True iff quote.text is a verbatim substring of its source review.

    Validation steps:
    1. review_id must be a member of the cluster (not from another cluster).
    2. The review must exist in reviews_by_id.
    3. After HTML-unescape and normalize_for_match, quote.text must be a
       substring of review.text.
    """
    if quote.review_id not in cluster.member_review_ids:
        log.debug(
            "quote_rejected",
            reason="not_in_cluster",
            review_id=quote.review_id,
            cluster_id=cluster.cluster_id,
        )
        return False

    review = reviews_by_id.get(quote.review_id)
    if review is None:
        log.debug(
            "quote_rejected",
            reason="review_not_found",
            review_id=quote.review_id,
        )
        return False

    needle = normalize_for_match(_unescape_html(quote.text))
    haystack = normalize_for_match(review.text)

    if not needle:
        log.debug("quote_rejected", reason="empty_after_normalize", review_id=quote.review_id)
        return False

    if needle not in haystack:
        log.debug(
            "quote_rejected",
            reason="not_substring",
            review_id=quote.review_id,
            needle_preview=needle[:80],
        )
        return False

    return True
