"""Public package interface for LuaCraft."""

from __future__ import annotations

from .api import (
    Capability,
    Edition,
    Primitive,
    PRIMITIVES,
    PRIMITIVE_BY_NAME,
    PORTABLE_PRIMITIVES,
    EDITION_ONLY_PRIMITIVES,
    describe_capabilities,
    get_primitive,
)
from .diagnostics import (
    Category,
    Diagnostic,
    DiagnosticBag,
    Severity,
)
from .luaparse import Chunk, LuaSyntaxError, parse

__version__ = "0.4.0"

__all__ = [
    "Capability",
    "Category",
    "Chunk",
    "Diagnostic",
    "DiagnosticBag",
    "EDITION_ONLY_PRIMITIVES",
    "Edition",
    "LuaSyntaxError",
    "PRIMITIVES",
    "PRIMITIVE_BY_NAME",
    "PORTABLE_PRIMITIVES",
    "Primitive",
    "Severity",
    "__version__",
    "describe_capabilities",
    "get_primitive",
    "parse",
]
