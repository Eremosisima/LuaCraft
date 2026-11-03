"""Static analysis of a Lua source before it is compiled.

The analyser walks the AST and decides, for the selected edition:

* whether every primitive call is available there, and at what level;
* whether the source uses any construct that has no equivalent at all;
* which source lines a target specific hook covers.

It never mutates the AST.  Its output is a list of diagnostics plus a summary
of what the source contains, which the backends use to plan their output.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .api import (
    Capability,
    Edition,
    PRIMITIVE_BY_NAME,
    get_primitive,
)
from .diagnostics import Category, DiagnosticBag
from .luaparse import (
    Assign,
    Call,
    Chunk,
    ExprStat,
    Function,
    FunctionBody,
    GenericFor,
    If,
    Index,
    LocalAssign,
    LocalFunction,
    Name,
    Node,
    NumericFor,
    Repeat,
    Table,
    While,
    walk,
)


#: Functions whose body is only emitted for one edition.
TARGET_HOOKS: dict[str, Edition] = {
    "onJavaOnly": Edition.JAVA,
    "onBedrockOnly": Edition.BEDROCK,
    "onLuantiOnly": Edition.LUANTI,
}

#: Hooks that always run in both editions.
PORTABLE_HOOKS = frozenset({"onTick", "onJoin", "onLoad"})


def _unavailable_category(edition: Edition) -> Category:
    """The diagnostic category for a capability missing in an edition."""
    if edition is Edition.JAVA:
        return Category.JAVA_UNAVAILABLE
    if edition is Edition.BEDROCK:
        return Category.BEDROCK_UNAVAILABLE
    return Category.LUANTI_UNAVAILABLE


@dataclass
class SourceInfo:
    """What the analyser found in the source."""

    #: Names of every primitive that is called, in order of first use.
    primitives_used: list[str] = field(default_factory=list)
    #: Names of every target specific hook that appears.
    hooks_used: list[str] = field(default_factory=list)
    #: True when the source declares at least one function.
    has_functions: bool = False
    #: True when the source contains a loop over players or entities.
    iterates: bool = False
    #: True when the source reads or writes a scoreboard.
    uses_scoreboard: bool = False

    def summary(self) -> dict[str, object]:
        return {
            "primitives_used": list(self.primitives_used),
            "hooks_used": list(self.hooks_used),
            "has_functions": self.has_functions,
            "iterates": self.iterates,
            "uses_scoreboard": self.uses_scoreboard,
        }


class Analyser:
    """Walks a parsed chunk and reports availability problems."""

    def __init__(self, edition: Edition, diagnostics: DiagnosticBag | None = None) -> None:
        self.edition = edition
        # Note: an empty DiagnosticBag is falsy, so it cannot be tested with
        # `or` here; the identity check keeps the caller's bag.
        self.diagnostics = diagnostics if diagnostics is not None else DiagnosticBag()
        self.info = SourceInfo()
        self._seen_primitives: set[str] = set()
        #: Stack of booleans: True while inside a hook for the other edition.
        self._foreign_hook_depth = 0
        self._foreign_hook_name = ""

    # -- public -----------------------------------------------------------

    def analyse(self, chunk: Chunk) -> SourceInfo:
        """Analyse a chunk and return what was found."""
        for node in walk(chunk):
            self._visit(node)
        return self.info

    # -- internals --------------------------------------------------------

    def _visit(self, node: Node) -> None:
        if isinstance(node, Call):
            self._visit_call(node)
        elif isinstance(node, Function):
            self.info.has_functions = True
        elif isinstance(node, FunctionBody):
            self.info.has_functions = True
        elif isinstance(node, Assign) and node.targets and isinstance(
            node.targets[0], Name
        ):
            # A global assignment such as `items.ruby = {...}`.
            pass
        elif isinstance(node, Table):
            pass
        elif isinstance(node, (If, While, Repeat, NumericFor, GenericFor)):
            self.info.iterates = True

    def _visit_call(self, node: Call) -> None:
        if node.method:
            # Method call: obj:method(...). Not part of the portable API yet.
            return
        if not isinstance(node.func, Name):
            return
        name = node.func.identifier

        if name in TARGET_HOOKS:
            self._check_target_hook(node, name)
            return

        if name in PRIMITIVE_BY_NAME:
            primitive = get_primitive(name)
            assert primitive is not None
            if name not in self._seen_primitives:
                self._seen_primitives.add(name)
                self.info.primitives_used.append(name)
            if name == "forEachPlayer" or name == "forEachEntity":
                self.info.iterates = True
            if name in ("score", "addObjective"):
                self.info.uses_scoreboard = True
            self._check_availability(node, primitive.name)
            return

        # Unknown global: could be a user defined global or a typo. We only
        # report it when it looks like one of the documented API names.
        if name.startswith("on") and name.endswith(("Tick", "Join", "Load")):
            self.diagnostics.warn(
                Category.UNKNOWN_API,
                f"unknown hook {name!r}",
                node.line,
                symbol=name,
                snippet=name,
                hint=(
                    "known hooks are onTick, onJoin, onLoad, onJavaOnly, "
                    "onBedrockOnly and onLuantiOnly"
                ),
            )

    def _check_target_hook(self, node: Call, name: str) -> None:
        hook_edition = TARGET_HOOKS[name]
        if name not in self.info.hooks_used:
            self.info.hooks_used.append(name)
        if hook_edition is self.edition:
            return
        # The hook belongs to the other edition: its body will not be emitted.
        self.diagnostics.warn(
            Category.UNSUPPORTED_TARGET,
            (
                f"{name} is a {hook_edition.label} only hook, so its body will "
                f"not be included in the {self.edition.label} output"
            ),
            node.line,
            symbol=name,
            snippet=name,
            edition=hook_edition.value,
            hint=(
                f"the {hook_edition.label} build keeps this code; the "
                f"{self.edition.label} build drops it"
            ),
        )

    def _check_availability(self, node: Call, name: str) -> None:
        primitive = get_primitive(name)
        if primitive is None:  # pragma: no cover - defensive
            return
        capability = primitive.capability(self.edition)

        if capability is Capability.FULL:
            return

        other = (
            Edition.BEDROCK if self.edition is Edition.JAVA else Edition.JAVA
        )
        other_capability = primitive.capability(other)
        if capability is Capability.NONE:
            # This primitive simply does not exist here, so suggest the hook
            # of an edition that does support it.  That keeps the code out of
            # this build while remaining useful in the others.
            supported = next(
                (
                    edition
                    for edition in Edition
                    if edition is not self.edition
                    and primitive.capability(edition) is not Capability.NONE
                ),
                self.edition,
            )
            self.diagnostics.error(
                _unavailable_category(self.edition),
                (
                    f"{primitive.name}() is not available in "
                    f"{self.edition.label}: {primitive.notes.get(self.edition, '')}"
                ).strip(),
                node.line,
                symbol=primitive.name,
                snippet=f"{primitive.name}(...)",
                edition=self.edition.value,
                hint=(
                    f"use the on{supported.hook_suffix} hook to keep this "
                    f"code specific to {supported.label}"
                ),
            )
            return

        # PARTIAL: available, but the behaviour differs.
        note = primitive.notes.get(self.edition, "")
        self.diagnostics.warn(
            Category.PARTIAL_SUPPORT,
            (
                f"{primitive.name}() is only partially supported in "
                f"{self.edition.label}"
            ),
            node.line,
            symbol=primitive.name,
            snippet=f"{primitive.name}(...)",
            edition=self.edition.value,
            hint=note or "the generated code approximates the intended behaviour",
        )


def analyse(chunk: Chunk, edition: Edition, diagnostics: DiagnosticBag | None = None) -> SourceInfo:
    """Convenience wrapper around :class:`Analyser`."""
    return Analyser(edition, diagnostics).analyse(chunk)
