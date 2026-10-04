"""
Merge AI multi-node orchestrator.

Graph:
  gather → intent → reason → plan → verify → feedback → prepare_edits / answer

Goals vs the old single-shot ask_text path:
  - fuller answers (not forced 2-sentence stubs)
  - command obedience via reason + verify + feedback
  - less hallucination (evidence + verify against sources)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from adapters.common.change_preview import ChangePreview
from session.agent import nodes
from session.agent.state import AgentState
from session.session_manager import Session


@dataclass
class AgentResult:
    intent: str
    answer: str
    previews: list[ChangePreview] = field(default_factory=list)
    ops: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    needs_confirm: bool = False
    trace: list[str] = field(default_factory=list)
    user_goal: str = ""


def run_agent(session: Session, user_message: str) -> AgentResult:
    """Run the full node graph for one user message."""
    state = AgentState(session=session, user_message=(user_message or "").strip())
    if not state.user_message:
        return AgentResult(intent="clarify", answer="Send a question or an edit instruction.")

    if not session.sources:
        return AgentResult(
            intent="clarify",
            answer="Merge a window or file first, then ask or give an edit command.",
        )

    # 1) Context first so intent sees real sources
    nodes.node_gather(state)

    # 2) Intent
    nodes.node_intent(state)
    # Clarify is rare; if set with a final_answer, return once (never loop)
    if state.intent == "clarify" and state.final_answer:
        return _to_result(state)

    # 3) Reason about the literal command
    nodes.node_reason(state)

    # 4) Plan
    nodes.node_plan(state)
    # If plan produced a final statement (missing data), return it — not a re-ask loop
    if state.final_answer and state.intent == "answer" and not state.answer_outline and not state.ops:
        return _to_result(state)

    # 5) Verify obedience / grounding (confidence-gated)
    nodes.node_verify(state)

    # 6) Feedback revisions (up to MAX_FEEDBACK_REVISIONS)
    nodes.node_feedback(state)

    # 7) Branch
    if state.intent == "edit":
        nodes.node_prepare_edits(state)
        nodes.node_answer(state)  # summary text
    else:
        nodes.node_answer(state)
        # Weak answer: regenerate once with verify issues injected
        if state.needs_answer_revision and state.revision_count < nodes.MAX_FEEDBACK_REVISIONS:
            state.revision_count += 1
            state.log("feedback", f"re-answer revision #{state.revision_count}")
            issues = "; ".join(state.verify_issues) or "answer not grounded"
            state.must_do = list(state.must_do) + [f"Re-answer fixing: {issues}"]
            nodes.node_answer(state)

    return _to_result(state)


def _to_result(state: AgentState) -> AgentResult:
    needs_confirm = state.intent == "edit" and bool(state.previews)
    return AgentResult(
        intent=state.intent,
        answer=state.final_answer
        or ("(no answer)" if state.intent != "edit" else "No changes proposed."),
        previews=list(state.previews),
        ops=list(state.ops),
        errors=list(state.preview_errors) + list(state.verify_issues),
        needs_confirm=needs_confirm,
        trace=list(state.trace),
        user_goal=state.user_goal,
    )
