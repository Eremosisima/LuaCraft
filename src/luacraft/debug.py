"""Debug tracing for the compiler pipeline.

When ``--debug`` is on, LuaCraft records what every phase did and how long it
took: the size of the source, the number of statements the parser produced,
the diagnostics the analyser raised, the content the reader found, the files
each backend emitted and the textures that were attached.

The trace is data, not log noise, so the CLI, the GUI and the MCP server can
all show the same thing.  It is safe to keep on: the trace never touches the
network and never includes file contents, only counts and names.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator


@dataclass
class TraceEvent:
    """One phase of a build."""

    phase: str
    detail: str = ""
    duration_ms: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "detail": self.detail,
            "duration_ms": round(self.duration_ms, 3),
        }


@dataclass
class Trace:
    """The recorded phases of one build."""

    enabled: bool = False
    events: list[TraceEvent] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)

    @contextmanager
    def phase(self, name: str) -> Iterator[dict[str, Any]]:
        """Time a phase and record it, even when it raises."""
        if not self.enabled:
            yield {}
            return
        started = time.perf_counter()
        state: dict[str, Any] = {}
        try:
            yield state
        finally:
            elapsed = (time.perf_counter() - started) * 1000.0
            detail = str(state.get("detail", ""))
            self.events.append(
                TraceEvent(phase=name, detail=detail, duration_ms=elapsed)
            )

    def record(self, phase: str, detail: str = "") -> None:
        if self.enabled:
            self.events.append(TraceEvent(phase=phase, detail=detail))

    def set(self, key: str, value: Any) -> None:
        if self.enabled:
            self.data[key] = value

    @property
    def total_ms(self) -> float:
        return sum(event.duration_ms for event in self.events)

    def as_dict(self) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False}
        return {
            "enabled": True,
            "total_ms": round(self.total_ms, 3),
            "events": [event.as_dict() for event in self.events],
            "data": self.data,
        }

    def format(self) -> str:
        """A compact, human readable trace for the CLI."""
        if not self.enabled:
            return ""
        lines = ["debug trace"]
        for event in self.events:
            line = f"  {event.duration_ms:8.3f} ms  {event.phase}"
            if event.detail:
                line += f"  ({event.detail})"
            lines.append(line)
        for key, value in sorted(self.data.items()):
            lines.append(f"  {key}: {value}")
        lines.append(f"  total: {self.total_ms:.3f} ms")
        return "\n".join(lines)
