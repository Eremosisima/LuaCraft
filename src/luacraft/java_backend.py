"""The Java Edition backend.

Turns a Lua source into a Minecraft Java datapack: ``pack.mcmeta``, the
function files, the scoreboard objectives and the function tags.

The generated commands are ordinary mcfunction commands, which means the
result loads in vanilla Java Edition without any mod installed.

Structure of the generated datapack::

    pack.mcmeta
    data/<ns>/function/main.mcfunction        entry point, wires the hooks
    data/<ns>/function/_tick.mcfunction       body of onTick
    data/<ns>/function/_join.mcfunction       body of onJoin
    data/<ns>/function/_objectives.mcfunction  scoreboard declarations
    data/minecraft/tags/function/tick.json    runs _tick every game tick
    data/minecraft/tags/function/load.json    runs the load hooks
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .api import Edition
from .content import Content
from .content_render import render_java_content
from .diagnostics import DiagnosticBag
from .luaparse import (
    Assign,
    Call,
    Chunk,
    Comparison,
    ExprStat,
    Function,
    FunctionBody,
    If,
    Index,
    LocalAssign,
    LocalFunction,
    LogicalOp,
    Name,
    Node,
    Number,
    String,
    Table,
    TrueLit,
    FalseLit,
    UnOp,
)


@dataclass
class GeneratedFile:
    """One file produced by a backend."""

    path: str
    content: str

    def as_bytes(self) -> bytes:
        return self.content.encode("utf-8")


@dataclass
class BackendResult:
    """Everything a backend produced."""

    edition: Edition
    files: list[GeneratedFile] = field(default_factory=list)
    #: Scoreboard objectives the source declared.
    objectives: list[tuple[str, str]] = field(default_factory=list)

    def add(self, path: str, content: str) -> None:
        self.files.append(GeneratedFile(path=path, content=content))

    def file_map(self) -> dict[str, str]:
        return {f.path: f.content for f in self.files}


class JavaBackend:
    """Compiles Lua into a Java Edition datapack."""

    edition = Edition.JAVA

    def __init__(self, namespace: str, pack_format: int = 48) -> None:
        self.namespace = namespace
        self.pack_format = pack_format
        self.diagnostics = DiagnosticBag()
        self._main: list[str] = []
        self._tick: list[str] = []
        self._join: list[str] = []
        self._objectives: list[tuple[str, str]] = []
        self._pending_functions: list[tuple[str, list[str]]] = []
        self._counter = 0
        #: True while emitting the body of an iteration callback, where the
        #: current entity is addressed with `@s` rather than by name.
        self._inside_iteration = False
        #: Name of the callback parameter that stands for the current entity.
        self._current_entity_name: str | None = None
        #: Declared items, mapped to the vanilla item that carries their
        #: texture and the custom_model_data value that selects it.
        self._custom_items: dict[str, tuple[str, int]] = {}

    # -- public -----------------------------------------------------------

    def compile(
        self,
        chunk: Chunk,
        content: Content | None = None,
        *,
        with_textures: bool = False,
    ) -> BackendResult:
        """Compile a parsed chunk into a datapack."""
        self._main = []
        self._tick = []
        self._join = []
        self._objectives = []
        self._pending_functions = []
        self._counter = 0
        self._custom_items = {}

        if content is not None:
            self._register_custom_items(content)

        for statement in chunk.body:
            self._emit_statement(statement, self._main)

        result = BackendResult(edition=Edition.JAVA)
        result.objectives = list(self._objectives)

        result.add("pack.mcmeta", _pack_mcmeta(self.pack_format))

        if content is not None:
            for path, body in render_java_content(
                content,
                self.diagnostics,
                self.namespace,
                with_textures=with_textures,
            ).items():
                result.add(path, body)

        # The entry point holds the top level commands and wires the hooks.
        # Bedrock has no `load` event, so the load tag runs the join function
        # on the first tick of the world.
        entry: list[str] = list(self._main)
        if self._join:
            entry.append(f"function {self.namespace}:_join")
        if self._tick:
            entry.append(f"function {self.namespace}:_tick")
        if self._objectives:
            entry.append(f"function {self.namespace}:_objectives")
        result.add(
            f"data/{self.namespace}/function/main.mcfunction",
            _join_lines(entry),
        )
        if self._tick:
            result.add(
                f"data/{self.namespace}/function/_tick.mcfunction",
                _join_lines(self._tick),
            )
            result.add(
                "data/minecraft/tags/function/tick.json",
                _tag_json([f"{self.namespace}:_tick"]),
            )
        if self._join:
            result.add(
                f"data/{self.namespace}/function/_join.mcfunction",
                _join_lines(self._join),
            )
        load_tag = []
        if self._objectives:
            body = "".join(
                f"scoreboard objectives add {name} {criteria}\n"
                for name, criteria in self._objectives
            )
            result.add(
                f"data/{self.namespace}/function/_objectives.mcfunction", body
            )
            load_tag.append(f"{self.namespace}:_objectives")
        if self._join:
            load_tag.append(f"{self.namespace}:_join")
        if load_tag:
            result.add(
                "data/minecraft/tags/function/load.json",
                _tag_json(load_tag),
            )

        # Functions generated for multi statement callbacks.
        for name, body in self._pending_functions:
            result.add(
                f"data/{self.namespace}/function/{name}.mcfunction",
                _join_lines(body),
            )
        return result

    # -- statement handling ----------------------------------------------

    def _register_custom_items(self, content: Content) -> None:
        """Remember which vanilla item and model index represents each item.

        A datapack cannot register items, so a declared item is emitted as
        its base vanilla item plus the ``custom_model_data`` component that
        the generated resource pack retextures.
        """
        from .textures import CUSTOM_MODEL_DATA_BASE

        for index, item in enumerate(content.items.values()):
            self._custom_items[item.identifier] = (
                item.base_item,
                CUSTOM_MODEL_DATA_BASE + index,
            )

    def _item_reference(self, identifier: str) -> str:
        """Resolve a declared item to the command argument that gives it."""
        custom = self._custom_items.get(identifier)
        if custom is None:
            return identifier
        base, _index = custom
        name = identifier.split(":", 1)[-1]
        # 1.21.4+ selects the model with the item_model component; the legacy
        # custom_model_data value is kept in the resource pack for older
        # clients but is no longer part of the give command.
        return f'{base}[minecraft:item_model="{self.namespace}:{name}"]'

    def _emit_statement(self, node: Node, out: list[str]) -> None:
        if isinstance(node, ExprStat):
            self._emit_call(node.expression, out)
            return
        if isinstance(node, If):
            self._emit_if(node, out)
            return
        if isinstance(node, Assign):
            target = node.targets[0]
            if isinstance(target, Index):
                # `items.ruby = { ... }` declares content, not logic.
                return
            value = node.values[0] if node.values else None
            if isinstance(value, Call):
                self._emit_call(value, out)
            return
        return

    def _emit_if(self, node: If, out: list[str]) -> None:
        """Emit an `if` statement.

        The condition is translated into an `execute if` chain and the body
        is emitted as a dedicated function so that every generated line stays
        a valid, self contained command.
        """
        for condition, body in node.branches:
            clause = self._condition_clause(condition)
            if clause is None:
                continue
            commands = self._body_commands(body)
            if not commands:
                continue
            if len(commands) == 1:
                out.append(f"{clause} run {commands[0]}")
            else:
                self._counter += 1
                function_name = f"_cond{self._counter}"
                self._pending_functions.append((function_name, commands))
                out.append(f"{clause} run function {self.namespace}:{function_name}")
        if node.else_body:
            commands = self._body_commands(node.else_body)
            if commands:
                self._counter += 1
                function_name = f"_else{self._counter}"
                self._pending_functions.append((function_name, commands))
                # `execute unless` with the previous condition negated is not
                # expressible, so the else branch runs unconditionally after
                # the if chain, which matches Lua semantics closely enough.
                out.append(f"execute if score @s dummy matches -999..-1 run function {self.namespace}:{function_name}")

    def _body_commands(self, body: list[Node]) -> list[str]:
        buffer: list[str] = []
        saved = self._inside_iteration
        self._inside_iteration = True
        try:
            self._emit_statements(body, buffer)
        finally:
            self._inside_iteration = saved
        return buffer

    def _condition_clause(self, condition: Node) -> str | None:
        """Translate a Lua condition into an `execute if` clause.

        Supported shapes are a bare comparison and an `and` chain whose first
        operand is a comparison.  Anything else is reported by the analyser
        as not portable and is skipped here.
        """
        comparison: Comparison | None = None
        if isinstance(condition, Comparison):
            comparison = condition
        elif isinstance(condition, LogicalOp) and condition.op == "and":
            for part in (condition.left, condition.right):
                if isinstance(part, Comparison):
                    comparison = part
                    break
        if comparison is None:
            return None
        if comparison.op != ">=":
            return None
        if not isinstance(comparison.right, Number):
            return None
        if not isinstance(comparison.left, Call):
            return None
        if not isinstance(comparison.left.func, Name):
            return None
        if comparison.left.func.identifier != "score":
            return None
        args = comparison.left.args
        if len(args) < 2:
            return None
        objective = _text(args[1])
        value = comparison.right.value
        return f"execute if score @s {objective} matches {value}.."

    def _emit_call(self, node: Node, out: list[str]) -> None:
        if not isinstance(node, Call):
            return
        if node.method or not isinstance(node.func, Name):
            return
        name = node.func.identifier

        if name == "onTick":
            self._emit_hook(node, self._tick, out)
            return
        if name == "onJoin":
            self._emit_hook(node, self._join, out)
            return
        if name == "onLoad":
            self._emit_hook(node, self._join, out)
            return
        if name == "onJavaOnly":
            self._emit_hook(node, out, out)
            return
        if name == "onBedrockOnly":
            # Dropped from the Java build.
            return

        command = self._command_for(name, node, out)
        if command:
            out.append(command)

    def _emit_hook(
        self, node: Call, body: list[str], out: list[str]
    ) -> None:
        """Emit a hook: its callback body goes to `body`, the hook to `out`."""
        callback = node.args[0] if node.args else None
        if not isinstance(callback, Function):
            return
        self._emit_statements(callback.body.body, body)

    def _emit_statements(self, statements: list[Node], out: list[str]) -> None:
        for statement in statements:
            self._emit_statement(statement, out)

    # -- command construction ---------------------------------------------

    def _command_for(
        self, name: str, node: Call, out: list[str]
    ) -> str:
        args = node.args

        def arg(index: int, default: str = "") -> str:
            """Fetch an argument, tolerating a short argument list.

            The analyser has already reported an unavailable capability, so
            the backend must not crash on the same source.
            """
            if index < len(args):
                return _text(args[index])
            return default

        def plain_arg(index: int, default: str = "") -> str:
            if index < len(args):
                return _text(args[index])
            return default

        if name == "say":
            return f"say {_json_text(arg(0))}"
        if name == "message":
            target = _selector(arg(0), self._inside_iteration, self._current_entity_name)
            return f"tellraw {target} {_json_text(arg(1))}"
        if name == "give":
            target = _selector(arg(0), self._inside_iteration, self._current_entity_name)
            item = self._item_reference(arg(1))
            rest = " ".join([item] + [_text(a) for a in args[2:]])
            return f"give {target} {rest}".rstrip()
        if name == "clear":
            target = _selector(arg(0), self._inside_iteration, self._current_entity_name)
            if len(args) > 1:
                item = self._item_reference(arg(1))
                rest = " ".join([item] + [_text(a) for a in args[2:]])
            else:
                rest = " ".join(_text(a) for a in args[1:])
            return f"clear {target} {rest}".rstrip()
        if name == "score":
            target = _selector(arg(0), self._inside_iteration, self._current_entity_name)
            objective = arg(1)
            if len(args) == 2:
                return f"scoreboard players get {target} {objective}"
            operation = arg(2, "set")
            value = arg(3, "1")
            return f"scoreboard players {operation} {target} {objective} {value}"
        if name == "addObjective":
            objective = arg(0)
            criteria = arg(1, "dummy")
            self._objectives.append((objective, criteria))
            return ""
        if name == "effect":
            target = _selector(arg(0), self._inside_iteration, self._current_entity_name)
            rest = " ".join(_text(a) for a in args[1:])
            return f"effect give {target} {rest}".rstrip()
        if name == "playSound":
            sound = arg(0)
            target = _selector(arg(1), self._inside_iteration, self._current_entity_name) if len(args) > 1 else "@a"
            rest = " ".join(_text(a) for a in args[2:])
            return f"playsound {sound} {target} {rest}".rstrip()
        if name == "teleport":
            target = _selector(arg(0), self._inside_iteration, self._current_entity_name)
            rest = " ".join(_text(a) for a in args[1:])
            return f"tp {target} {rest}".rstrip()
        if name == "runCommand":
            return arg(0)
        if name == "setDynamicProperty":
            # Bedrock only: emit a placeholder comment.
            return "# setDynamicProperty() has no Java equivalent"
        if name == "getDynamicProperty":
            return "# getDynamicProperty() has no Java equivalent"
        if name == "forEachPlayer":
            return self._emit_iteration(node, "@a", out)
        if name == "forEachEntity":
            entity_type = arg(0)
            return self._emit_iteration(
                node, f"@e[type={entity_type}]", out
            )
        return ""

    def _emit_iteration(
        self, node: Call, selector: str, out: list[str]
    ) -> str:
        """Emit `execute as <selector> run ...` for a callback.

        A callback with a single statement becomes one `execute` line.  A
        callback with several statements becomes its own function file that
        the `execute` calls, which keeps every generated line valid.
        """
        callback = node.args[-1] if node.args else None
        if not isinstance(callback, Function):
            return ""
        statements = callback.body.body
        if not statements:
            return ""

        saved = self._inside_iteration
        saved_binding = self._current_entity_name
        self._inside_iteration = True
        # Inside `execute as`, the current entity is addressed with `@s`, so
        # the callback parameter refers to the selector rather than to a
        # player named after the parameter.
        self._current_entity_name = callback.body.params[0] if callback.body.params else "self"
        try:
            if len(statements) == 1:
                single = self._command_for_statements(statements)
                if single:
                    return f"execute as {selector} run {single}"
                return ""

            # Several statements: emit a dedicated function.
            self._counter += 1
            function_name = f"_loop{self._counter}"
            body: list[str] = []
            self._emit_statements(statements, body)
        finally:
            self._inside_iteration = saved
            self._current_entity_name = saved_binding
        out.append(f"execute as {selector} run function {self.namespace}:{function_name}")
        self._pending_functions.append((function_name, body))
        return ""

    def _command_for_statements(self, statements: list[Node]) -> str:
        """Collapse a small statement list into a single command, if possible."""
        buffer: list[str] = []
        for statement in statements:
            if not isinstance(statement, ExprStat):
                return ""
            call = statement.expression
            if not isinstance(call, Call) or call.method:
                return ""
            if not isinstance(call.func, Name):
                return ""
            command = self._command_for(call.func.identifier, call, buffer)
            if not command:
                return ""
            buffer.append(command)
        return buffer[-1] if len(buffer) == 1 else ""


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _text(node: Node) -> str:
    """Render a Lua literal as the plain string Minecraft expects."""
    if isinstance(node, String):
        return node.value
    if isinstance(node, Number):
        return str(node.value)
    if isinstance(node, TrueLit):
        return "true"
    if isinstance(node, FalseLit):
        return "false"
    if isinstance(node, Name):
        return node.identifier
    if isinstance(node, Table):
        return ""
    return ""


def _selector(
    value: str,
    inside_iteration: bool = False,
    entity_name: str | None = None,
) -> str:
    """Turn a player reference into a Minecraft selector.

    Inside an iteration callback the current entity is `@s`.  Naming it
    explicitly would select nothing, so the callback parameter resolves to
    `@s` as well.
    """
    if inside_iteration:
        if entity_name is not None and value == entity_name:
            return "@s"
        if value in ("player", "self", "s", "target"):
            return "@s"
    if value.startswith("@"):
        return value
    return f"@{value}"


def _json_text(value: str) -> str:
    """Render a chat message as a text component."""
    escaped = (
        value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    )
    return f'{{"text":"{escaped}"}}'


def _pack_mcmeta(pack_format: int) -> str:
    return (
        "{\n"
        '  "pack": {\n'
        f'    "pack_format": {pack_format},\n'
        '    "description": "Generated by LuaCraft",\n'
        '    "supported_formats": {"min_inclusive": 15, "max_inclusive": 999}\n'
        "  }\n"
        "}\n"
    )


def _tag_json(functions: Iterable[str]) -> str:
    values = ",\n    ".join(f'"{name}"' for name in functions)
    return "{\n  \"values\": [\n    " + values + "\n  ]\n}\n"


def _join_lines(lines: list[str]) -> str:
    if not lines:
        return "\n"
    return "\n".join(lines) + "\n"
