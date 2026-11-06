"""Minimal NBT encoding and decoding, with no third-party dependencies.

Two byte orders are needed by the Minecraft world exporters:

* Java Edition stores ``level.dat`` as gzip-compressed big-endian NBT.
* Bedrock Edition stores ``level.dat`` as uncompressed little-endian NBT
  behind an eight byte header.

Only the tags a world needs are implemented, but they are enough to round
trip what LuaCraft writes and to validate it in the sandbox and tests.
"""

from __future__ import annotations

import gzip
import struct
from dataclasses import dataclass, field
from typing import Any

# NBT tag ids.
TAG_END = 0
TAG_BYTE = 1
TAG_SHORT = 2
TAG_INT = 3
TAG_LONG = 4
TAG_FLOAT = 5
TAG_DOUBLE = 6
TAG_BYTE_ARRAY = 7
TAG_STRING = 8
TAG_LIST = 9
TAG_COMPOUND = 10
TAG_INT_ARRAY = 11
TAG_LONG_ARRAY = 12


class NbtError(ValueError):
    """The bytes are not valid NBT."""


# -- typed scalars --------------------------------------------------------


@dataclass(frozen=True)
class Byte:
    value: int = 0

    def __post_init__(self) -> None:
        if not -128 <= self.value <= 127:
            raise NbtError(f"byte out of range: {self.value}")


@dataclass(frozen=True)
class Short:
    value: int = 0

    def __post_init__(self) -> None:
        if not -32768 <= self.value <= 32767:
            raise NbtError(f"short out of range: {self.value}")


@dataclass(frozen=True)
class Int:
    value: int = 0

    def __post_init__(self) -> None:
        if not -(2**31) <= self.value < 2**31:
            raise NbtError(f"int out of range: {self.value}")


@dataclass(frozen=True)
class Long:
    value: int = 0

    def __post_init__(self) -> None:
        if not -(2**63) <= self.value < 2**63:
            raise NbtError(f"long out of range: {self.value}")


@dataclass(frozen=True)
class Float:
    value: float = 0.0


@dataclass(frozen=True)
class Double:
    value: float = 0.0


@dataclass(frozen=True)
class String:
    value: str = ""


@dataclass(frozen=True)
class ByteArray:
    value: bytes = b""


@dataclass(frozen=True)
class IntArray:
    value: tuple[int, ...] = ()


@dataclass(frozen=True)
class LongArray:
    value: tuple[int, ...] = ()


@dataclass(frozen=True)
class List:
    """A homogeneous NBT list.  ``kind`` is required when it is empty."""

    value: tuple[Any, ...] = ()
    kind: int = TAG_END


@dataclass
class Compound:
    """An ordered NBT compound.  Order is preserved for reproducible bytes."""

    value: dict[str, Any] = field(default_factory=dict)


def _kind_of(value: Any) -> int:
    if isinstance(value, Byte):
        return TAG_BYTE
    if isinstance(value, Short):
        return TAG_SHORT
    if isinstance(value, Int):
        return TAG_INT
    if isinstance(value, Long):
        return TAG_LONG
    if isinstance(value, Float):
        return TAG_FLOAT
    if isinstance(value, Double):
        return TAG_DOUBLE
    if isinstance(value, ByteArray):
        return TAG_BYTE_ARRAY
    if isinstance(value, String) or isinstance(value, str):
        return TAG_STRING
    if isinstance(value, List):
        return TAG_LIST
    if isinstance(value, Compound) or isinstance(value, dict):
        return TAG_COMPOUND
    if isinstance(value, IntArray):
        return TAG_INT_ARRAY
    if isinstance(value, LongArray):
        return TAG_LONG_ARRAY
    raise NbtError(f"cannot infer an NBT tag for {type(value).__name__}")


