"""Phase 2 orchestrator: convert RawReview[] → CleanReview[] + CorpusStats."""
from __future__ import annotations

import structlog

from pulse.phase_1.ingestion.base import RawReview
from pulse.phase_2.core.types import CleanReview, CorpusStats
from pulse.util.text import text_hash as _text_hash

from .filter import is_target_language, is_too_short
from .normalize import merge_title_body, normalize_text
from .pii import scrub_pii

log = structlog.get_logger()


def clean(reviews: list[RawReview]) -> tuple[list[CleanReview], CorpusStats]:
    """Normalize, PII-scrub, filter, and dedup *reviews*.

    Returns ``(clean_reviews, stats)`` where ``stats.assert_reconciles()`` is
    guaranteed to pass — any counting bug raises ``AssertionError`` immediately.
    """
    total_in = len(reviews)
    dropped_short = 0
    dropped_lang = 0
    dedup_count = 0

    pre_dedup: list[CleanReview] = []

    for review in reviews:
        # 1. Merge title + body
        raw_text = merge_title_body(review.title, review.body)

        # 2. Unicode / whitespace normalization
        norm_text = normalize_text(raw_text)

        # 3. PII scrub
        scrubbed_text, pii_counts = scrub_pii(norm_text)

        if sum(pii_counts.values()):
            log.debug(
                "pii_scrubbed",
                review_id=review.review_id,
                counts=pii_counts,
            )

        # 4. Length filter (post-scrub — reviews that scrubbing made too short
        #    are counted as dropped_short, not a separate dropped_pii counter)
        if is_too_short(scrubbed_text):
            dropped_short += 1
            continue

        # 5. Language filter
        if not is_target_language(scrubbed_text):
            dropped_lang += 1
            continue

        # 6. Build CleanReview with source-prefixed review_id
        pre_dedup.append(
            CleanReview(
                review_id=f"{review.source}:{review.review_id}",
                source=review.source,
                product=review.product,
                rating=review.rating,
                locale=review.locale,
                posted_at=review.posted_at,
                app_version=review.app_version,
                text=scrubbed_text,
                text_hash=_text_hash(scrubbed_text),
            )
        )

    # 7. Exact dedup by text_hash (cross-source: if same text appears on both
    #    stores, only the first occurrence proceeds)
    seen_hashes: set[str] = set()
    final: list[CleanReview] = []
    for cr in pre_dedup:
        if cr.text_hash in seen_hashes:
            dedup_count += 1
        else:
            seen_hashes.add(cr.text_hash)
            final.append(cr)

    stats = CorpusStats(
        total_in=total_in,
        total_out=len(final),
        dropped_pii=0,
        dropped_short=dropped_short,
        dropped_lang=dropped_lang,
        dedup_count=dedup_count,
    )
    stats.assert_reconciles()  # fail fast on any counting bug

    log.info(
        "phase_2_clean_done",
        total_in=total_in,
        total_out=len(final),
        dropped_short=dropped_short,
        dropped_lang=dropped_lang,
        dedup_count=dedup_count,
    )

    return final, stats
