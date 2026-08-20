from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import pytest

from tenable_reports.infrastructure.jsonl_io import (
    iter_jsonl_objects,
    resolve_jsonl_artifact,
    write_jsonl_gzip_exclusive,
)


def test_gzip_round_trip_reports_stored_and_logical_sizes(tmp_path: Path) -> None:
    path = tmp_path / "findings.jsonl.gz"
    records = ({"id": number, "value": "repeated-value" * 20} for number in range(100))

    result = write_jsonl_gzip_exclusive(path, records)

    restored = list(iter_jsonl_objects(path))
    assert restored[0] == {"id": 0, "value": "repeated-value" * 20}
    assert restored[-1] == {"id": 99, "value": "repeated-value" * 20}
    assert result.records == 100
    assert result.logical_bytes > result.stored_bytes
    assert result.stored_bytes == path.stat().st_size
    assert result.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_gzip_output_is_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first.jsonl.gz"
    second = tmp_path / "second.jsonl.gz"
    records = [{"name": "exemplo", "severity": "HIGH"}]

    write_jsonl_gzip_exclusive(first, records)
    write_jsonl_gzip_exclusive(second, records)

    assert first.read_bytes() == second.read_bytes()


def test_writer_never_overwrites_immutable_artifact(tmp_path: Path) -> None:
    path = tmp_path / "assets.jsonl.gz"
    write_jsonl_gzip_exclusive(path, [{"id": "first"}])

    with pytest.raises(FileExistsError):
        write_jsonl_gzip_exclusive(path, [{"id": "second"}])

    assert list(iter_jsonl_objects(path)) == [{"id": "first"}]


def test_reader_keeps_legacy_jsonl_compatibility(tmp_path: Path) -> None:
    legacy = tmp_path / "assets.jsonl"
    legacy.write_text('{"id":"asset-a"}\n', encoding="utf-8")

    assert list(iter_jsonl_objects(legacy)) == [{"id": "asset-a"}]
    assert resolve_jsonl_artifact(tmp_path, "assets") == legacy


def test_resolver_rejects_ambiguous_legacy_and_gzip_pair(tmp_path: Path) -> None:
    (tmp_path / "findings.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "findings.jsonl.gz").write_bytes(gzip.compress(b""))

    with pytest.raises(ValueError, match="ambíguos"):
        resolve_jsonl_artifact(tmp_path, "findings")


def test_reader_identifies_invalid_jsonl_line(tmp_path: Path) -> None:
    path = tmp_path / "invalid.jsonl"
    path.write_text('{"valid":true}\nnot-json\n', encoding="utf-8")

    with pytest.raises(ValueError, match="linha 2"):
        list(iter_jsonl_objects(path))


def test_writer_uses_canonical_compact_json(tmp_path: Path) -> None:
    path = tmp_path / "canonical.jsonl.gz"
    write_jsonl_gzip_exclusive(path, [{"z": 1, "á": "valor"}])

    with gzip.open(path, "rt", encoding="utf-8") as stream:
        line = stream.read()

    assert line == json.dumps(
        {"z": 1, "á": "valor"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"
