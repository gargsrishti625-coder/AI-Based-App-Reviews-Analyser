# Phase 5 — Report Composition: Implementation

Render validated themes into the two output shapes Phase 6 needs:
- `DocReport`: a structured block list for `docs.batchUpdate` (not raw HTML).
- `EmailReport`: a short HTML + plain-text teaser with a deep-link placeholder.

**See also:** [architecture.md § Phase 5](../architecture.md), [evaluations/phase-5.md](../evaluations/phase-5.md), [edge-cases/phase-5.md](../edge-cases/phase-5.md).

---

## Goals

1. `DocReport` is a structured list of typed blocks consumable by Docs MCP.
2. `EmailReport` includes both HTML and plain-text alternatives with a single deep-link placeholder.
3. The section anchor is **deterministic** from `(product, iso_week)`.
4. Footer discloses missing sources, fallback theming, and run metadata.

---

## Modules

| File | Responsibility |
|---|---|
| `src/pulse/report/anchor.py` | Deterministic anchor for the week |
| `src/pulse/report/doc_blocks.py` | Builds the typed block list for Docs |
| `src/pulse/report/email_render.py` | Jinja2 templates (HTML + text) |
| `src/pulse/core/types.py` | Add `DocReport`, `EmailReport`, `DocBlock` discriminated union |

---

## Data Models

```python
class DocBlock(BaseModel):
    type: Literal["heading_2", "heading_3", "paragraph", "bullet", "blockquote"]
    text: str
    anchor: str | None = None   # set on the H2 only
    attribution: str | None = None  # for blockquote (the review_id)

class DocReport(BaseModel):
    anchor: str                 # e.g. "pulse-groww-2026-W17"
    blocks: list[DocBlock]
    metadata: dict              # for audit traceability

class EmailReport(BaseModel):
    subject: str
    html_body: str              # contains exactly one {{PULSE_DEEP_LINK}}
    text_body: str              # contains exactly one {{PULSE_DEEP_LINK}}
```

---

## Anchor Format

`pulse-{slug(product)}-{iso_week}` — e.g. `pulse-groww-2026-W17`.

```python
def anchor_for(product: str, iso_week: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", product.lower()).strip("-")
    return f"pulse-{slug}-{iso_week}"
```

The format is a **stable contract**. Changing it would break Phase 6 idempotency for prior weeks.

---

## Doc Block Composition

H2 (anchored):
- `Week of {window_end:%Y-%m-%d} (ISO {iso_week}) — {N} reviews`

For each `Theme` (in ranked order):
- H3: `{theme.title}`
- Paragraph: `{theme.summary}`
- Bullets: top supporting bullet points (derived from quotes' surrounding context — keep simple: just the `summary` if no extra context).
- Blockquotes: one per validated quote, with `attribution = quote.review_id` rendered as a small suffix.
- H3 (sub): `Action ideas` — only if `len(theme.action_ideas) > 0`.
- Bullets: each action idea.

Footer (paragraph + bullets):
- `"Who this helps"` line (configurable).
- Run metadata: `LLM: {model} · Window: {start} → {end} · Sources: {counts}`.
- Fallback caveat (if Phase 3 used rating-bucketed): `"Low-volume week — themes derived by rating bucket."`.
- Missing-source note (if Phase 1 had a soft failure): `"Play Store unavailable this week."`.

---

## Email Templates

`templates/email.html.j2`:

```html
<!doctype html><html><body style="font-family:system-ui,sans-serif;">
  <h2 style="margin:0 0 8px;">{{ subject }}</h2>
  <p>This week's top themes:</p>
  <ul>{% for t in top_titles %}<li>{{ t }}</li>{% endfor %}</ul>
  <p><a href="{{ PULSE_DEEP_LINK }}">Read the full pulse →</a></p>
  <p style="color:#666;font-size:12px;">{{ footer_meta }}</p>
</body></html>
```

`templates/email.txt.j2`:

```
{{ subject }}

This week's top themes:
{% for t in top_titles %}- {{ t }}
{% endfor %}
Read the full pulse: {{ PULSE_DEEP_LINK }}

{{ footer_meta }}
```

Both templates render with the literal placeholder `{{PULSE_DEEP_LINK}}` baked in (Jinja `{% raw %}` block) so Phase 6 can substitute via simple string replace. Asserted: each body contains exactly one occurrence.

Subject: `f"Weekly Pulse — {product.display_name} — Week of {window_end:%Y-%m-%d}"`.

---

## Implementation Steps

1. **`anchor.py`** — `anchor_for(product, iso_week)`.
2. **`doc_blocks.py`** — `build_doc_report(themes, plan, corpus_stats, missing_sources, fallback_used) -> DocReport`. Pure function; deterministic.
3. **`email_render.py`**:
   - `render_email_report(themes, plan, anchor) -> EmailReport`.
   - Subject + render both Jinja templates.
   - Assert exactly one `{{PULSE_DEEP_LINK}}` per body via a regex check.
4. **Top-level `compose(themes, plan, corpus_stats, ingest_status) -> tuple[DocReport, EmailReport]`** in `report/__init__.py`.

---

## Tests to Add

Mapped to [evaluations/phase-5.md](../evaluations/phase-5.md):

- `test_three_themes_render_doc_blocks` (P5-E1).
- `test_anchor_format_deterministic` (P5-E2).
- `test_idempotent_render` (P5-E3) — same inputs → byte-identical `DocReport`/`EmailReport`.
- `test_missing_source_footer` (P5-E4).
- `test_email_html_text_equivalence` (P5-E5).
- `test_doc_h2_text_format` (P5-E6).
- `test_no_action_subheading_when_empty` (P5-E7).
- `test_deep_link_placeholder_present_once` (P5-E8).

Edge cases from [edge-cases/phase-5.md](../edge-cases/phase-5.md):

- Product slug with spaces/unicode.
- Long theme titles truncated for headings.
- Smart-quotes preserved.
- HTML email size cap (truncate to top-3 titles if over budget).
- Fallback caveat appears when Phase 3 used rating-bucketed.

---

## Dependencies

- New libs: `jinja2`.

---

## Definition of Done

- `pulse compose --run-id <id>` writes `doc_report.json` and `email.{html,txt}` to `.pulse/runs/<run_id>/`.
- Visual inspection on a real fixture looks correct (hand-check, M3 demo).
- All evaluations P5-E1..E8 pass.
- The anchor format is documented as a stable contract in this file and in `architecture.md`.
