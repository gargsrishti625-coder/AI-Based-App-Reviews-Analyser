# Phase 2 — Normalize, PII Scrub, Filter: Evaluations

Evaluation criteria, test cases, and acceptance gates for normalization, PII scrubbing, and filtering.

## Quality Criteria

- **PII never leaks downstream.** No raw email, phone number, or account number appears in any output that touches the LLM, the Doc, or the email.
- The scrubbed text is still semantically useful — `[email]`/`[phone]` placeholders preserve grammatical structure.
- Dedup operates on `text_hash` (post-normalization) so trivial whitespace/case differences collapse.
- Quote validation in Phase 4 uses the **scrubbed** `CleanReview.text` — Phase 2 must produce text that quotes will be validated against.

## Functional Tests

| ID | Scenario | Expected |
|---|---|---|
| P2-E1 | Review contains `Email me at user@example.com` | Becomes `Email me at [email]` |
| P2-E2 | Review contains `Call +91 98765 43210 anytime` | Becomes `Call [phone] anytime` |
| P2-E3 | Review contains 12-digit account number `123456789012` | Replaced with `[account]` |
| P2-E4 | Two reviews differing only in whitespace/case | One survives; `dedup_count` increments |
| P2-E5 | Review with 4 tokens | Dropped, `dropped_short` increments |
| P2-E6 | Non-English review (e.g. Hindi) with `language_filter=en` | Dropped, `dropped_lang` increments |
| P2-E7 | Title + body merged into single `text` | `text == title.strip() + "\n" + body.strip()` (or configured separator) |
| P2-E8 | Emoji-only review | Dropped (`< min_tokens` after normalization) |
| P2-E9 | `corpus_stats` totals reconcile | `total_in == total_out + dropped_pii + dropped_short + dropped_lang + dedup_count` |

## PII Scrub Recall / Precision

- **Recall target ≥ 95%** for: emails (RFC 5322 common subset), Indian + international phone formats, 10–16 digit numeric strings preceded/followed by financial keywords (e.g. "account", "card").
- **Precision target ≥ 90%** — names and product mentions must not be over-scrubbed (false positives degrade theming quality).
- Optional NER pass for names is gated behind a confidence threshold (e.g. ≥ 0.85) to avoid mangling product names.

## Determinism

- Re-running Phase 2 on the same `RawReview[]` produces identical `CleanReview[]` and `corpus_stats`.
- `text_hash` is stable across runs (use a fixed hash family, e.g. `sha256` on UTF-8 NFC-normalized text).

## Metrics to Log

- `total_in`, `total_out`, `dropped_pii`, `dropped_short`, `dropped_lang`, `dedup_count`
- `pii_scrub_duration_ms`
- `pii_match_counts` per category (email, phone, account)

## Acceptance Gate

The phase passes when:
1. `CleanReview[]` is non-empty.
2. `corpus_stats` is recorded in the audit log.
3. A spot-check assertion confirms no pattern from the PII regex set survives in any `CleanReview.text`.
