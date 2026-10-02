from __future__ import annotations

import copy
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from tenable_reports.domain.reporting import parse_datetime
from tenable_reports.domain.report_reference import MONTHLY_CANONICAL_MODE


MONTHLY_CUTOFF_MODE = "MONTHLY_CUTOFF"
_MONTHS_PT = (
    "JAN",
    "FEV",
    "MAR",
    "ABR",
    "MAI",
    "JUN",
    "JUL",
    "AGO",
    "SET",
    "OUT",
    "NOV",
    "DEZ",
)
_DOCUMENT_PERIOD_SUFFIX = re.compile(
    r"\s+(?:(?:\d{2})?[A-Z]{3}\d{2}"
    r"(?:-(?:\d{2})?[A-Z]{3}\d{2})?)\.docx$"
)


def _month_start(period_id: str, timezone_name: str) -> datetime:
    try:
        parsed = datetime.strptime(period_id, "%Y-%m")
    except ValueError as exc:
        raise ValueError("A competência precisa usar o formato YYYY-MM.") from exc
    return parsed.replace(tzinfo=ZoneInfo(timezone_name))


def is_monthly_cutoff_interval(
    *,
    period_id: str,
    start_at: str,
    end_at: str,
    timezone_name: str,
) -> bool:
    month_start = _month_start(period_id, timezone_name)
    start = parse_datetime(start_at, timezone_name)
    end = parse_datetime(end_at, timezone_name)
    next_month_start = (
        month_start.replace(year=month_start.year + 1, month=1)
        if month_start.month == 12
        else month_start.replace(month=month_start.month + 1)
    )
    last_day = (next_month_start - timedelta(days=1)).date()
    return (
        start == month_start
        and end > start
        and (
            end == next_month_start
            or (end < next_month_start and end.date() == last_day)
        )
    )


def needs_monthly_cutoff_repair(
    *,
    stored_period_id: str,
    stored_period_mode: str,
    manifest_period: Mapping[str, Any],
    target_period_id: str,
) -> bool:
    return not (
        str(stored_period_id).strip() == target_period_id
        and str(stored_period_mode).strip() == MONTHLY_CUTOFF_MODE
        and str(manifest_period.get("period_id") or "").strip() == target_period_id
        and str(manifest_period.get("mode") or "").strip() == MONTHLY_CUTOFF_MODE
    )


def _competence_suffix(period_id: str) -> str:
    parsed = _month_start(period_id, "UTC")
    return f"{_MONTHS_PT[parsed.month - 1]}{parsed.year % 100:02d}"


def monthly_competence_document_path(path: str | Path, *, period_id: str) -> Path:
    source = Path(path).resolve()
    suffix = _competence_suffix(period_id)
    if source.name.endswith(f" {suffix}.docx"):
        return source
    if not _DOCUMENT_PERIOD_SUFFIX.search(source.name):
        raise ValueError(f"Documento sem sufixo de período reconhecido: {source.name}")
    name = _DOCUMENT_PERIOD_SUFFIX.sub(f" {suffix}.docx", source.name)
    return source.with_name(name)


def rewrite_monthly_cutoff_manifest(
    manifest: Mapping[str, Any],
    *,
    period_id: str,
    document_paths: Mapping[Path, Path],
    repaired_at: str,
) -> dict[str, Any]:
    updated = copy.deepcopy(dict(manifest))
    period = updated.get("period")
    if not isinstance(period, dict):
        raise ValueError("Manifesto sem período válido.")
    documents = updated.get("documents")
    if not isinstance(documents, list):
        raise ValueError("Manifesto sem documentos válidos.")
    period["period_id"] = period_id
    period["mode"] = MONTHLY_CUTOFF_MODE
    for document in documents:
        if not isinstance(document, dict):
            raise ValueError("Manifesto possui documento inválido.")
        source = Path(str(document.get("path") or "")).resolve()
        target = document_paths.get(source)
        if target is None:
            raise ValueError("Documento do manifesto não pertence ao plano de correção.")
        document["path"] = str(target.resolve())
    updated["competence_repair"] = {
        "period_id": period_id,
        "mode": MONTHLY_CUTOFF_MODE,
        "repaired_at": repaired_at,
        "effective_end_at_preserved": True,
    }
    return updated


def rewrite_monthly_cutoff_checkpoint(
    checkpoint: Mapping[str, Any],
    *,
    period_id: str,
) -> dict[str, Any]:
    """Reclassify a persisted checkpoint without changing its collection window.

    Checkpoint compatibility intentionally includes the period mode and identifier.
    A competence repair therefore has to update those two identity fields together
    with the report metadata.  The effective [start, end) interval remains the
    original audited collection window.
    """

    updated = copy.deepcopy(dict(checkpoint))
    period = updated.get("period")
    if not isinstance(period, dict):
        raise ValueError("Checkpoint sem período válido.")
    timezone_name = str(period.get("timezone") or "").strip()
    start_at = str(period.get("start_at") or "").strip()
    end_at = str(period.get("end_at") or "").strip()
    if not timezone_name or not start_at or not end_at:
        raise ValueError("Checkpoint sem janela de competência completa.")
    if not is_monthly_cutoff_interval(
        period_id=period_id,
        start_at=start_at,
        end_at=end_at,
        timezone_name=timezone_name,
    ):
        raise ValueError("Checkpoint fora da competência mensal informada.")
    period["period_id"] = period_id
    period["mode"] = MONTHLY_CUTOFF_MODE
    return updated


def rewrite_history_snapshot_payload(
    payload: Mapping[str, Any],
    *,
    period_id: str,
) -> dict[str, Any]:
    """Align the snapshot identity and its nested compatibility metadata."""

    updated = copy.deepcopy(dict(payload))
    compatibility = updated.get("compatibility")
    if not isinstance(compatibility, dict):
        raise ValueError("Snapshot histórico sem compatibilidade válida.")
    updated["period_id"] = period_id
    compatibility["period_mode"] = MONTHLY_CANONICAL_MODE
    return updated


__all__ = [
    "MONTHLY_CANONICAL_MODE",
    "MONTHLY_CUTOFF_MODE",
    "is_monthly_cutoff_interval",
    "monthly_competence_document_path",
    "needs_monthly_cutoff_repair",
    "rewrite_monthly_cutoff_checkpoint",
    "rewrite_history_snapshot_payload",
    "rewrite_monthly_cutoff_manifest",
]
