"""Tests for the Lua parser."""

from __future__ import annotations

import pytest

from luacraft.luaparse import (
    Assign,
    Call,
    Chunk,
    Comparison,
    ExprStat,
    Function,
    If,
    LocalAssign,
    LuaSyntaxError,
    Name,
    Number,
    String,
    Table,
    parse,
    walk,
)


def test_parses_empty_source() -> None:
    chunk = parse("")
    assert isinstance(chunk, Chunk)
    assert chunk.body == []


def test_rejects_a_bare_literal_statement() -> None:
    # In Lua a statement must be a call or an assignment, so a bare literal
    # is a syntax error.
    with pytest.raises(LuaSyntaxError):
        parse('"hola"')


def test_parses_numbers() -> None:
    chunk = parse("local a = 1 local b = 2.5 local c = 0xFF")
    values = [
        node.value
        for node in walk(chunk)
        if isinstance(node, Number)
    ]
    assert values == [1, 2.5, 255]


def test_parses_a_table_constructor() -> None:
    chunk = parse("local t = { a = 1, 2, [3] = 4 }")
    statement = chunk.body[0]
    assert isinstance(statement, LocalAssign)
    table = statement.values[0]
    assert isinstance(table, Table)
    assert len(table.hash_part) == 2
    assert len(table.array_part) == 1


def test_parses_a_table_as_a_call_argument() -> None:
    chunk = parse('give(player, { count = 1 })')
    statement = chunk.body[0]
    call = statement.expression
    assert isinstance(call, Call)
    assert isinstance(call.args[1], Table)


def test_parses_a_function_call() -> None:
    chunk = parse('say("hola", 1)')
    statement = chunk.body[0]
    assert isinstance(statement, ExprStat)
    call = statement.expression
    assert isinstance(call, Call)
    assert isinstance(call.func, Name)
    assert call.func.identifier == "say"
    assert len(call.args) == 2


def test_parses_local_declaration() -> None:
    chunk = parse("local x = 1")
    statement = chunk.body[0]
    assert isinstance(statement, LocalAssign)
    assert statement.names == ["x"]


def test_parses_an_if_statement() -> None:
    chunk = parse('if x >= 1 then say("a") end')
    statement = chunk.body[0]
    assert isinstance(statement, If)
    assert len(statement.branches) == 1
    condition = statement.branches[0][0]
    assert isinstance(condition, Comparison)
    assert condition.op == ">="


def test_parses_an_else_branch() -> None:
    chunk = parse('if x then say("a") else say("b") end')
    statement = chunk.body[0]
    assert isinstance(statement, If)
    assert statement.else_body is not None
    assert len(statement.else_body) == 1


def test_parses_an_anonymous_function() -> None:
    chunk = parse('forEachPlayer(function(p) say("a") end)')
    statement = chunk.body[0]
    call = statement.expression
    assert isinstance(call, Call)
    function = call.args[0]
    assert isinstance(function, Function)
    assert function.body.params == ["p"]


def test_parses_a_nested_callback() -> None:
    chunk = parse(
        """
        onTick(function()
          forEachPlayer(function(p)
            if score(p, "x") >= 10 then
              give(p, "minecraft:diamond", 1)
            end
          end)
        end)
        """
    )
    ifs = [node for node in walk(chunk) if isinstance(node, If)]
    assert len(ifs) == 1
    gives = [
        node
        for node in walk(chunk)
        if isinstance(node, Call)
        and isinstance(node.func, Name)
        and node.func.identifier == "give"
    ]
    assert len(gives) == 1


def test_walk_visits_each_node_once() -> None:
    chunk = parse('forEachPlayer(function(p) say("a") end)')
    nodes = list(walk(chunk))
    assert len(nodes) == len({id(node) for node in nodes})


def test_comments_are_ignored() -> None:
    chunk = parse("-- a comment\nsay(\"hola\") -- another\n")
    assert len(chunk.body) == 1


def test_long_comments_are_ignored() -> None:
    chunk = parse("--[[ un\ncomentario ]]\nsay(\"hola\")")
    assert len(chunk.body) == 1


def test_raises_on_unfinished_string() -> None:
    with pytest.raises(LuaSyntaxError) as excinfo:
        parse('say("hola')
    assert excinfo.value.line == 1


def test_raises_on_unexpected_token() -> None:
    with pytest.raises(LuaSyntaxError):
        parse("say(")


def test_raises_on_missing_end() -> None:
    with pytest.raises(LuaSyntaxError):
        parse("if x then say(\"a\")")


def test_line_numbers_are_tracked() -> None:
    chunk = parse('say("a")\nsay("b")\nsay("c")')
    calls = [
        node
        for node in walk(chunk)
        if isinstance(node, Call) and isinstance(node.func, Name)
    ]
    assert [call.line for call in calls] == [1, 2, 3]


def test_parses_string_concatenation() -> None:
    chunk = parse('say("a" .. "b")')
    assert len(chunk.body) == 1
