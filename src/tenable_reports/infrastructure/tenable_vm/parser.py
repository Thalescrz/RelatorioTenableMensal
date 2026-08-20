from __future__ import annotations

import gzip
import io
import json
from pathlib import Path
from typing import Any, Iterator


class ChunkParseError(ValueError):
    """Chunk nao contem JSON suportado."""


def records_from_json_payload(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("vulnerabilities", "findings", "data", "items", "results"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        if any(key in data for key in ("id", "asset", "definition", "plugin", "severity")):
            return [data]
    return []


def parse_chunk_response(content: bytes) -> list[dict[str, Any]]:
    if not content:
        return []
    payload = content
    if payload[:2] == b"\x1f\x8b":
        try:
            payload = gzip.decompress(payload)
        except (OSError, EOFError) as exc:
            raise ChunkParseError("Chunk gzip invalido.") from exc

    try:
        text = payload.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise ChunkParseError("Chunk nao esta codificado em UTF-8.") from exc
    if not text:
        return []

    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        decoded = None
    if decoded is not None:
        records = records_from_json_payload(decoded)
        if records or decoded in ([], {}):
            return records

    records: list[dict[str, Any]] = []
    invalid_lines = 0
    for raw_line in text.splitlines():
        line = raw_line.strip().rstrip(",")
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            invalid_lines += 1
            continue
        if isinstance(item, dict):
            records.append(item)
    if records and invalid_lines == 0:
        return records
    raise ChunkParseError("Chunk nao parece JSON valido nem JSON Lines valido.")


def iter_chunk_records(path: str | Path) -> Iterator[dict[str, Any]]:
    source = Path(path)
    try:
        with source.open("rb") as probe:
            is_gzip = probe.read(2) == b"\x1f\x8b"
        opener = gzip.open if is_gzip else open
        with opener(source, "rb") as binary_stream:
            with io.TextIOWrapper(binary_stream, encoding="utf-8") as stream:
                yield from _iter_stream_records(stream)
    except (OSError, EOFError, UnicodeDecodeError) as exc:
        raise ChunkParseError(f"Chunk armazenado invalido: {source}") from exc


def _iter_stream_records(stream: Any) -> Iterator[dict[str, Any]]:
    decoder = json.JSONDecoder()
    buffer = ""
    eof = False

    def fill() -> bool:
        nonlocal buffer, eof
        chunk = stream.read(64 * 1024)
        if chunk:
            buffer += chunk
            return True
        eof = True
        return False

    while not buffer.strip() and not eof:
        fill()
    stripped = buffer.lstrip()
    if not stripped:
        return
    array_mode = stripped.startswith("[")
    if array_mode:
        buffer = stripped[1:]

    while True:
        buffer = buffer.lstrip()
        if array_mode:
            if buffer.startswith("]"):
                trailing = buffer[1:] + stream.read()
                if trailing.strip():
                    raise ChunkParseError("Conteúdo inesperado após o array JSON.")
                return
            if buffer.startswith(","):
                buffer = buffer[1:].lstrip()
        elif not buffer and eof:
            return

        try:
            value, end = decoder.raw_decode(buffer)
        except json.JSONDecodeError as exc:
            if not eof and fill():
                continue
            raise ChunkParseError(
                "Chunk nao parece JSON valido nem JSON Lines valido."
            ) from exc

        buffer = buffer[end:]
        if array_mode and isinstance(value, dict):
            yield value
        else:
            yield from records_from_json_payload(value)

        while not buffer.strip() and not eof:
            fill()
        if array_mode:
            if eof and not buffer.strip():
                raise ChunkParseError("Array JSON incompleto.")
        elif eof and not buffer.strip():
            return
