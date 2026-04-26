# Phase 5 — Report Composition: Evaluations

Evaluation criteria, test cases, and acceptance gates for rendering the validated themes into `DocReport` and `EmailReport`.

## Quality Criteria

- `DocReport` is a **structured block list**, not raw HTML — directly consumable by Docs MCP `batchUpdate`.
- The section anchor for the week is **deterministic** from `(product, iso_week)` — Phase 6 relies on this for idempotency.
- The Doc is the system of record; `EmailReport` is a teaser + deep link, never a duplicate body.
- Both HTML and plain-text alternatives are rendered for the email.

## Functional Tests

| ID | Scenario | Expected |
|---|---|---|
| P5-E1 | 3 surviving themes from Phase 4 | `DocReport` has H2 (week), 3× H3 (theme) + summary + bullets + blockquoted quotes + action ideas |
| P5-E2 | Anchor is `pulse-{product}-{iso_week}` (e.g. `pulse-groww-2026-W17`) | Anchor matches a documented format and is reproducible |
| P5-E3 | Re-render with the same inputs | Byte-identical `DocReport` block list and `EmailReport` text |
| P5-E4 | One source missing (Phase 1 soft failure) | Footer notes "Play Store unavailable this week (N from App Store only)" |
| P5-E5 | Email HTML and text bodies semantically equivalent | Both list top theme titles and reference the same deep-link placeholder |
| P5-E6 | Doc heading text | `Week of YYYY-MM-DD (ISO YYYY-Www) — N reviews`, where N is `corpus_stats.total_out` |
| P5-E7 | Theme has 0 action ideas (rare) | Theme rendered without "Action ideas" subheading; no empty bullet list |
| P5-E8 | Deep-link placeholder present | `EmailReport` contains a unique sentinel like `{{PULSE_DEEP_LINK}}` for Phase 6 to substitute |

## Block-List Schema (Docs MCP)

- Each block declares its type (`heading_2`, `heading_3`, `paragraph`, `bullet`, `blockquote`) and content.
- The block list is composable into a single `batchUpdate` request — Phase 6 must not need to re-split it.
- Quotes are `blockquote` blocks with the originating `review_id` as a small attribution suffix.

## Email Render

- `EmailReport.subject` is `Weekly Pulse — {Product} — Week of {YYYY-MM-DD}`.
- HTML body uses inline styles (no external CSS); plain-text alternative is a simple bullet list.
- Deep-link placeholder is present in **both** HTML and plain-text versions.

## Determinism

- Anchor format documented and tested: `pulse-{slug(product)}-{iso_week}` (lowercased, slug-safe).
- Same inputs → same outputs across runs (no current-time strings inside the body).

## Metrics to Log

- `doc_block_count`, `doc_render_duration_ms`
- `email_html_bytes`, `email_text_bytes`, `email_render_duration_ms`
- `themes_rendered`, `total_quotes_rendered`, `total_actions_rendered`
- `anchor_value` (recorded for traceability)

## Acceptance Gate

The phase passes when:
1. Non-empty `DocReport` block list exists.
2. Both HTML and plain-text email bodies exist with the deep-link placeholder.
3. Anchor matches the deterministic format.