class _Writer:
    def __init__(self, *, little_endian: bool) -> None:
        self.little = little_endian
        self.buffer = bytearray()

    def _pack(self, fmt: str, value: Any) -> None:
        self.buffer += struct.pack(("<" if self.little else ">") + fmt, value)

    def _string(self, text: str) -> None:
        encoded = text.encode("utf-8")
        self._pack("H", len(encoded))
        self.buffer += encoded

    def tag(self, kind: int, name: str, value: Any) -> None:
        self.buffer.append(kind)
        if kind == TAG_END:
            return
        self._string(name)
        self.payload(kind, value)

    def payload(self, kind: int, value: Any) -> None:
        if kind == TAG_BYTE:
            self._pack("b", value.value if isinstance(value, Byte) else int(value))
        elif kind == TAG_SHORT:
            self._pack("h", value.value if isinstance(value, Short) else int(value))
        elif kind == TAG_INT:
            self._pack("i", value.value if isinstance(value, Int) else int(value))
        elif kind == TAG_LONG:
            self._pack("q", value.value if isinstance(value, Long) else int(value))
        elif kind == TAG_FLOAT:
            self._pack("f", value.value if isinstance(value, Float) else float(value))
        elif kind == TAG_DOUBLE:
            self._pack("d", value.value if isinstance(value, Double) else float(value))
        elif kind == TAG_STRING:
            self._string(value.value if isinstance(value, String) else str(value))
        elif kind == TAG_BYTE_ARRAY:
            raw = value.value if isinstance(value, ByteArray) else bytes(value)
            self._pack("i", len(raw))
            self.buffer += raw
        elif kind == TAG_INT_ARRAY:
            items = value.value if isinstance(value, IntArray) else tuple(value)
            self._pack("i", len(items))
            for item in items:
                self._pack("i", int(item))
        elif kind == TAG_LONG_ARRAY:
            items = value.value if isinstance(value, LongArray) else tuple(value)
            self._pack("i", len(items))
            for item in items:
                self._pack("q", int(item))
        elif kind == TAG_LIST:
            if isinstance(value, List):
                items, element_kind = value.value, value.kind
            else:
                items = tuple(value)
                element_kind = _kind_of(items[0]) if items else TAG_END
            if items and element_kind == TAG_END:
                element_kind = _kind_of(items[0])
            self.buffer.append(element_kind)
            self._pack("i", len(items))
            for item in items:
                self.payload(element_kind, item)
        elif kind == TAG_COMPOUND:
            entries = value.value if isinstance(value, Compound) else value
            for key, item in entries.items():
                item_kind = _kind_of(item)
                self.tag(item_kind, key, item)
            self.buffer.append(TAG_END)
        else:  # pragma: no cover - guarded by _kind_of
            raise NbtError(f"unsupported tag id {kind}")

    def root(self, value: Compound | dict[str, Any], name: str = "") -> bytes:
        self.tag(TAG_COMPOUND, name, value)
        return bytes(self.buffer)


