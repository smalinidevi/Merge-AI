"""Multi-node Merge AI agent: intent → gather → reason → plan → verify → answer."""

from session.agent.orchestrator import AgentResult, run_agent

__all__ = ["AgentResult", "run_agent"]
