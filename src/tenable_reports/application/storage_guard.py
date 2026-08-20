from __future__ import annotations

import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from tenable_reports.application.failures import FailureCode, OperationalFailure


GIB = 1024 ** 3
DEFAULT_MINIMUM_FREE_GB = 10
DOWNLOAD_OVERHEAD_BYTES = 2 * GIB
COMPRESSED_STAGING_OVERHEAD_BYTES = 512 * 1024 ** 2


@dataclass(frozen=True, slots=True)
class StorageRequirement:
    path: Path
    available_bytes: int
    required_bytes: int
    last_success_bytes: int | None

    @property
    def sufficient(self) -> bool:
        return self.available_bytes >= self.required_bytes


@dataclass(frozen=True, slots=True)
class CompressedPeakEstimate:
    estimated_staging_bytes: int
    required_free_bytes: int
    observed_compression_ratio: float


def estimate_compressed_peak(
    *,
    last_logical_bytes: int | None,
    last_stored_bytes: int | None,
    minimum_free_gb: int = DEFAULT_MINIMUM_FREE_GB,
) -> CompressedPeakEstimate:
    if minimum_free_gb < 1:
        raise ValueError("minimum_free_gb deve ser maior ou igual a 1.")
    logical = int(last_logical_bytes or 0)
    stored = int(last_stored_bytes or 0)
    if logical < 0 or stored < 0:
        raise ValueError("Histórico de armazenamento não pode ser negativo.")
    observed_ratio = stored / logical if logical and stored else 0.25
    safe_ratio = min(0.75, max(0.10, observed_ratio))
    estimated = math.ceil(logical * safe_ratio * 1.25) + (
        COMPRESSED_STAGING_OVERHEAD_BYTES if logical else 0
    )
    floor = minimum_free_gb * GIB
    return CompressedPeakEstimate(
        estimated_staging_bytes=estimated,
        required_free_bytes=max(floor, estimated),
        observed_compression_ratio=observed_ratio,
    )


def required_free_bytes(
    *,
    last_success_bytes: int | None,
    minimum_free_gb: int = DEFAULT_MINIMUM_FREE_GB,
) -> int:
    if minimum_free_gb < 1:
        raise ValueError("minimum_free_gb deve ser maior ou igual a 1.")
    floor = minimum_free_gb * GIB
    if last_success_bytes is None:
        return floor
    if last_success_bytes < 0:
        raise ValueError("last_success_bytes não pode ser negativo.")
    estimate = math.ceil(last_success_bytes * 1.5) + DOWNLOAD_OVERHEAD_BYTES
    return max(floor, estimate)


def storage_preflight(
    path: str | Path,
    *,
    last_success_bytes: int | None,
    minimum_free_gb: int = DEFAULT_MINIMUM_FREE_GB,
    disk_usage: Callable[[Any], Any] = shutil.disk_usage,
) -> StorageRequirement:
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    usage = disk_usage(target)
    requirement = StorageRequirement(
        path=target.resolve(),
        available_bytes=int(usage.free),
        required_bytes=required_free_bytes(
            last_success_bytes=last_success_bytes,
            minimum_free_gb=minimum_free_gb,
        ),
        last_success_bytes=last_success_bytes,
    )
    if not requirement.sufficient:
        raise OperationalFailure(
            code=FailureCode.DISK_INSUFFICIENT,
            message=(
                "Espaço livre insuficiente para iniciar a coleta: "
                f"disponível={requirement.available_bytes}, "
                f"necessário={requirement.required_bytes}."
            ),
            retryable=True,
        )
    return requirement
