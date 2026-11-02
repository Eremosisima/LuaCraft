"""Tests for the capability analysis and the warnings it produces."""

from __future__ import annotations

from luacraft.analyser import analyse
from luacraft.api import Capability, Edition, PRIMITIVES, get_primitive
from luacraft.diagnostics import Category, DiagnosticBag, Severity
from luacraft.luaparse import parse


def _diagnose(source: str, edition: Edition) -> DiagnosticBag:
    bag = DiagnosticBag()
    analyse(parse(source), edition, bag)
    return bag


def test_a_portable_primitive_produces_no_diagnostics() -> None:
    bag = _diagnose('say("hola")', Edition.JAVA)
    assert len(bag) == 0


def test_a_bedrock_only_primitive_is_an_error_in_java() -> None:
    bag = _diagnose('setDynamicProperty(player, "k", 1)', Edition.JAVA)
    assert len(bag) == 1
    diagnostic = bag.errors[0]
    assert diagnostic.category is Category.JAVA_UNAVAILABLE
    assert diagnostic.severity is Severity.ERROR
    assert "Java Edition" in diagnostic.message


def test_a_java_only_primitive_is_an_error_in_bedrock() -> None:
    bag = _diagnose('nbt(player, "path", 1)', Edition.BEDROCK)
    assert len(bag) == 1
    diagnostic = bag.errors[0]
    assert diagnostic.category is Category.BEDROCK_UNAVAILABLE
    assert "Bedrock Edition" in diagnostic.message


def test_a_partially_supported_primitive_is_a_warning() -> None:
    bag = _diagnose('setblock(0, 64, 0, "minecraft:stone")', Edition.BEDROCK)
    assert len(bag) == 1
    diagnostic = bag.warnings[0]
    assert diagnostic.category is Category.PARTIAL_SUPPORT
    assert diagnostic.severity is Severity.WARNING


def test_a_partially_supported_primitive_is_silent_in_the_other_edition() -> None:
    bag = _diagnose('setblock(0, 64, 0, "minecraft:stone")', Edition.JAVA)
    assert len(bag) == 0


def test_a_target_hook_warns_in_the_other_edition() -> None:
    bag = _diagnose('onJavaOnly(function() say("a") end)', Edition.BEDROCK)
    assert len(bag) == 1
    diagnostic = bag.warnings[0]
    assert diagnostic.category is Category.UNSUPPORTED_TARGET
    assert "Java Edition only hook" in diagnostic.message


def test_a_target_hook_is_silent_in_its_own_edition() -> None:
    bag = _diagnose('onJavaOnly(function() say("a") end)', Edition.JAVA)
    assert len(bag) == 0


def test_diagnostics_point_at_the_right_line() -> None:
    source = 'say("a")\nsay("b")\nsetDynamicProperty(player, "k", 1)'
    bag = _diagnose(source, Edition.JAVA)
    assert bag.errors[0].line == 3


def test_primitives_inside_callbacks_are_detected() -> None:
    source = """
    onTick(function()
      setDynamicProperty(player, "k", 1)
    end)
    """
    bag = _diagnose(source, Edition.JAVA)
    assert len(bag) == 1
    assert bag.errors[0].line == 3


def test_the_summary_lists_the_primitives_used() -> None:
    info = analyse(parse('say("a")\ngive(player, "minecraft:diamond", 1)'), Edition.JAVA)
    assert info.primitives_used == ["say", "give"]


def test_the_summary_flags_scoreboard_use() -> None:
    info = analyse(parse('score(player, "coins", "add", 1)'), Edition.JAVA)
    assert info.uses_scoreboard is True


def test_every_primitive_declares_both_editions() -> None:
    for primitive in PRIMITIVES:
        assert primitive.capability(Edition.JAVA) in set(Capability)
        assert primitive.capability(Edition.BEDROCK) in set(Capability)


def test_every_partial_primitive_explains_itself() -> None:
    for primitive in PRIMITIVES:
        for edition in Edition:
            if primitive.capability(edition) is Capability.PARTIAL:
                assert primitive.notes.get(edition), (
                    f"{primitive.name} is partial in {edition} but has no note"
                )


def test_every_none_primitive_explains_itself() -> None:
    for primitive in PRIMITIVES:
        for edition in Edition:
            if primitive.capability(edition) is Capability.NONE:
                assert primitive.notes.get(edition), (
                    f"{primitive.name} is missing in {edition} but has no note"
                )


def test_portable_primitives_are_exactly_the_fully_supported_ones() -> None:
    for primitive in PRIMITIVES:
        expected = (
            primitive.capability(Edition.JAVA) is Capability.FULL
            and primitive.capability(Edition.BEDROCK) is Capability.FULL
        )
        assert primitive.is_portable() is expected


def test_the_api_lookup_returns_none_for_unknown_names() -> None:
    assert get_primitive("doesNotExist") is None
