"""Shared Azure chat helper for agent nodes (higher token budgets than ask_text)."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

from adapters.vision.vision_adapter import VisionAdapter, _load_dotenv


def chat(
    messages: list[dict[str, Any]],
    *,
    max_tokens: int = 600,
    temperature: float = 0.1,
) -> str:
    """Low-temperature chat for grounded planning / answering."""
    _load_dotenv()
    endpoint = (
        os.environ.get("DAI_AZURE_OPENAI_ENDPOINT")
        or os.environ.get("AZURE_OPENAI_ENDPOINT")
        or ""
    ).rstrip("/")
    if not endpoint:
        raise RuntimeError("DAI_AZURE_OPENAI_ENDPOINT not set.")
    helper = VisionAdapter("agent")
    return helper._chat_azure(
        endpoint, messages, max_completion_tokens=max_tokens
    )


def chat_json(
    system: str,
    user: str,
    *,
    max_tokens: int = 700,
) -> dict[str, Any]:
    raw = chat(
        [
            {
                "role": "system",
                "content": system
                + "\n\nReturn ONLY valid JSON. No markdown fences. No commentary.",
            },
            {"role": "user", "content": user},
        ],
        max_tokens=max_tokens,
        temperature=0.05,
    )
    return parse_json(raw)


def parse_json(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
        return {"value": data}
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError(f"Model did not return JSON: {text[:400]}")
        data = json.loads(match.group(0))
        if not isinstance(data, dict):
            raise ValueError("JSON root must be an object")
        return data


def chat_text(
    system: str,
    user: str,
    *,
    max_tokens: int = 800,
) -> str:
    return chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=max_tokens,
        temperature=0.15,
    ).strip()
