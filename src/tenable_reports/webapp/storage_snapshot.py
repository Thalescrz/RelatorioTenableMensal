"""Short-lived snapshot for the expensive transient-storage directory scan."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

from tenable_reports.application.retention import TRANSIENT_CATEGORIES


@dataclass(frozen=True, slots=True)
class TransientStorageSnapshot:
    temporary_bytes: int
    by_client: Mapping[str, int]
    scanned_at: float


def scan_transient_storage(
    output_root: Path,
    client_ids: Sequence[str],
    scanned_at: float,
) -> TransientStorageSnapshot:
    root = Path(output_root).resolve()
    by_client: dict[str, int] = {
        str(client_id): 0 for client_id in client_ids
    }
    temporary_bytes = 0
    for scope in ("automatic-monthly", "manual"):
        for category in TRANSIENT_CATEGORIES:
            category_root = root / scope / category
            if not category_root.is_dir():
                continue
            for path in category_root.rglob("*"):
                try:
                    if not path.is_file():
                        continue
                    size = path.stat().st_size
                except OSError:
                    continue
                temporary_bytes += size
                relative = path.relative_to(category_root)
                if relative.parts:
                    client_id = relative.parts[0]
                    by_client[client_id] = by_client.get(client_id, 0) + size
    return TransientStorageSnapshot(
        temporary_bytes=temporary_bytes,
        by_client=by_client,
        scanned_at=float(scanned_at),
    )


StorageScanner = Callable[
    [Path, tuple[str, ...], float],
    TransientStorageSnapshot,
]


class TransientStorageSnapshotCache:
    def __init__(
        self,
        *,
        ttl_seconds: float = 30.0,
        monotonic: Callable[[], float] = time.monotonic,
        scanner: StorageScanner = scan_transient_storage,
    ) -> None:
        self.ttl_seconds = max(0.0, float(ttl_seconds))
        self.monotonic = monotonic
        self.scanner = scanner
        self._lock = threading.RLock()
        self._snapshot: TransientStorageSnapshot | None = None
        self._key: tuple[Path, tuple[str, ...]] | None = None

    def get(
        self,
        output_root: Path,
        client_ids: Sequence[str],
    ) -> TransientStorageSnapshot:
        root = Path(output_root).resolve()
        normalized_clients = tuple(dict.fromkeys(str(item) for item in client_ids))
        key = (root, normalized_clients)
        with self._lock:
            now = float(self.monotonic())
            if (
                self._snapshot is not None
                and self._key == key
                and now - self._snapshot.scanned_at <= self.ttl_seconds
            ):
                return self._snapshot
            snapshot = self.scanner(root, normalized_clients, now)
            self._snapshot = snapshot
            self._key = key
            return snapshot

    def invalidate(self) -> None:
        with self._lock:
            self._snapshot = None
            self._key = None


__all__ = [
    "TransientStorageSnapshot",
    "TransientStorageSnapshotCache",
    "scan_transient_storage",
]
