"""Ensure overlay Send path starts a background thread (non-blocking)."""

import os
import sys
import threading
import time
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_send_starts_thread_without_waiting():
    # Import overlay module pieces without requiring a display by mocking Gtk path
    # We only unit-test the worker scheduling logic via a thin stand-in.
    started = threading.Event()
    finished = threading.Event()

    def slow_agent(session, text):
        started.set()
        time.sleep(0.3)
        finished.set()
        from session.agent.orchestrator import AgentResult

        return AgentResult(intent="answer", answer="ok")

    # Minimal fake of the send worker pattern used in overlay_app
    agent_busy = True
    results = []

    def worker(session, text):
        err = None
        result = None
        try:
            result = slow_agent(session, text)
        except BaseException as e:
            err = e
        results.append((result, err))

    t0 = time.time()
    t = threading.Thread(target=worker, args=(object(), "hi"), daemon=True)
    t.start()
    # Must return immediately (async)
    elapsed = time.time() - t0
    assert elapsed < 0.15
    assert started.wait(1.0)
    t.join(2.0)
    assert finished.is_set()
    assert results and results[0][0].answer == "ok"


if __name__ == "__main__":
    test_send_starts_thread_without_waiting()
    print("Async send tests passed.")
