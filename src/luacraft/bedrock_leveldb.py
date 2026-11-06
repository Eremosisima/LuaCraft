"""Write a tiny, valid LevelDB database for a Bedrock world.

Bedrock Edition stores its chunks in a LevelDB database under ``db/``.  A
world with an empty or missing database is reported as corrupted, so the
world exporter has to create one.  The database can be empty: Bedrock fills
it with terrain when the world is opened for the first time.

Only the files LevelDB needs to open an empty database are written:

* ``CURRENT`` points at the manifest,
* ``MANIFEST-000001`` holds one version edit,
* ``LOG``, ``LOG.old``, ``LOCK`` and one empty write-ahead log are created.

The implementation is deliberately small and dependency free.  It follows
the LevelDB log and version-edit formats, including the masked CRC32C
checksum the format requires.
"""

from __future__ import annotations

import struct
from pathlib import Path

# --------------------------------------------------------------------------
# Checksums
# --------------------------------------------------------------------------


def _crc32c_table() -> tuple[int, ...]:
    table: list[int] = []
    for value in range(256):
        crc = value
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
        table.append(crc)
    return tuple(table)


_CRC32C_TABLE = _crc32c_table()


def crc32c(data: bytes) -> int:
    """Return the CRC32C (Castagnoli) checksum of ``data``."""
    crc = 0xFFFFFFFF
    for byte in data:
        crc = _CRC32C_TABLE[(crc ^ byte) & 0xFF] ^ (crc >> 8)
    return crc ^ 0xFFFFFFFF


def masked_crc32c(data: bytes) -> int:
    """Mask a CRC32C the way the LevelDB log format stores it."""
    crc = crc32c(data)
    rotated = ((crc >> 15) | (crc << 17)) & 0xFFFFFFFF
    return (rotated + 0xA282EAD8) & 0xFFFFFFFF


# --------------------------------------------------------------------------
# LevelDB encoding primitives
# --------------------------------------------------------------------------


def _varint(value: int) -> bytes:
    if value < 0:
        raise ValueError("varints are unsigned")
    out = bytearray()
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _slice(data: bytes | str) -> bytes:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    return _varint(len(raw)) + raw


def log_record(payload: bytes, record_type: int = 1) -> bytes:
    """Encode one full LevelDB log record."""
    checksum = masked_crc32c(bytes([record_type]) + payload)
    return struct.pack("<IHB", checksum, len(payload), record_type) + payload


# VersionEdit tags, from leveldb/db/version_edit.cc.
_TAG_COMPARATOR = 1
_TAG_LOG_NUMBER = 2
_TAG_NEXT_FILE_NUMBER = 3
_TAG_LAST_SEQUENCE = 4


def version_edit(
    *,
    comparator: str = "leveldb.BytewiseComparator",
    log_number: int = 0,
    next_file_number: int = 4,
    last_sequence: int = 0,
) -> bytes:
    """Encode the version edit of a brand-new, empty database."""
    out = bytearray()
    out += _varint(_TAG_COMPARATOR) + _slice(comparator)
    out += _varint(_TAG_LOG_NUMBER) + _varint(log_number)
    out += _varint(_TAG_NEXT_FILE_NUMBER) + _varint(next_file_number)
    out += _varint(_TAG_LAST_SEQUENCE) + _varint(last_sequence)
    return bytes(out)


def write_empty_db(directory: Path) -> Path:
    """Create an empty but valid LevelDB database in ``directory``."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "MANIFEST-000001").write_bytes(log_record(version_edit()))
    (directory / "CURRENT").write_text("MANIFEST-000001\n", encoding="ascii")
    (directory / "LOCK").write_bytes(b"")
    (directory / "LOG").write_bytes(b"")
    (directory / "LOG.old").write_bytes(b"")
    # An empty write-ahead log.  The manifest points at log 0, so LevelDB
    # ignores this file and creates its own on first open; keeping it makes
    # the folder look like a normal database to other tools.
    (directory / "000003.log").write_bytes(b"")
    return directory


def read_manifest_edit(path: Path) -> bytes:
    """Read the payload of the first log record, for tests and diagnostics."""
    data = path.read_bytes()
    if len(data) < 7:
        raise ValueError("manifest is too short")
    checksum, length, record_type = struct.unpack_from("<IHB", data, 0)
    payload = data[7 : 7 + length]
    if len(payload) != length:
        raise ValueError("manifest is truncated")
    if record_type != 1:
        raise ValueError(f"unexpected log record type {record_type}")
    if masked_crc32c(bytes([record_type]) + payload) != checksum:
        raise ValueError("manifest checksum does not match")
    return payload
