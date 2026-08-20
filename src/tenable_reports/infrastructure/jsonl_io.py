from __future__ import annotations

import gzip
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, TextIO


@dataclass(frozen=True, slots=True)
class JsonlWriteResult:
    path: Path
    records: int
    logical_bytes: int
    stored_bytes: int
    sha256: str


def _text_reader(path: Path) -> TextIO:
    if path.name.endswith(".jsonl.gz"):
        return gzip.open(path, mode="rt", encoding="utf-8", newline="")
    return path.open(mode="r", encoding="utf-8", newline="")


def iter_jsonl_objects(path: str | Path) -> Iterator[dict[str, Any]]:
    source = Path(path)
    try:
        with _text_reader(source) as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"JSONL inválido em {source}, linha {line_number}."
                    ) from exc
                if not isinstance(value, dict):
                    raise ValueError(
                        f"Registro JSONL não é objeto em {source}, linha {line_number}."
                    )
                yield value
    except OSError as exc:
        raise ValueError(f"Não foi possível ler o artefato JSONL: {source}") from exc


def write_jsonl_gzip_exclusive(
    path: str | Path,
    records: Iterable[Mapping[str, Any]],
) -> JsonlWriteResult:
    output = Path(path)
    if not output.name.endswith(".jsonl.gz"):
        raise ValueError("O artefato comprimido deve terminar em .jsonl.gz.")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f"Artefato imutável já existe: {output}")

    partial = output.with_name(f"{output.name}.partial")
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    record_count = 0
    logical_bytes = 0
    try:
        with os.fdopen(descriptor, "wb") as raw_stream:
            with gzip.GzipFile(
                filename="",
                mode="wb",
                fileobj=raw_stream,
                compresslevel=6,
                mtime=0,
            ) as compressed:
                for record in records:
                    line = (
                        json.dumps(
                            dict(record),
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        + "\n"
                    ).encode("utf-8")
                    compressed.write(line)
                    record_count += 1
                    logical_bytes += len(line)
        if output.exists():
            raise FileExistsError(f"Artefato imutável já existe: {output}")
        partial.rename(output)
    except Exception:
        partial.unlink(missing_ok=True)
        raise

    digest = hashlib.sha256()
    with output.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return JsonlWriteResult(
        path=output,
        records=record_count,
        logical_bytes=logical_bytes,
        stored_bytes=output.stat().st_size,
        sha256=digest.hexdigest(),
    )


def resolve_jsonl_artifact(directory: str | Path, stem: str) -> Path:
    if not stem or Path(stem).name != stem:
        raise ValueError("O nome-base do artefato JSONL é inválido.")
    root = Path(directory)
    legacy = root / f"{stem}.jsonl"
    compressed = root / f"{stem}.jsonl.gz"
    available = tuple(path for path in (compressed, legacy) if path.is_file())
    if len(available) > 1:
        raise ValueError(
            f"Artefatos JSONL ambíguos para {stem}: existem versões gzip e legada."
        )
    if not available:
        raise FileNotFoundError(f"Artefato JSONL não encontrado para {stem} em {root}.")
    return available[0]
