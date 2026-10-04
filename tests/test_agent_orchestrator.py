"""Agent unit tests that do not require Azure."""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from session.agent.llm import parse_json
from session.agent.state import AgentState
from session.agent import nodes
from session.session_manager import Session


def test_parse_json_plain_and_fenced():
    assert parse_json('{"intent":"answer"}')["intent"] == "answer"
    assert parse_json('```json\n{"a":1}\n```')["a"] == 1


def test_agent_state_log():
    s = Session("t")
    st = AgentState(session=s, user_message="hi")
    st.log("intent", "answer")
    assert st.trace == ["[intent] answer"]


def test_run_agent_no_sources():
    from session.agent import run_agent

    session = Session("t")
    result = run_agent(session, "What is in A1?")
    assert result.intent == "clarify"
    assert "Merge" in result.answer


def test_run_agent_empty_message():
    from session.agent import run_agent
    from session.session_manager import Session as S

    session = S("t")
    result = run_agent(session, "   ")
    assert "question" in result.answer.lower() or "instruction" in result.answer.lower()


def test_confidence_gate_fails_low_confidence():
    s = Session("t")
    st = AgentState(session=s, user_message="summarize")
    st.intent = "answer"
    st.user_goal = "summarize"
    st.answer_outline = ["point"]
    st.evidence = ["e"]
    st.context = "hello"

    with patch.object(
        nodes.llm,
        "chat_json",
        return_value={
            "pass": True,
            "confidence": 0.4,
            "issues": [],
            "revised_outline": [],
            "revised_evidence": [],
        },
    ):
        nodes.node_verify(st)
    assert st.verified is False
    assert st.answer_confidence == 0.4
    assert st.needs_answer_revision is True


def test_feedback_revises_until_pass_or_max():
    s = Session("t")
    st = AgentState(session=s, user_message="fill A1")
    st.intent = "edit"
    st.user_goal = "fill A1"
    st.must_do = ["write A1"]
    st.ops = [{"op": "write_cell", "target": "a.xlsx", "cell": "A1", "value": "x"}]
    st.context = "CITATIONS: Page 1 — x"
    st.verified = False
    st.answer_confidence = 0.2
    st.verify_issues = ["bad"]

    calls = {"n": 0}

    def fake_plan(state):
        state.log("plan", "mocked")
        return state

    def fake_verify(state):
        calls["n"] += 1
        if calls["n"] >= 2:
            state.verified = True
            state.answer_confidence = 0.9
            state.verify_issues = []
            state.log("verify", "PASS mock")
        else:
            state.verified = False
            state.answer_confidence = 0.3
            state.verify_issues = ["still bad"]
            state.log("verify", "FAIL mock")
        return state

    with patch.object(nodes, "node_plan", side_effect=fake_plan), patch.object(
        nodes, "node_verify", side_effect=fake_verify
    ):
        nodes.node_feedback(st)
    assert st.verified is True
    assert st.revision_count == 2


def test_feedback_stops_at_max_revisions():
    s = Session("t")
    st = AgentState(session=s, user_message="q")
    st.intent = "answer"
    st.verified = False
    st.verify_issues = ["x"]
    st.answer_confidence = 0.1

    def always_fail(state):
        state.verified = False
        state.answer_confidence = 0.1
        state.verify_issues = ["x"]
        return state

    with patch.object(nodes, "node_plan", side_effect=lambda st: st), patch.object(
        nodes, "node_verify", side_effect=always_fail
    ):
        nodes.node_feedback(st)
    assert st.revision_count == nodes.MAX_FEEDBACK_REVISIONS
    assert st.verified is False
    assert st.needs_answer_revision is True


if __name__ == "__main__":
    test_parse_json_plain_and_fenced()
    test_agent_state_log()
    test_run_agent_no_sources()
    test_run_agent_empty_message()
    test_confidence_gate_fails_low_confidence()
    test_feedback_revises_until_pass_or_max()
    test_feedback_stops_at_max_revisions()
    print("All agent tests passed.")
