from __future__ import annotations

import hashlib
import zlib
from typing import Iterable


FINGERPRINT_VERSION = "sha256-128-v1"
FINGERPRINT_SIZE = 16


def fingerprint_finding_key(value: str) -> bytes:
    canonical = str(value).encode("utf-8")
    return hashlib.sha256(canonical).digest()[:FINGERPRINT_SIZE]


def pack_fingerprints(values: Iterable[bytes]) -> bytes:
    ordered = sorted(set(bytes(value) for value in values))
    if any(len(value) != FINGERPRINT_SIZE for value in ordered):
        raise ValueError("Fingerprint deve possuir 16 bytes.")
    return zlib.compress(b"".join(ordered), level=9)


def unpack_fingerprints(payload: bytes | bytearray | memoryview) -> tuple[bytes, ...]:
    try:
        content = zlib.decompress(bytes(payload))
    except zlib.error as exc:
        raise ValueError("Pacote de fingerprints inválido.") from exc
    if len(content) % FINGERPRINT_SIZE:
        raise ValueError("Pacote de fingerprints inválido.")
    values = tuple(
        content[index:index + FINGERPRINT_SIZE]
        for index in range(0, len(content), FINGERPRINT_SIZE)
    )
    if values != tuple(sorted(set(values))):
        raise ValueError("Pacote de fingerprints inválido.")
    return values
