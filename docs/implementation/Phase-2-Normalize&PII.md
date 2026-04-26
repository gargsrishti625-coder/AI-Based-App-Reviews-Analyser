# Phase 2 — Normalize, PII Scrub, Filter: Implementation

Convert `RawReview[]` into `CleanReview[]` — normalized, PII-scrubbed, deduplicated. After this phase, no downstream code (LLM, Doc, email) ever sees raw user PII.

**See also:** [architecture.md § Phase 2](../architecture.md), [evaluations/phase-2.md](../evaluations/phase-2.md), [edge-cases/phase-2.md](../edge-cases/phase-2.md).

---

## Goals

1. Mandatory PII scrub before the text reaches the LLM or any output channel.
2. Deduplicate on a stable `text_hash` after normalization.
3. Filter low-signal reviews (too short, non-target language).
4. Produce `corpus_stats` that reconciles arithmetically (`total_in == total_out + drops + dedupes`).

The normalization choices here form a **contract** with Phase 4's quote validator — the validator must apply the same normalization before substring matching.

---

## Modules

| File | Responsibility |
|---|---|
| `src/pulse/preprocess/normalize.py` | Whitespace, unicode (NFC), emoji handling, title+body merge |
| `src/pulse/preprocess/pii.py` | Regex (always) + optional NER scrub for emails, phones, account numbers |
| `src/pulse/preprocess/filter.py` | Length, language, exact-dup filters; produces `corpus_stats` |
| `src/pulse/core/types.py` | Add `CleanReview`, `CorpusStats` |

---

## Data Models

```python
class CleanReview(BaseModel):
    review_id: str            # source-prefixed: "app_store:12345"
    source: str
    product: str
    rating: int
    locale: str | None
    posted_at: datetime
    app_version: str | None
    text: str                 # normalized + PII-scrubbed
    text_hash: str            # sha256 of NFC + whitespace-collapsed text

class CorpusStats(BaseModel):
    total_in: int
    total_out: int
    dropped_pii: int          # reviews where scrub yielded an empty/too-short text
    dropped_short: int
    dropped_lang: int
    dedup_count: int
```

---

## Library Choices

| Concern | Lib |
|---|---|
| Unicode normalization | stdlib `unicodedata.normalize("NFC", ...)` |
| Language detection | `lingua-language-detector` (more accurate than `langdetect`, deterministic) |
| NER (optional) | `spacy` `en_core_web_sm` for person names — gated behind config flag and confidence threshold |
| Regex | stdlib `re`; compile patterns once at import |

---

## Normalization Contract (Phase 4 must mirror)

1. `unicodedata.normalize("NFC", text)` — pick ONE form, document it.
2. Strip zero-width and bidi control characters.
3. Collapse runs of whitespace to a single space; preserve newlines as-is.
4. **Do not** lowercase. **Do not** remove punctuation. (These would weaken quote evidentiality.)
5. Emoji: keep; they don't count toward `min_tokens`.

`util/text.py` exposes `normalize_for_match(text: str) -> str` — used by both Phase 2 (when computing `text_hash`) and Phase 4 (when validating quotes). One function, two callers.

---

## PII Patterns

Compile once in `pii.py`:

```python
EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
EMAIL_OBFUSCATED = re.compile(r"\b[A-Z0-9._%+-]+\s*[\(\[]?\s*at\s*[\)\]]?\s*[A-Z0-9.-]+\s*[\(\[]?\s*dot\s*[\)\]]?\s*[A-Z]{2,}\b", re.I)
PHONE_INTL = re.compile(r"\+?\d[\d\s\-\(\)]{8,16}\d")
ACCOUNT = re.compile(r"\b\d{10,16}\b")  # see exclusions below
URL = re.compile(r"https?://\S+")
```

Replacement: `[email]`, `[phone]`, `[account]`, `[url]`.

**False-positive guards** before replacing `ACCOUNT`:
- skip if the match is part of an app version (`v1.2.3`, `1.2.3.456`)
- skip if the match is a 4-digit year between 1900 and current+5
- skip if surrounded by ratings keywords

Order of operations matters: scrub URLs first (so emails inside URLs don't survive), then emails, then phones, then accounts.

---

## Implementation Steps

1. **`util/text.py`** — `normalize_for_match()` and `text_hash()`.
2. **`preprocess/normalize.py`** — `merge_title_body()`, `normalize_text()`. Title and body joined by `"\n"`.
3. **`preprocess/pii.py`** — `scrub_pii(text) -> tuple[str, dict[str, int]]` returning the cleaned text and per-category match counts.
4. **`preprocess/filter.py`**:
   - `is_too_short(text, min_tokens=10)` — split on whitespace, count.
   - `is_target_language(text, target="en")` — only drop on high-confidence non-target.
   - `dedup_by_hash(reviews) -> tuple[list[CleanReview], int]`.
5. **Top-level `clean(reviews: list[RawReview]) -> tuple[list[CleanReview], CorpusStats]`** in `preprocess/__init__.py`. Keep counters local; assert reconciliation at exit:

   ```python
   assert stats.total_in == stats.total_out + stats.dropped_pii + stats.dropped_short + stats.dropped_lang + stats.dedup_count
   ```

6. **`review_id` rewrite** to source-prefixed form: `f"{review.source}:{review.review_id}"`.
7. **Persist `CorpusStats`** via the audit hook (Phase 7 reads it later).

---

## Tests to Add

Mapped to [evaluations/phase-2.md](../evaluations/phase-2.md):

- Email/phone/account scrub round-trips (P2-E1..3).
- Whitespace/case dedup (P2-E4).
- Length filter (P2-E5), language filter (P2-E6).
- Title+body merge (P2-E7), emoji-only drop (P2-E8).
- `corpus_stats` reconciles (P2-E9) — assert in helper.

Edge cases from [edge-cases/phase-2.md](../edge-cases/phase-2.md):

- Obfuscated emails (`user [at] example [dot] com`), spaced phone digits, Arabic-Indic digits, account-number false positives (year, version), reviews that become empty after scrub, NER misfires on product names, smart-vs-straight quotes.
- **Quote-validation parity test** in `tests/integration/`: take a `CleanReview.text`, extract a substring, run it through `normalize_for_match` and assert it's a substring of the original normalized text. Catches drift between Phase 2 and Phase 4.

---

## Dependencies

- New libs: `lingua-language-detector`. `spacy` only if NER is enabled (gated).

---

## Definition of Done

- `pulse clean --run-id <id>` debug command outputs `CleanReview[]` JSONL and `corpus_stats`.
- An end-to-end smoke test asserts no email/phone match in any rendered Doc block (Phase 5 output).
- All evaluations P2-E1..E9 pass; all edge cases covered by tests.
- The shared `normalize_for_match` is the only normalization function used by both Phase 2 and Phase 4.
