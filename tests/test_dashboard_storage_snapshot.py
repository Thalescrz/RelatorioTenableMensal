from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tenable_reports.webapp.storage_snapshot import (
    TransientStorageSnapshot,
    TransientStorageSnapshotCache,
    scan_transient_storage,
)


class _Clock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def test_storage_snapshot_cache_respects_ttl_and_invalidation(tmp_path: Path) -> None:
    clock = _Clock()
    calls: list[tuple[Path, tuple[str, ...], float]] = []

    def scanner(root: Path, client_ids: tuple[str, ...], scanned_at: float):
        calls.append((root, client_ids, scanned_at))
        return TransientStorageSnapshot(
            temporary_bytes=len(calls),
            by_client={client_id: 0 for client_id in client_ids},
            scanned_at=scanned_at,
        )

    cache = TransientStorageSnapshotCache(
        ttl_seconds=30,
        monotonic=clock,
        scanner=scanner,
    )

    first = cache.get(tmp_path, ("client-a",))
    second = cache.get(tmp_path, ("client-a",))
    assert first is second
    assert len(calls) == 1

    clock.advance(31)
    third = cache.get(tmp_path, ("client-a",))
    assert third.temporary_bytes == 2
    assert len(calls) == 2

    cache.invalidate()
    fourth = cache.get(tmp_path, ("client-a",))
    assert fourth.temporary_bytes == 3
    assert len(calls) == 3


def test_storage_snapshot_ignores_file_disappearing_during_stat(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "manual" / "raw" / "client-a" / "run-a" / "chunk.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"fixture")
    original_stat = Path.stat

    def disappearing_stat(path: Path, *args, **kwargs):
        if path == target:
            raise FileNotFoundError(str(path))
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", disappearing_stat)

    snapshot = scan_transient_storage(
        tmp_path,
        ("client-a",),
        scanned_at=10.0,
    )

    assert snapshot.temporary_bytes == 0
    assert snapshot.by_client == {"client-a": 0}


def test_storage_snapshot_cache_serializes_concurrent_scans(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def scanner(root: Path, client_ids: tuple[str, ...], scanned_at: float):
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(3)
        return TransientStorageSnapshot(0, {"client-a": 0}, scanned_at)

    cache = TransientStorageSnapshotCache(scanner=scanner)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(cache.get, tmp_path, ("client-a",))
        assert entered.wait(3)
        second = executor.submit(cache.get, tmp_path, ("client-a",))
        release.set()
        assert first.result(timeout=3) is second.result(timeout=3)

    assert calls == 1
