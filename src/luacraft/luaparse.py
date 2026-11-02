"""A small, dependency free Lua parser.

LuaCraft needs to understand Lua well enough to compile it to two very
different targets, and to point at the exact source line when something cannot
be expressed.  Using only the Python standard library keeps the tool installable
offline with no wheels to download.

The parser covers Lua 5.4 syntax:

* values: ``nil``, ``true``, ``false``, numbers, strings, varargs, functions,
  table constructors
* operators: the full precedence table including ``//``, ``~``, ``&``, ``|``,
  ``<<``, ``>>`` and the string coercion operator ``..``
* statements: assignment, local declaration, function call, ``if``/``elseif``/
  ``else``, ``while``, ``repeat``, numeric ``for``, generic ``for``,
  ``function`` declarations, local function declarations, ``return``,
  ``break``, ``goto``/``::label::`` and ``do`` blocks
* comments: ``--`` line and ``--[[ ]]`` block

It is not a full Lua implementation: metatables are parsed as ordinary table
fields, and ``load``/``loadstring`` are not special cased.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator


class LuaSyntaxError(Exception):
    """Raised when the source cannot be parsed."""

    def __init__(self, message: str, line: int, snippet: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.line = line
        self.snippet = snippet

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"line {self.line}: {self.message}"


# --------------------------------------------------------------------------
# AST nodes
# --------------------------------------------------------------------------


@dataclass
class Node:
    """Base class for every AST node."""

    line: int = field(default=0, kw_only=True)


@dataclass
class Nil(Node):
    pass


@dataclass
class TrueLit(Node):
    pass


@dataclass
class FalseLit(Node):
    pass


@dataclass
class Vararg(Node):
    pass


@dataclass
class Number(Node):
    value: float | int
    is_integer: bool = True


@dataclass
class String(Node):
    value: str


@dataclass
class Name(Node):
    identifier: str


@dataclass
class Index(Node):
    """``obj[key]`` or ``obj.key`` (both share this node)."""

    obj: Node
    key: Node
    #: True when written with a dot rather than brackets.
    dot: bool = False


@dataclass
class Call(Node):
    """A function call, used as a statement or as an expression."""

    func: Node
    args: list[Node]
    #: Method call form ``obj:name(args)``.
    method: str = ""


@dataclass
class FunctionBody(Node):
    params: list[str]
    is_vararg: bool
    body: list[Node]


@dataclass
class Function(Node):
    body: FunctionBody


@dataclass
class Table(Node):
    """A table constructor.

    ``array_part`` holds the positional values, ``hash_part`` the key/value
    pairs and ``pairs`` the mixed ``key = value`` entries.
    """

    array_part: list[Node] = field(default_factory=list)
    hash_part: list[tuple[Node, Node]] = field(default_factory=list)


@dataclass
class BinOp(Node):
    op: str
    left: Node
    right: Node


@dataclass
class UnOp(Node):
    op: str
    operand: Node


@dataclass
class LogicalOp(Node):
    op: str
    left: Node
    right: Node


@dataclass
class Comparison(Node):
    op: str
    left: Node
    right: Node


@dataclass
class Assign(Node):
    targets: list[Node]
    values: list[Node]


@dataclass
class LocalAssign(Node):
    names: list[str]
    values: list[Node]


@dataclass
class LocalFunction(Node):
    name: str
    body: FunctionBody


@dataclass
class If(Node):
    branches: list[tuple[Node, list[Node]]]
    else_body: list[Node] | None = None


@dataclass
class While(Node):
    condition: Node
    body: list[Node]


@dataclass
class Repeat(Node):
    body: list[Node]
    condition: Node


@dataclass
class NumericFor(Node):
    name: str
    start: Node
    stop: Node
    step: Node | None
    body: list[Node]


@dataclass
class GenericFor(Node):
    names: list[str]
    exprs: list[Node]
    body: list[Node]


@dataclass
class DoBlock(Node):
    body: list[Node]


@dataclass
class Return(Node):
    values: list[Node]


@dataclass
class Break(Node):
    pass


@dataclass
class Goto(Node):
    label: str


@dataclass
class Label(Node):
    name: str


@dataclass
class ExprStat(Node):
    """A statement that is just an expression, normally a call."""

    expression: Node


@dataclass
class Chunk(Node):
    body: list[Node]


# --------------------------------------------------------------------------
# Lexer
# --------------------------------------------------------------------------

KEYWORDS = frozenset(
    {
        "and", "break", "do", "else", "elseif", "end", "false", "for", "function",
        "goto", "if", "in", "local", "nil", "not", "or", "repeat", "return",
        "then", "true", "until", "while",
    }
)

# Longest first so that ``...`` is not mistaken for ``..``.
OPERATORS = (
    "...", "..", "==", "~=", "<=", ">=", "//", "<<", ">>", "::",
    "+", "-", "*", "/", "%", "^", "#", "<", ">", "=", "(", ")", "{", "}",
    "[", "]", ";", ":", ",", ".", "&", "|", "~",
)

ESCAPES = {
    "a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t",
    "v": "\v", "\\": "\\", '"': '"', "'": "'", "\n": "\n",
}


@dataclass
class Token:
    kind: str  # "name" | "number" | "string" | "op" | "keyword" | "eof"
    value: Any
    line: int


def _is_name_start(ch: str) -> bool:
    return ch.isalpha() or ch == "_"


def _is_name_char(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


class Lexer:
    """Turns Lua source into a token list."""

    def __init__(self, source: str) -> None:
        self.source = source
        self.pos = 0
        self.line = 1
        self.tokens: list[Token] = []

    def error(self, message: str) -> LuaSyntaxError:
        return LuaSyntaxError(message, self.line, self._current_line())

    def _current_line(self) -> str:
        start = self.source.rfind("\n", 0, self.pos) + 1
        end = self.source.find("\n", self.pos)
        if end == -1:
            end = len(self.source)
        return self.source[start:end].strip()

    def _peek(self, offset: int = 0) -> str:
        idx = self.pos + offset
        if idx >= len(self.source):
            return ""
        return self.source[idx]

    def _peek_in(self, characters: str, offset: int = 0) -> bool:
        """Check whether the next character belongs to a set.

        Unlike ``self._peek() in characters`` this is safe at the end of the
        source, because the empty string is a substring of every string in
        Python and would otherwise always match.
        """
        ch = self._peek(offset)
        return ch != "" and ch in characters

    def _advance(self) -> str:
        ch = self.source[self.pos]
        self.pos += 1
        if ch == "\n":
            self.line += 1
        return ch

    def _skip_whitespace_and_comments(self) -> None:
        while self.pos < len(self.source):
            ch = self._peek()
            if ch in " \t\r\n":
                self._advance()
                continue
            if ch == "-" and self._peek(1) == "-":
                self._skip_comment()
                continue
            break

    def _skip_comment(self) -> None:
        # Consume the two dashes.
        self._advance()
        self._advance()
        # A long comment starts with a long bracket: --[[ ... ]] or --[==[ ... ]==]
        if self._peek() == "[":
            level = 0
            probe = self.pos + 1
            while probe < len(self.source) and self.source[probe] == "=":
                level += 1
                probe += 1
            if probe < len(self.source) and self.source[probe] == "[":
                self.pos = probe + 1
                self._read_long_string(level)
                return
        # Line comment.
        while self.pos < len(self.source) and self._peek() != "\n":
            self._advance()

    def _read_long_string(self, level: int) -> str:
        # self.pos points just after the opening [==[
        buf: list[str] = []
        closing = "]" + "=" * level + "]"
        while True:
            if self.pos >= len(self.source):
                raise self.error("unfinished long string")
            if self.source.startswith(closing, self.pos):
                for _ in closing:
                    self._advance()
                return "".join(buf)
            buf.append(self._advance())

    def _read_long_bracket(self) -> str:
        # At '['; check for [=*[
        level = 0
        probe = self.pos + 1
        while probe < len(self.source) and self.source[probe] == "=":
            level += 1
            probe += 1
        if probe < len(self.source) and self.source[probe] == "[":
            self.pos = probe + 1
            return self._read_long_string(level)
        return ""

    def _read_short_string(self, quote: str) -> str:
        self._advance()  # opening quote
        buf: list[str] = []
        while True:
            if self.pos >= len(self.source):
                raise self.error("unfinished string")
            ch = self._advance()
            if ch == quote:
                return "".join(buf)
            if ch == "\\":
                esc = self._advance()
                if esc in ESCAPES:
                    buf.append(ESCAPES[esc])
                elif esc == "x":
                    hex_digits = ""
                    while len(hex_digits) < 2 and self._peek_in("0123456789abcdefABCDEF"):
                        hex_digits += self._advance()
                    if not hex_digits:
                        raise self.error("hexadecimal digit expected")
                    buf.append(chr(int(hex_digits, 16)))
                elif esc == "z":
                    while self._peek_in(" \t\r\n"):
                        self._advance()
                elif esc.isdigit():
                    digits = esc
                    while len(digits) < 3 and self._peek().isdigit():
                        digits += self._advance()
                    buf.append(chr(int(digits)))
                else:
                    buf.append(esc)
            else:
                buf.append(ch)

    def _read_number(self) -> Token:
        start = self.pos
        is_float = False
        if self._peek() == "0" and self._peek(1) in ("x", "X"):
            self._advance()
            self._advance()
            while self._peek_in("0123456789abcdefABCDEF"):
                self._advance()
            text = self.source[start : self.pos]
            return Token("number", int(text, 16), self.line)
        while self._peek_in("0123456789"):
            self._advance()
        if self._peek() == ".":
            is_float = True
            self._advance()
            while self._peek_in("0123456789"):
                self._advance()
        if self._peek_in("eE"):
            is_float = True
            self._advance()
            if self._peek_in("+-"):
                self._advance()
            while self._peek_in("0123456789"):
                self._advance()
        text = self.source[start : self.pos]
        value: float | int
        if is_float:
            value = float(text)
        else:
            value = int(text)
        return Token("number", value, self.line)

    def tokenize(self) -> list[Token]:
        while True:
            self._skip_whitespace_and_comments()
            if self.pos >= len(self.source):
                break
            ch = self._peek()
            line = self.line
            if _is_name_start(ch):
                start = self.pos
                while self.pos < len(self.source) and _is_name_char(self._peek()):
                    self._advance()
                word = self.source[start : self.pos]
                kind = "keyword" if word in KEYWORDS else "name"
                self.tokens.append(Token(kind, word, line))
                continue
            if ch.isdigit() or (
                ch == "." and self._peek(1).isdigit()
            ):
                self.tokens.append(self._read_number())
                continue
            if ch in "'\"":
                self.tokens.append(Token("string", self._read_short_string(ch), line))
                continue
            if ch == "[":
                probe = self.pos + 1
                level = 0
                while probe < len(self.source) and self.source[probe] == "=":
                    level += 1
                    probe += 1
                if probe < len(self.source) and self.source[probe] == "[":
                    self.pos = probe + 1
                    self.tokens.append(Token("string", self._read_long_string(level), line))
                    continue
            for op in OPERATORS:
                if self.source.startswith(op, self.pos):
                    for _ in op:
                        self._advance()
                    self.tokens.append(Token("op", op, line))
                    break
            else:
                raise self.error(f"unexpected character {ch!r}")
        self.tokens.append(Token("eof", None, self.line))
        return self.tokens


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------

# Binary operator precedence, higher binds tighter.
BINARY_PRECEDENCE: dict[str, int] = {
    "or": 1,
    "and": 2,
    "<": 3, ">": 3, "<=": 3, ">=": 3, "~=": 3, "==": 3,
    "|": 4,
    "~": 5,
    "&": 6,
    "<<": 7, ">>": 7,
    "..": 9,  # right associative
    "+": 10, "-": 10,
    "*": 11, "/": 11, "//": 11, "%": 11,
    "^": 14,  # right associative
}

RIGHT_ASSOCIATIVE = {"..", "^"}
UNARY_PRECEDENCE = 12


class Parser:
    """Recursive descent parser producing the AST above."""

    def __init__(self, source: str) -> None:
        self.source = source
        self.tokens = Lexer(source).tokenize()
        self.index = 0

    # -- token helpers ----------------------------------------------------

    @property
    def current(self) -> Token:
        return self.tokens[self.index]

    def peek(self, offset: int = 1) -> Token:
        idx = min(self.index + offset, len(self.tokens) - 1)
        return self.tokens[idx]

    def advance(self) -> Token:
        token = self.tokens[self.index]
        if token.kind != "eof":
            self.index += 1
        return token

    def check(self, kind: str, value: Any = None) -> bool:
        token = self.current
        if token.kind != kind:
            return False
        return value is None or token.value == value

    def accept(self, kind: str, value: Any = None) -> Token | None:
        if self.check(kind, value):
            return self.advance()
        return None

    def expect(self, kind: str, value: Any = None) -> Token:
        token = self.accept(kind, value)
        if token is None:
            wanted = value if value is not None else kind
            got = self.current.value
            raise LuaSyntaxError(
                f"expected {wanted!r} but found {got!r}", self.current.line
            )
        return token

    def error(self, message: str, line: int | None = None) -> LuaSyntaxError:
        return LuaSyntaxError(message, line or self.current.line)

    # -- entry point ------------------------------------------------------

    def parse(self) -> Chunk:
        body = self.parse_block()
        if self.current.kind != "eof":
            raise self.error(f"unexpected {self.current.value!r}")
        return Chunk(body=body, line=1)

    def parse_block(self) -> list[Node]:
        statements: list[Node] = []
        while True:
            token = self.current
            if token.kind == "eof":
                break
            if token.kind == "keyword" and token.value == "end":
                break
            if token.kind == "keyword" and token.value == "else":
                break
            if token.kind == "keyword" and token.value == "elseif":
                break
            if token.kind == "keyword" and token.value == "until":
                break
            statement = self.parse_statement()
            if statement is not None:
                statements.append(statement)
        return statements

    # -- statements -------------------------------------------------------

    def parse_statement(self) -> Node | None:
        token = self.current
        line = token.line

        if token.kind == "op" and token.value == ";":
            self.advance()
            return None

        if token.kind == "op" and token.value == "::":
            self.advance()
            name = self.expect("name").value
            self.expect("op", "::")
            return Label(name=name, line=line)

        if token.kind == "keyword":
            keyword = token.value
            if keyword == "local":
                return self.parse_local()
            if keyword == "if":
                return self.parse_if()
            if keyword == "while":
                return self.parse_while()
            if keyword == "repeat":
                return self.parse_repeat()
            if keyword == "for":
                return self.parse_for()
            if keyword == "function":
                return self.parse_function_statement()
            if keyword == "do":
                self.advance()
                body = self.parse_block()
                self.expect("keyword", "end")
                return DoBlock(body=body, line=line)
            if keyword == "return":
                self.advance()
                values: list[Node] = []
                if not self._block_ends() and not self.check("op", ";"):
                    values.append(self.parse_expression())
                    while self.accept("op", ","):
                        values.append(self.parse_expression())
                self.accept("op", ";")
                return Return(values=values, line=line)
            if keyword == "break":
                self.advance()
                return Break(line=line)
            if keyword == "goto":
                self.advance()
                name = self.expect("name").value
                return Goto(label=name, line=line)

        # Either an assignment or a call statement.
        expr = self.parse_suffixed_expression()
        if self.check("op", "=") or self.check("op", ","):
            targets = [expr]
            while self.accept("op", ","):
                targets.append(self.parse_suffixed_expression())
            self.expect("op", "=")
            values = [self.parse_expression()]
            while self.accept("op", ","):
                values.append(self.parse_expression())
            for target in targets:
                if not isinstance(target, (Name, Index)):
                    raise self.error("cannot assign to this expression", line)
            return Assign(targets=targets, values=values, line=line)

        if not isinstance(expr, Call):
            raise self.error("syntax error near an expression that is not a call", line)
        return ExprStat(expression=expr, line=line)

    def _block_ends(self) -> bool:
        token = self.current
        if token.kind == "eof":
            return True
        return token.kind == "keyword" and token.value in {
            "end", "else", "elseif", "until",
        }

    def parse_local(self) -> Node:
        line = self.current.line
        self.advance()  # local
        if self.accept("keyword", "function"):
            name = self.expect("name").value
            body = self.parse_function_body()
            return LocalFunction(name=name, body=body, line=line)
        names = [self.expect("name").value]
        # Attribute syntax: local x <const> / <close>
        while self.accept("op", "<"):
            self.expect("name")
            self.expect("op", ">")
        values: list[Node] = []
        if self.accept("op", "="):
            values.append(self.parse_expression())
            # A comma may introduce another name (`local a = 1, b = 2`) or
            # another value (`local a, b = 1, 2`).  A name followed by `=`
            # starts a new declaration, so the loop stops there.
            while self.check("op", ",") and self._next_is_value():
                self.advance()
                values.append(self.parse_expression())
        return LocalAssign(names=names, values=values, line=line)

    def _next_is_value(self) -> bool:
        """Whether the token after the current comma starts an expression.

        A name followed by `=` introduces another local declaration rather
        than another value, so it does not count as a value here.
        """
        token = self.peek()
        if token.kind == "name":
            # `name =` means a new declaration, not a value.
            return not (self.peek(2).kind == "op" and self.peek(2).value == "=")
        if token.kind in ("number", "string"):
            return True
        if token.kind == "keyword" and token.value in (
            "nil", "true", "false", "function",
        ):
            return True
        if token.kind == "op" and token.value in ("{", "(", "..."):
            return True
        return False

    def parse_if(self) -> Node:
        line = self.current.line
        self.advance()  # if
        branches: list[tuple[Node, list[Node]]] = []
        condition = self.parse_expression()
        self.expect("keyword", "then")
        body = self.parse_block()
        branches.append((condition, body))
        else_body: list[Node] | None = None
        while self.check("keyword", "elseif"):
            self.advance()
            cond = self.parse_expression()
            self.expect("keyword", "then")
            sub_body = self.parse_block()
            branches.append((cond, sub_body))
        if self.accept("keyword", "else"):
            else_body = self.parse_block()
        self.expect("keyword", "end")
        return If(branches=branches, else_body=else_body, line=line)

    def parse_while(self) -> Node:
        line = self.current.line
        self.advance()  # while
        condition = self.parse_expression()
        self.expect("keyword", "do")
        body = self.parse_block()
        self.expect("keyword", "end")
        return While(condition=condition, body=body, line=line)

    def parse_repeat(self) -> Node:
        line = self.current.line
        self.advance()  # repeat
        body = self.parse_block()
        self.expect("keyword", "until")
        condition = self.parse_expression()
        return Repeat(body=body, condition=condition, line=line)

    def parse_for(self) -> Node:
        line = self.current.line
        self.advance()  # for
        first = self.expect("name").value
        if self.accept("op", "="):
            start = self.parse_expression()
            self.expect("op", ",")
            stop = self.parse_expression()
            step: Node | None = None
            if self.accept("op", ","):
                step = self.parse_expression()
            self.expect("keyword", "do")
            body = self.parse_block()
            self.expect("keyword", "end")
            return NumericFor(
                name=first, start=start, stop=stop, step=step, body=body, line=line
            )
        names = [first]
        while self.accept("op", ","):
            names.append(self.expect("name").value)
        self.expect("keyword", "in")
        exprs = [self.parse_expression()]
        while self.accept("op", ","):
            exprs.append(self.parse_expression())
        self.expect("keyword", "do")
        body = self.parse_block()
        self.expect("keyword", "end")
        return GenericFor(names=names, exprs=exprs, body=body, line=line)

    def parse_function_statement(self) -> Node:
        line = self.current.line
        self.advance()  # function
        name = self.expect("name").value
        target: Node = Name(identifier=name, line=line)
        is_method = False
        while True:
            if self.accept("op", "."):
                key = self.expect("name").value
                target = Index(obj=target, key=String(value=key, line=line), dot=True, line=line)
                continue
            if self.accept("op", ":"):
                key = self.expect("name").value
                target = Index(obj=target, key=String(value=key, line=line), dot=True, line=line)
                is_method = True
                break
            break
        body = self.parse_function_body(is_method=is_method)
        if not isinstance(target, Name):
            raise self.error("cannot assign a function to this expression", line)
        return Assign(
            targets=[target], values=[Function(body=body, line=line)], line=line
        )

    def parse_function_body(self, is_method: bool = False) -> FunctionBody:
        line = self.current.line
        self.expect("op", "(")
        params: list[str] = []
        is_vararg = False
        if is_method:
            params.append("self")
        if not self.check("op", ")"):
            while True:
                if self.accept("op", "..."):
                    is_vararg = True
                    break
                params.append(self.expect("name").value)
                if not self.accept("op", ","):
                    break
        self.expect("op", ")")
        body = self.parse_block()
        self.expect("keyword", "end")
        return FunctionBody(params=params, is_vararg=is_vararg, body=body, line=line)

    # -- expressions ------------------------------------------------------

    def parse_expression(self) -> Node:
        return self.parse_subexpression(0)

    def parse_subexpression(self, limit: int) -> Node:
        token = self.current
        line = token.line

        if (token.kind == "keyword" and token.value == "not") or (
            token.kind == "op" and token.value in ("-", "#", "~")
        ):
            op = self.advance().value
            operand = self.parse_subexpression(UNARY_PRECEDENCE)
            left: Node = UnOp(op=op, operand=operand, line=line)
        else:
            left = self.parse_simple_expression()

        while True:
            token = self.current
            op: str | None = None
            if token.kind == "op" and token.value in BINARY_PRECEDENCE:
                op = token.value
            elif token.kind == "keyword" and token.value in ("and", "or"):
                op = token.value
            if op is None:
                break
            precedence = BINARY_PRECEDENCE[op]
            if precedence <= limit:
                break
            self.advance()
            right = self.parse_subexpression(
                precedence - 1 if op in RIGHT_ASSOCIATIVE else precedence
            )
            if op == "and" or op == "or":
                left = LogicalOp(op=op, left=left, right=right, line=line)
            elif op in ("<", ">", "<=", ">=", "~=", "=="):
                left = Comparison(op=op, left=left, right=right, line=line)
            else:
                left = BinOp(op=op, left=left, right=right, line=line)
        return left

    def parse_simple_expression(self) -> Node:
        token = self.current
        line = token.line
        if token.kind == "number":
            self.advance()
            return Number(
                value=token.value, is_integer=isinstance(token.value, int), line=line
            )
        if token.kind == "string":
            self.advance()
            return String(value=token.value, line=line)
        if token.kind == "keyword":
            if token.value == "nil":
                self.advance()
                return Nil(line=line)
            if token.value == "true":
                self.advance()
                return TrueLit(line=line)
            if token.value == "false":
                self.advance()
                return FalseLit(line=line)
            if token.value == "function":
                self.advance()
                body = self.parse_function_body()
                return Function(body=body, line=line)
        if token.kind == "op" and token.value == "...":
            self.advance()
            return Vararg(line=line)
        if token.kind == "op" and token.value == "{":
            return self.parse_table()
        return self.parse_suffixed_expression()

    def parse_primary_expression(self) -> Node:
        token = self.current
        line = token.line
        if token.kind == "name":
            self.advance()
            return Name(identifier=token.value, line=line)
        if token.kind == "op" and token.value == "(":
            self.advance()
            inner = self.parse_expression()
            self.expect("op", ")")
            return inner
        if token.kind == "op" and token.value == "{":
            return self.parse_table()
        raise self.error(f"unexpected {token.value!r}", line)

    def parse_suffixed_expression(self) -> Node:
        expr = self.parse_primary_expression()
        while True:
            token = self.current
            line = token.line
            if token.kind == "op" and token.value == ".":
                self.advance()
                key = self.expect("name").value
                expr = Index(obj=expr, key=String(value=key, line=line), dot=True, line=line)
                continue
            if token.kind == "op" and token.value == "[":
                self.advance()
                key = self.parse_expression()
                self.expect("op", "]")
                expr = Index(obj=expr, key=key, dot=False, line=line)
                continue
            if token.kind == "op" and token.value == ":":
                self.advance()
                method = self.expect("name").value
                args = self.parse_call_args()
                expr = Call(func=expr, args=args, method=method, line=line)
                continue
            if token.kind == "op" and token.value in ("(", "{"):
                args = self.parse_call_args()
                expr = Call(func=expr, args=args, line=line)
                continue
            if token.kind == "string":
                args = self.parse_call_args()
                expr = Call(func=expr, args=args, line=line)
                continue
            break
        return expr

    def parse_call_args(self) -> list[Node]:
        token = self.current
        if token.kind == "string":
            self.advance()
            return [String(value=token.value, line=token.line)]
        if token.kind == "op" and token.value == "{":
            return [self.parse_table()]
        self.expect("op", "(")
        args: list[Node] = []
        if not self.check("op", ")"):
            args.append(self.parse_expression())
            while self.accept("op", ","):
                args.append(self.parse_expression())
        self.expect("op", ")")
        return args

    def parse_table(self) -> Node:
        line = self.current.line
        self.expect("op", "{")
        array_part: list[Node] = []
        hash_part: list[tuple[Node, Node]] = []
        while not self.check("op", "}"):
            if self.check("op", "["):
                self.advance()
                key = self.parse_expression()
                self.expect("op", "]")
                self.expect("op", "=")
                value = self.parse_expression()
                hash_part.append((key, value))
            elif (
                self.current.kind == "name"
                and self.peek().kind == "op"
                and self.peek().value == "="
            ):
                key_name = self.advance().value
                self.advance()  # =
                value = self.parse_expression()
                hash_part.append((String(value=key_name, line=line), value))
            else:
                array_part.append(self.parse_expression())
            if not (self.accept("op", ",") or self.accept("op", ";")):
                break
        self.expect("op", "}")
        return Table(array_part=array_part, hash_part=hash_part, line=line)


def parse(source: str) -> Chunk:
    """Parse Lua source into an AST."""
    return Parser(source).parse()


def walk(node: Node) -> Iterator[Node]:
    """Yield every node in the tree, parents before children.

    Each node is yielded exactly once, even when it is reachable through
    more than one path.
    """
    seen: set[int] = set()
    stack: list[Node] = [node]
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        # Push in reverse so children are visited in source order.
        stack.extend(reversed(children(current)))


def children(node: Node) -> list[Node]:
    """Return the direct children of a node."""
    out: list[Node] = []
    if isinstance(node, Chunk):
        out.extend(node.body)
    elif isinstance(node, Index):
        out.extend([node.obj, node.key])
    elif isinstance(node, Call):
        out.append(node.func)
        out.extend(node.args)
    elif isinstance(node, Function):
        out.append(node.body)
        out.extend(node.body.body)
    elif isinstance(node, FunctionBody):
        out.extend(node.body)
    elif isinstance(node, Table):
        out.extend(node.array_part)
        for key, value in node.hash_part:
            out.extend([key, value])
    elif isinstance(node, BinOp):
        out.extend([node.left, node.right])
    elif isinstance(node, UnOp):
        out.append(node.operand)
    elif isinstance(node, LogicalOp):
        out.extend([node.left, node.right])
    elif isinstance(node, Comparison):
        out.extend([node.left, node.right])
    elif isinstance(node, Assign):
        out.extend(node.targets)
        out.extend(node.values)
    elif isinstance(node, LocalAssign):
        out.extend(node.values)
    elif isinstance(node, LocalFunction):
        out.append(node.body)
        out.extend(node.body.body)
    elif isinstance(node, If):
        for condition, body in node.branches:
            out.append(condition)
            out.extend(body)
        if node.else_body:
            out.extend(node.else_body)
    elif isinstance(node, While):
        out.append(node.condition)
        out.extend(node.body)
    elif isinstance(node, Repeat):
        out.extend(node.body)
        out.append(node.condition)
    elif isinstance(node, NumericFor):
        out.extend([node.start, node.stop])
        if node.step:
            out.append(node.step)
        out.extend(node.body)
    elif isinstance(node, GenericFor):
        out.extend(node.exprs)
        out.extend(node.body)
    elif isinstance(node, DoBlock):
        out.extend(node.body)
    elif isinstance(node, Return):
        out.extend(node.values)
    elif isinstance(node, ExprStat):
        out.append(node.expression)
    return out
