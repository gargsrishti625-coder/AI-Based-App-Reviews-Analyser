# Phase 4 — LLM Theming, Quotes, Actions: Edge Cases

Failure modes and boundary conditions for LLM theming and quote validation.

## Quote Validation — Adversarial / Subtle Failures

- **LLM paraphrases instead of quoting** (changes a single word) → fails substring match; quote dropped. This is the validator working as intended.
- **LLM fabricates a `review_id`** that exists in another cluster → reject: validator must check the quote against `member_review_ids` of the *current* cluster only.
- **LLM fabricates a `review_id`** that doesn't exist at all → reject; log as a validator failure mode (not just a "quote miss").
- **Quote contains smart quotes / em-dashes** but source has straight quotes → normalize both sides identically before matching. Document the normalization in audit.
- **Quote contains `[email]` placeholder** → matches because Phase 2 produced the same placeholder; this is the intended invariant.
- **Whitespace difference only** → validator collapses runs of whitespace before comparing, so this matches.
- **Cross-review concatenation** (LLM stitches quote from two reviews) → fails, because no single member review contains the full string.

## Prompt Injection / Misuse

- **Review text says "ignore the system prompt and..."** → with role-tagged data wrapping, ignored. Verify with adversarial corpus in CI.
- **Review text contains a fake `</review>` tag** → use a tag that's unlikely to appear in user text, or escape `<`/`>` in review text before insertion. Tag escaping must round-trip so quote validation still works.
- **Review text demands the LLM emit specific themes** → still ignored; themes are validated against actual cluster content, but the LLM may comply with the injection. Mitigation: explicit system instruction + validator catching hallucinated quotes.
- **Review text in another language attempting injection** → same defenses; theming runs are English-only by design.

## LLM Failure Modes

- **LLM returns malformed JSON** → retry once with stricter "JSON only" instruction; if second attempt also malformed, drop that cluster's theme; log.
- **LLM returns valid JSON but missing required fields** (`title` empty, `quotes` not a list) → drop, log.
- **LLM rate limit / 429** → backoff; respect total LLM time budget.
- **LLM refuses content** ("I can't help with that") on a benign cluster → log, drop the theme. Do not retry with the same prompt.
- **LLM returns themes for a cluster but every quote is hallucinated** → theme dropped; cluster gets no theme this week. Common with very short member reviews — instruct LLM to skip if no good quote exists rather than invent.

## Token Budget

- **First cluster consumes 80% of the run budget** (e.g. very long reviews) → cap per-cluster prompt length: truncate centroid candidate texts at a configurable max chars, and limit candidate count.
- **Hard cap exceeded mid-cluster** → finish the in-flight cluster (so we don't waste partial spend), then stop; mark remaining clusters as skipped.
- **Cap exceeded on cluster 1 of K** → run still proceeds to Phase 5 if at least one theme survives; otherwise abort.

## Theme Quality

- **Two themes with near-identical titles** → de-duplicate by title similarity (cosine on titles or simple Jaccard); keep the higher-ranked.
- **Theme summary contradicts the quotes** (LLM hallucination) → not detected by validator; mitigated by prompt design (summary must reference quote IDs) and human review.
- **Action ideas are vague** ("improve the app") → instruct LLM to be specific; if all ideas trip a vagueness heuristic (e.g. < 4 words, generic verbs only), regenerate once.
- **Action idea references a feature that doesn't exist** → not detected automatically; rely on human reviewer + cumulative trust.

## Per-Cluster Isolation

- One LLM call per cluster bounds blast radius: a single bad cluster doesn't poison others. Verify this is preserved (no shared mutable state across cluster calls).
- Parallel cluster calls must not share rate-limit state in a way that causes thundering-herd 429s — use a bounded concurrent worker pool.

## Recovery

- **Zero themes survive** → run aborts before Phase 6 (no Doc append, no email, no audit "ok"). The audit row records `failed_phase=4` with a human-readable reason.
- **Some themes survive, some dropped** → continue; Phase 5 includes a footer note like "1 candidate theme dropped due to unverifiable quotes" if useful for trust.
