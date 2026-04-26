# Phase 2 — Normalize, PII Scrub, Filter: Edge Cases

Failure modes and boundary conditions for normalization, PII scrubbing, and filtering.

## PII Scrub — Recall Risks (PII that may slip through)

- **Obfuscated emails**: `user [at] example [dot] com`, `user(at)example(dot)com` — augment regex with `(at|@)` and `(dot|\.)` variants.
- **Spaced phone numbers**: `9 8 7 6 5 4 3 2 1 0` — collapse whitespace within digit runs before matching, OR add a "spaced digits" rule.
- **International formats** with country codes, parentheses, dashes: `+1 (415) 555-0100`.
- **Numbers split across lines** (e.g. account number wrapping in a review).
- **Unicode digit lookalikes** (Arabic-Indic digits `٠١٢…`) — normalize to ASCII digits before matching.
- **PII inside URLs**: `https://example.com/profile/user@example.com` — scrub the embedded email but consider whether to also scrub URLs entirely.

## PII Scrub — Precision Risks (false positives)

- **Product names** containing digits (e.g. "Groww 4.5") — must not be tagged as account numbers.
- **App version strings** (`v1.2.3`) — exclude from numeric-PII rules.
- **Common short numerics** like ratings or prices ("5 stars", "₹500") — exclude.
- **Names that overlap with product/brand** ("Groww") — never scrubbed even if NER fires.
- **Years** (`2024`) — never treated as PII.

## Quote Validation Coupling

- The scrubbed text is the substrate Phase 4's validator searches. **Any normalization Phase 2 does must be reversible at validation time** — i.e., the validator must apply the same normalization to incoming quotes before substring matching.
- If Phase 2 lowercases or strips punctuation, the validator must too (or Phase 2 should not do it).
- Recommended: keep punctuation and case in `text`; only normalize whitespace and unicode form.

## Normalization

- **Mixed unicode normalization forms** (NFC vs NFD) — pick one (NFC) and stick with it; `text_hash` depends on this.
- **Smart quotes vs straight quotes** — normalize or not, but consistently. The validator must agree.
- **Zero-width chars / RTL marks** — strip; otherwise `text_hash` differs from visually-identical reviews and dedup misses.
- **Emoji handling**: keep emoji in text (they're often sentiment-bearing) but don't count them toward `min_tokens`.

## Filter Boundaries

- **Token count == min_tokens** (boundary) — keep (`>=` not `>`).
- **Language detector confidence low** (e.g. mixed-language review) → keep by default; only drop on high-confidence non-target language.
- **Reviews containing only a URL** → drop as low-signal.
- **All-caps reviews** → keep; uppercase ≠ low quality (often sentiment-bearing).
- **Reviews that become empty after PII scrub** (e.g. "[email]") → drop, increment `dropped_short`, not `dropped_pii`.

## Dedup Anomalies

- **Identical text across `app_store` and `play_store`** → dedup keeps **one**; record source provenance for both in audit but a single `CleanReview` proceeds.
- **Near-duplicates** (same review with one trailing space) → collapsed by NFC + whitespace normalization before hashing.
- **Bot-style copy-paste reviews** (many identical) → all collapse to one; `dedup_count` reflects this. Theming will under-weight bot brigades, which is intended.

## Operational Failures

- **NER model fails to load** → fall back to regex-only PII scrub with a logged warning; do not abort.
- **NER inference timeout** on a long review → skip NER for that review; regex still applied.
- **`corpus_stats` arithmetic mismatch** (totals don't reconcile) → fail the phase; this signals a counting bug.
