"""Real Google Docs API backend for docs_server.

Used when GOOGLE_DOCS_ENABLED=true.  Auth is delegated to
``google_auth.build_service`` so Docs and Gmail share one cached token.

The document must be shared with the authenticating identity (edit access).
"""
from __future__ import annotations

import re

_PULSE_ANCHOR_PREFIX = "pulse-"
_ISO_WEEK_RE = re.compile(r"\(ISO (\d{4}-W\d+)\)")


def _get_service():
    """Return an authenticated Google Docs API service."""
    from pulse.phase_6.mcp_servers.google_auth import build_service

    return build_service("docs", "v1")


def _utf16_len(text: str) -> int:
    """Google Docs API counts indices in UTF-16 code units."""
    return len(text.encode("utf-16-le")) // 2


def get_doc_info(doc_id: str) -> dict:
    """Return revision_id, anchors list, and found flag for *doc_id*."""
    service = _get_service()
    doc = service.documents().get(documentId=doc_id).execute()

    # Named ranges we created carry the pulse anchor names
    named_ranges: dict = doc.get("namedRanges", {})
    anchors = [name for name in named_ranges if name.startswith(_PULSE_ANCHOR_PREFIX)]

    # Fallback: scan heading_2 text for "(ISO YYYY-WNN)" pattern in case the
    # doc was populated without named ranges (manual edits, old runs, etc.)
    content = doc.get("body", {}).get("content", [])
    found_iso_weeks: set[str] = set()
    for element in content:
        para = element.get("paragraph")
        if not para:
            continue
        style = para.get("paragraphStyle", {}).get("namedStyleType", "")
        if style != "HEADING_2":
            continue
        texts = [
            r.get("textRun", {}).get("content", "")
            for r in para.get("elements", [])
        ]
        heading_text = "".join(texts)
        m = _ISO_WEEK_RE.search(heading_text)
        if m:
            found_iso_weeks.add(m.group(1))  # e.g. "2026-W16"

    return {
        "revision_id": doc.get("revisionId"),
        "anchors": anchors,
        "_found_iso_weeks": list(found_iso_weeks),
        "found": True,
    }


def append_blocks(doc_id: str, anchor: str, blocks: list[dict]) -> str:
    """Append *blocks* to the end of *doc_id*; return the new revisionId."""
    service = _get_service()

    # Get current document to find the insertion point
    doc = service.documents().get(documentId=doc_id).execute()
    content = doc.get("body", {}).get("content", [])
    # Insert just before the trailing newline (end_index - 1)
    insert_at = (content[-1]["endIndex"] - 1) if content else 1

    # ── Build the full text string and track each block's character span ──────
    full_text = ""
    # List of (utf16_start, utf16_end, block_type) relative to insert_at
    spans: list[tuple[int, int, str]] = []

    for block in blocks:
        text = block["text"] + "\n"
        start = _utf16_len(full_text)
        full_text += text
        end = _utf16_len(full_text)
        spans.append((start, end, block["type"]))

    requests: list[dict] = []

    # 1. Insert all text in one shot
    requests.append(
        {
            "insertText": {
                "location": {"index": insert_at},
                "text": full_text,
            }
        }
    )

    # 2. Style each block (indices shift by insert_at after the insertText)
    for utf16_start, utf16_end, block_type in spans:
        abs_start = insert_at + utf16_start
        # Style range excludes the trailing \n
        abs_end = insert_at + utf16_end - 1
        if abs_start >= abs_end:
            continue

        # Paragraph named style
        if block_type == "heading_2":
            named_style = "HEADING_2"
        elif block_type == "heading_3":
            named_style = "HEADING_3"
        else:
            named_style = "NORMAL_TEXT"

        requests.append(
            {
                "updateParagraphStyle": {
                    "range": {"startIndex": abs_start, "endIndex": abs_end},
                    "paragraphStyle": {"namedStyleType": named_style},
                    "fields": "namedStyleType",
                }
            }
        )

        if block_type == "blockquote":
            requests.append(
                {
                    "updateParagraphStyle": {
                        "range": {"startIndex": abs_start, "endIndex": abs_end},
                        "paragraphStyle": {
                            "indentFirstLine": {"magnitude": 36, "unit": "PT"},
                            "indentStart": {"magnitude": 36, "unit": "PT"},
                        },
                        "fields": "indentFirstLine,indentStart",
                    }
                }
            )
        elif block_type == "bullet":
            requests.append(
                {
                    "createParagraphBullets": {
                        "range": {"startIndex": abs_start, "endIndex": abs_end},
                        "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
                    }
                }
            )

    # 3. Create a named range on the first (heading_2) block so future
    #    runs can detect this anchor via get_doc_info
    if spans:
        h2_start = insert_at + spans[0][0]
        h2_end = insert_at + spans[0][1] - 1
        if h2_start < h2_end:
            requests.append(
                {
                    "createNamedRange": {
                        "name": anchor,
                        "range": {"startIndex": h2_start, "endIndex": h2_end},
                    }
                }
            )

    result = (
        service.documents()
        .batchUpdate(documentId=doc_id, body={"requests": requests})
        .execute()
    )
    return result.get("documentRevisionId", "unknown")
