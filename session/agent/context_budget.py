"""
Keep Azure requests under the deployment context limit.

Rough heuristic: ~4 characters ≈ 1 token for English/code text.
Images are handled separately (resized before upload).
"""

from __future__ import annotations

import os


def max_context_chars() -> int:
    """Total chars allowed in merged SOURCE DATA blocks per LLM call."""
    try:
        # Default ~48k chars ≈ 12k tokens — leaves headroom for system prompts
        # and multi-node calls under a 272k context window.
        return max(4000, int(os.environ.get("DAI_MAX_CONTEXT_CHARS", "48000")))
    except ValueError:
        return 48000


def max_source_chars() -> int:
    try:
        return max(1500, int(os.environ.get("DAI_MAX_SOURCE_CHARS", "10000")))
    except ValueError:
        return 10000


def excel_max_rows() -> int:
    try:
        return max(10, int(os.environ.get("DAI_EXCEL_MAX_ROWS", "40")))
    except ValueError:
        return 40


def truncate_text(text: str, limit: int, label: str = "content") -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    keep = max(0, limit - 80)
    return text[:keep] + f"\n…({label} truncated, {len(text) - keep} chars omitted)"


def fit_chunks(chunks: list[str], total_limit: int | None = None) -> str:
    """Join chunks, trimming from the end until under total_limit."""
    limit = total_limit if total_limit is not None else max_context_chars()
    if not chunks:
        return "(no merged source content)"
    # Prefer keeping earlier sources; shrink later ones first
    parts = list(chunks)
    per = max(800, limit // max(1, len(parts)))
    parts = [truncate_text(p, per, "source") for p in parts]
    joined = "\n\n".join(parts)
    if len(joined) <= limit:
        return joined
    return truncate_text(joined, limit, "context")


def estimate_tokens(text: str) -> int:
    return max(1, (len(text or "") + 3) // 4)
