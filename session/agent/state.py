"""Shared state passed between agent nodes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from adapters.common.change_preview import ChangePreview
from session.session_manager import Session


@dataclass
class AgentState:
    session: Session
    user_message: str

    # Node outputs
    intent: str = ""  # edit | answer | clarify
    intent_confidence: float = 0.0
    intent_rationale: str = ""

    context: str = ""
    source_names: list[str] = field(default_factory=list)
    has_vision: bool = False
    vision_image_path: Optional[str] = None

    # Literal command understanding (anti-drift)
    user_goal: str = ""
    must_do: list[str] = field(default_factory=list)
    must_not: list[str] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)

    # Plan
    ops: list[dict[str, Any]] = field(default_factory=list)
    answer_outline: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)

    # Verify / feedback (confidence-gated, no golden answers)
    verified: bool = False
    verify_issues: list[str] = field(default_factory=list)
    revision_count: int = 0
    answer_confidence: float = 0.0
    needs_answer_revision: bool = False

    # Final
    previews: list[ChangePreview] = field(default_factory=list)
    preview_errors: list[str] = field(default_factory=list)
    final_answer: str = ""

    trace: list[str] = field(default_factory=list)

    def log(self, node: str, msg: str) -> None:
        self.trace.append(f"[{node}] {msg}")