class _Reader:
    def __init__(self, data: bytes, *, little_endian: bool) -> None:
        self.data = data
        self.little = little_endian
        self.offset = 0

    def _unpack(self, fmt: str) -> Any:
        size = struct.calcsize(fmt)
        if self.offset + size > len(self.data):
            raise NbtError("unexpected end of NBT data")
        value = struct.unpack_from(
            ("<" if self.little else ">") + fmt, self.data, self.offset
        )[0]
        self.offset += size
        return value

    def _string(self) -> str:
        length = self._unpack("H")
        if self.offset + length > len(self.data):
            raise NbtError("string runs past the end of the NBT data")
        text = self.data[self.offset : self.offset + length].decode("utf-8")
        self.offset += length
        return text

    def payload(self, kind: int) -> Any:
        if kind == TAG_BYTE:
            return Byte(self._unpack("b"))
        if kind == TAG_SHORT:
            return Short(self._unpack("h"))
        if kind == TAG_INT:
            return Int(self._unpack("i"))
        if kind == TAG_LONG:
            return Long(self._unpack("q"))
        if kind == TAG_FLOAT:
            return Float(self._unpack("f"))
        if kind == TAG_DOUBLE:
            return Double(self._unpack("d"))
        if kind == TAG_STRING:
            return String(self._string())
        if kind == TAG_BYTE_ARRAY:
            length = self._unpack("i")
            raw = self.data[self.offset : self.offset + length]
            if len(raw) != length:
                raise NbtError("byte array runs past the end of the NBT data")
            self.offset += length
            return ByteArray(raw)
        if kind == TAG_INT_ARRAY:
            length = self._unpack("i")
            return IntArray(tuple(self._unpack("i") for _ in range(length)))
        if kind == TAG_LONG_ARRAY:
            length = self._unpack("i")
            return LongArray(tuple(self._unpack("q") for _ in range(length)))
        if kind == TAG_LIST:
            element_kind = self.data[self.offset]
            self.offset += 1
            length = self._unpack("i")
            if element_kind == TAG_END:
                return List(tuple(), TAG_END)
            return List(
                tuple(self.payload(element_kind) for _ in range(length)),
                element_kind,
            )
        if kind == TAG_COMPOUND:
            result: dict[str, Any] = {}
            while True:
                child_kind = self.data[self.offset]
                self.offset += 1
                if child_kind == TAG_END:
                    break
                name = self._string()
                result[name] = self.payload(child_kind)
            return Compound(result)
        raise NbtError(f"unsupported tag id {kind}")

    def root(self) -> Compound:
        kind = self.data[self.offset]
        self.offset += 1
        if kind != TAG_COMPOUND:
            raise NbtError(f"the NBT root must be a compound, got tag {kind}")
        self._string()  # root name, normally empty
        return self.payload(TAG_COMPOUND)


def encode(value: Compound | dict[str, Any], *, little_endian: bool = False) -> bytes:
    """Serialize a compound as NBT, without any file header."""
    return _Writer(little_endian=little_endian).root(value)


def decode(data: bytes, *, little_endian: bool = False) -> Compound:
    """Parse one NBT root compound."""
    return _Reader(data, little_endian=little_endian).root()


def encode_java_level(value: Compound | dict[str, Any]) -> bytes:
    """Serialize a Java ``level.dat``: big-endian NBT inside gzip."""
    return gzip.compress(encode(value, little_endian=False), mtime=0)


def decode_java_level(data: bytes) -> Compound:
    return decode(gzip.decompress(data), little_endian=False)


def encode_bedrock_level(
    value: Compound | dict[str, Any], *, storage_version: int = 10
) -> bytes:
    """Serialize a Bedrock ``level.dat``: header plus little-endian NBT."""
    payload = encode(value, little_endian=True)
    return struct.pack("<II", storage_version, len(payload)) + payload


def decode_bedrock_level(data: bytes) -> Compound:
    if len(data) < 8:
        raise NbtError("Bedrock level.dat is shorter than its 8-byte header")
    storage_version, length = struct.unpack_from("<II", data, 0)
    if storage_version < 1:
        raise NbtError(f"invalid Bedrock storage version {storage_version}")
    payload = data[8 : 8 + length]
    if len(payload) != length:
        raise NbtError("Bedrock level.dat is truncated")
    return decode(payload, little_endian=True)


def to_plain(value: Any) -> Any:
    """Turn typed tags into plain Python values, for diagnostics and tests."""
    if isinstance(value, (Byte, Short, Int, Long)):
        return value.value
    if isinstance(value, (Float, Double)):
        return value.value
    if isinstance(value, String):
        return value.value
    if isinstance(value, ByteArray):
        return value.value
    if isinstance(value, (IntArray, LongArray)):
        return list(value.value)
    if isinstance(value, List):
        return [to_plain(item) for item in value.value]
    if isinstance(value, Compound):
        return {key: to_plain(item) for key, item in value.value.items()}
    return value
