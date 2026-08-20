from __future__ import annotations

from collections import namedtuple
from pathlib import Path

import pytest

from tenable_reports.application.failures import FailureCode, OperationalFailure
from tenable_reports.application.storage_guard import (
    GIB,
    estimate_compressed_peak,
    required_free_bytes,
    storage_preflight,
)


def test_storage_estimate_uses_history_with_floor() -> None:
    assert required_free_bytes(last_success_bytes=4 * GIB) == 10 * GIB
    assert required_free_bytes(last_success_bytes=None) == 10 * GIB
    assert required_free_bytes(last_success_bytes=8 * GIB) == 14 * GIB


def test_disk_preflight_blocks_before_work_starts(tmp_path: Path) -> None:
    Usage = namedtuple("Usage", "total used free")

    with pytest.raises(OperationalFailure) as caught:
        storage_preflight(
            tmp_path,
            last_success_bytes=None,
            disk_usage=lambda _: Usage(20 * GIB, 18 * GIB, 2 * GIB),
        )

    assert caught.value.code is FailureCode.DISK_INSUFFICIENT
    assert caught.value.retryable is True


def test_compressed_estimate_uses_observed_ratio_with_safe_floor() -> None:
    estimate = estimate_compressed_peak(
        last_logical_bytes=10 * GIB,
        last_stored_bytes=1536 * 1024 ** 2,
        minimum_free_gb=10,
    )

    assert estimate.estimated_staging_bytes < 3 * GIB
    assert estimate.required_free_bytes >= 10 * GIB
    assert 0 < estimate.observed_compression_ratio < 1
