# Phase 5 — Report Composition: Edge Cases

Failure modes and boundary conditions for report composition.

## Anchor Determinism

- **Product name with spaces or unicode** ("Groww Mutual Funds") → slugged consistently (`groww-mutual-funds`); anchor is ASCII-safe.
- **Anchor collision across products** sharing the same Doc → impossible if anchor includes product slug; verify the format does.
- **ISO week year boundary** (e.g. `2025-W53`) → anchor still valid: `pulse-groww-2025-W53`.
- **Anchor format change** between releases → would break Phase 6 idempotency for old weeks. Treat the anchor format as a stable contract; if it must change, version it and migrate or accept duplicate weeks.

## Content Edge Cases

- **Theme title contains markdown special chars** (`*`, `_`, `>`) → escaped or preserved as plain text in Doc blocks (Docs MCP doesn't interpret markdown).
- **Theme title contains the deep-link placeholder sentinel** → unlikely, but escape sentinels before placement; pick a sentinel that can't appear in titles.
- **Quote contains a literal blockquote-ending character** → not applicable for Docs (block boundaries are structural, not character-based).
- **Very long theme title** → truncate to a sensible length (e.g. 80 chars) for headings; full title can appear in summary.
- **Quote contains line breaks** → render as a single blockquote block with line breaks preserved (Docs supports paragraph breaks within blockquote-styled paragraphs).
- **Action idea contains an em-dash or curly quote** → preserved; downstream rendering is unicode-safe.

## Email Render

- **HTML email body too large** (themes with many quotes) → cap email at a configurable size (e.g. 50KB); the email is meant to be a teaser, not a duplicate. Truncate to top 3 theme titles.
- **Plain-text body and HTML body diverge** → bug; assert at end of phase that both reference the same theme titles in the same order.
- **Subject contains unicode** → ensure RFC 2047 encoding is applied at Phase 6, but Phase 5 keeps the raw unicode subject.
- **Deep-link placeholder accidentally double-replaced** → enforce "exactly once" in HTML and "exactly once" in text body; assert in tests.

## Source / Corpus Disclosure

- **Both sources present** → footer just lists counts.
- **One source missing** → footer notes "X unavailable this week"; do not silently underreport.
- **Phase 3 fallback to rating-bucketed theming** → footer carries an explicit caveat ("Low-volume week — themes derived by rating bucket, not clustering").
- **PII scrub dropped a notable fraction** (e.g. > 10%) → optional footer note for transparency.

## Doc Block-List Validity

- **Block-list must be valid as a single `batchUpdate`** — no orphan inline elements; no nested blockquotes the Docs API can't represent.
- **Mixed list types** (bullet vs ordered) — pick one (bullet) per section to keep `batchUpdate` predictable.
- **Empty bullet list** → don't emit the "Action ideas" subheading at all rather than emitting an empty list.

## Reproducibility

- **Locale-dependent date formatting** (e.g. comma in en-IN vs en-US) → use ISO-8601 inside heading text; format human dates only as a literal `Week of YYYY-MM-DD`.
- **Including `now()` anywhere in the rendered body** → forbidden; the body is a function of `(themes, run_plan, corpus_stats)`.

## Recovery

- **Render fails for one theme but not others** → drop that theme with an audit log entry; continue if any theme remains. If none remain, abort before Phase 6.
- **Render fails entirely** → run aborts with `failed_phase=5`; no Doc/email side effects (none have happened yet).
