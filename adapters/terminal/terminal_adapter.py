"""
Terminal adapter. Per the design rule "terminal commands always confirm, no
exceptions" — there is deliberately no read-only fast path here that skips
confirmation, even though running `ls` is harmless. Consistency beats
per-command judgment calls about what's "safe enough."

`source` is a logical label (e.g. "local-shell"), not a file path.
"""

from __future__ import annotations

import subprocess
from typing import Any

from adapters.base_adapter import BaseAdapter
from adapters.common.change_preview import ChangePreview


class TerminalAdapter(BaseAdapter):
    kind = "terminal"
    can_execute = True
    requires_confirm = True  # explicitly documented here too — do not change

    def __init__(self, source: str = "local-shell", cwd: str | None = None):
        super().__init__(source)
        self.cwd = cwd
        self._history: list[dict[str, Any]] = []  # {command, stdout, stderr, returncode}

    # ---------- BaseAdapter contract ----------

    def read_all(self) -> str:
        """Dump of command history run in this session (not live shell state)."""
        if not self._history:
            return "(no commands run yet in this session)"
        lines = []
        for entry in self._history:
            lines.append(f"$ {entry['command']}\n{entry['stdout']}{entry['stderr']}")
        return "\n".join(lines)

    def search(self, value: Any) -> list[int]:
        """Return indices into history where the command or its output
        contains the given substring."""
        needle = str(value).lower()
        return [
            i for i, entry in enumerate(self._history)
            if needle in entry["command"].lower()
            or needle in entry["stdout"].lower()
            or needle in entry["stderr"].lower()
        ]

    # ---------- EXECUTE (confirm-first, always) ----------

    def run_command(self, command: str) -> ChangePreview:
        """Returns a preview describing the command that WOULD run.
        Nothing executes until `.apply()` is called."""

        def _apply():
            proc = subprocess.run(
                command, shell=True, cwd=self.cwd,
                capture_output=True, text=True, timeout=30,
            )
            entry = {
                "command": command,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "returncode": proc.returncode,
            }
            self._history.append(entry)
            return entry

        return ChangePreview(
            description="run_command", source=self.source,
            location="shell", old_value=None, new_value=command,
            _apply_fn=_apply,
        )
