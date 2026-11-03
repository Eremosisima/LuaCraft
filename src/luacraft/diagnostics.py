"""Diagnostics: warnings and errors raised while compiling a Lua source.

Every diagnostic carries the exact Lua line where the problem originates, so
that a failure in the generated ``.mcfunction`` or ``.js`` file can be traced
back to the source the author actually wrote.

The distinction that matters for LuaCraft:

``availability``
    A capability that exists in one edition but not the other.  These are
    reported as warnings: the build succeeds, but the author is told exactly
    what will be missing in the target they selected.

``error``
    The source cannot be expressed in the selected target at all (for example
    calling a Bedrock-only primitive while compiling for Java).  These abort
    the build.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable


class Severity(Enum):
    """How serious a diagnostic is."""

    WARNING = "warning"
    ERROR = "error"


class Category(Enum):
    """What kind of problem the diagnostic describes."""

    #: A capability that exists in Java Edition but not in Bedrock Edition.
    BEDROCK_UNAVAILABLE = "bedrock-unavailable"
    #: A capability that exists in Bedrock Edition but not in Java Edition.
    JAVA_UNAVAILABLE = "java-unavailable"
    #: A capability that exists in Luanti but not in the other editions.
    LUANTI_UNAVAILABLE = "luanti-unavailable"
    #: A capability that is only partially supported by one of the editions.
    PARTIAL_SUPPORT = "partial-support"
    #: The source uses an API that does not exist at all.
    UNKNOWN_API = "unknown-api"
    #: The source is not valid for the selected target.
    UNSUPPORTED_TARGET = "unsupported-target"
    #: Syntax or name resolution problems.
    SYNTAX = "syntax"


#: Human readable explanation for each category.
CATEGORY_EXPLANATION: dict[Category, str] = {
    Category.BEDROCK_UNAVAILABLE: (
        "this capability does not exist in Minecraft Bedrock Edition"
    ),
    Category.JAVA_UNAVAILABLE: (
        "this capability does not exist in Minecraft Java Edition"
    ),
    Category.LUANTI_UNAVAILABLE: (
        "this capability does not exist in Luanti"
    ),
    Category.PARTIAL_SUPPORT: (
        "this capability is only partially supported in one of the editions"
    ),
    Category.UNKNOWN_API: "no such API function or declaration",
    Category.UNSUPPORTED_TARGET: "not supported by the selected target",
    Category.SYNTAX: "the source could not be parsed",
}


@dataclass(frozen=True)
class Diagnostic:
    """A single warning or error, anchored to a source location."""

    severity: Severity
    category: Category
    message: str
    #: 1-based line number in the Lua source.
    line: int
    #: Optional source snippet, usually the offending call.
    snippet: str = ""
    #: Name of the API primitive or declaration involved, when applicable.
    symbol: str = ""
    #: The edition the problem applies to ("bedrock" or "java").
    edition: str = ""
    #: Suggested remediation for the author.
    hint: str = ""

    def format(self, source_name: str = "<source>") -> str:
        """Render the diagnostic as a single human readable line."""
        location = f"{source_name}:{self.line}"
        prefix = "error" if self.severity is Severity.ERROR else "warning"
        out = f"{location}: {prefix}: {self.message}"
        if self.snippet:
            out += f"\n    | {self.snippet.strip()}"
        if self.hint:
            out += f"\n    = hint: {self.hint}"
        return out

    def as_dict(self) -> dict[str, object]:
        """Machine readable representation, used by the JSON report."""
        return {
            "severity": self.severity.value,
            "category": self.category.value,
            "message": self.message,
            "line": self.line,
            "snippet": self.snippet,
            "symbol": self.symbol,
            "edition": self.edition,
            "hint": self.hint,
        }


class DiagnosticBag:
    """Collects diagnostics and keeps track of whether the build may proceed."""

    def __init__(self) -> None:
        self._items: list[Diagnostic] = []

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterable[Diagnostic]:
        return iter(self._items)

    @property
    def items(self) -> list[Diagnostic]:
        return list(self._items)

    def add(self, diagnostic: Diagnostic) -> None:
        self._items.append(diagnostic)

    def warn(
        self,
        category: Category,
        message: str,
        line: int,
        *,
        snippet: str = "",
        symbol: str = "",
        edition: str = "",
        hint: str = "",
    ) -> None:
        """Record a warning."""
        self.add(
            Diagnostic(
                severity=Severity.WARNING,
                category=category,
                message=message,
                line=line,
                snippet=snippet,
                symbol=symbol,
                edition=edition,
                hint=hint,
            )
        )

    def error(
        self,
        category: Category,
        message: str,
        line: int,
        *,
        snippet: str = "",
        symbol: str = "",
        edition: str = "",
        hint: str = "",
    ) -> None:
        """Record an error."""
        self.add(
            Diagnostic(
                severity=Severity.ERROR,
                category=category,
                message=message,
                line=line,
                snippet=snippet,
                symbol=symbol,
                edition=edition,
                hint=hint,
            )
        )

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self._items if d.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Diagnostic]:
        return [d for d in self._items if d.severity is Severity.WARNING]

    @property
    def has_errors(self) -> bool:
        return any(d.severity is Severity.ERROR for d in self._items)

    def extend(self, other: "DiagnosticBag") -> None:
        self._items.extend(other._items)
