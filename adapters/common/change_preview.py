"""
Shared by every adapter that can write. A write method NEVER applies
directly — it returns a ChangePreview describing what would change, and the
caller (CLI today, overlay confirm-dialog later) must call `.apply()`.

This is the single confirm-first mechanism referenced everywhere in the
design: "reads are automatic, writes/deletes/sends/executes require an
explicit confirm, no exceptions."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class ChangePreview:
    description: str          # short label, e.g. "write_cell", "run_command"
    source: str                # file path, or a logical name for non-file adapters
    location: str               # cell/range, paragraph index, "stdin", etc.
    old_value: Any
    new_value: Any
    _apply_fn: Optional[Callable[[], Any]] = field(repr=False, default=None)
    applied: bool = False
    result: Any = field(default=None, repr=False)  # populated by apply() if _apply_fn returns something

    def apply(self) -> Any:
        if self.applied:
            raise RuntimeError("This change was already applied.")
        if self._apply_fn is None:
            raise RuntimeError("No apply function attached to this preview.")
        self.result = self._apply_fn()
        self.applied = True
        return self.result

    def __str__(self) -> str:
        return (
            f"[{self.source} :: {self.location}] "
            f"{self.old_value!r} -> {self.new_value!r}  ({self.description})"
        )
