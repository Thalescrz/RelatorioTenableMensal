from __future__ import annotations

import os
import re
import shutil
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence


class ReportArchiveError(RuntimeError):
    pass


class EmptyReportArchiveError(ReportArchiveError):
    pass


class UnsafeReportArchivePath(ReportArchiveError):
    pass


class InsufficientReportArchiveSpace(ReportArchiveError):
    pass


@dataclass(frozen=True, slots=True)
class ArchiveDocument:
    path: Path
    name: str | None = None


@dataclass(frozen=True, slots=True)
class ArchiveReportSet:
    client_id: str
    display_name: str
    run_id: str
    period_id: str
    is_main: bool
    documents: tuple[ArchiveDocument, ...]
    deleted: bool = False


@dataclass(frozen=True, slots=True)
class ArchiveClient:
    client_id: str
    display_name: str
    reports: tuple[ArchiveReportSet, ...]


@dataclass(frozen=True, slots=True)
class ReportArchiveResult:
    path: Path
    download_name: str
    included_clients: int
    included_documents: int
    omissions: tuple[str, ...]


_INVALID_COMPONENT = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MONTH_PATTERN = re.compile(r"\d{4}-\d{2}")
_ALLOWED_DOCUMENT_SUFFIXES = {".docx", ".pdf"}
_SPACE_RESERVE_BYTES = 16 * 1024 * 1024


def _safe_component(value: str, *, fallback: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).strip()
    normalized = _INVALID_COMPONENT.sub("-", normalized).rstrip(". ")
    normalized = re.sub(r"\s+", " ", normalized)
    return (normalized or fallback)[:120].rstrip(". ") or fallback


def _download_component(value: str, *, fallback: str) -> str:
    return _safe_component(value, fallback=fallback).replace(" ", "-")


def _unique_component(value: str, used: set[str]) -> str:
    candidate = value
    counter = 2
    while candidate.casefold() in used:
        candidate = f"{value} ({counter})"
        counter += 1
    used.add(candidate.casefold())
    return candidate


def _unique_filename(value: str, used: set[str]) -> str:
    path = Path(value)
    stem = path.stem or "relatorio"
    suffix = path.suffix
    candidate = f"{stem}{suffix}"
    counter = 2
    while candidate.casefold() in used:
        candidate = f"{stem} ({counter}){suffix}"
        counter += 1
    used.add(candidate.casefold())
    return candidate


def _source_path(document: ArchiveDocument, *, data_root: Path) -> Path:
    source = Path(document.path).resolve()
    root = data_root.resolve()
    if not source.is_relative_to(root):
        raise UnsafeReportArchivePath(
            "O conjunto referencia um documento fora do diretório de dados."
        )
    return source


def _summary_text(
    *,
    period_id: str,
    reports: Sequence[ArchiveReportSet],
    omissions: Sequence[str],
    included_documents: int,
) -> str:
    lines = [
        "RELATÓRIOS TENABLE",
        f"Período: {period_id}",
        f"Gerado em: {datetime.now(UTC).isoformat()}",
        f"Documentos incluídos: {included_documents}",
        "",
        "Conjuntos incluídos:",
    ]
    lines.extend(
        f"- {report.display_name} ({report.client_id}): {report.run_id}"
        + (" [MAIN]" if report.is_main else "")
        for report in reports
    )
    if omissions:
        lines.extend(("", "Omissões e alertas:"))
        lines.extend(f"- {item}" for item in omissions)
    return "\n".join(lines) + "\n"


def _build_archive(
    *,
    data_root: Path,
    temporary_root: Path,
    period_id: str,
    reports: Sequence[ArchiveReportSet],
    initial_omissions: Sequence[str],
    download_name: str,
) -> ReportArchiveResult:
    root_name = _safe_component(
        f"Relatorios-Tenable-{period_id}",
        fallback="Relatorios-Tenable",
    )
    omissions = list(initial_omissions)
    entries: list[tuple[Path, str]] = []
    included_reports: list[ArchiveReportSet] = []
    used_folders: set[str] = set()

    for report in reports:
        if report.deleted:
            omissions.append(f"{report.display_name}: conjunto excluído")
            continue
        folder = _unique_component(
            _safe_component(report.display_name, fallback=report.client_id),
            used_folders,
        )
        used_names: set[str] = set()
        report_entries = 0
        for document in report.documents:
            source = _source_path(document, data_root=data_root)
            raw_name = Path(document.name or source.name).name
            safe_name = _safe_component(raw_name, fallback="relatorio.docx")
            if source.suffix.lower() not in _ALLOWED_DOCUMENT_SUFFIXES:
                omissions.append(
                    f"{report.display_name} / {safe_name}: formato não suportado"
                )
                continue
            if not source.is_file():
                omissions.append(
                    f"{report.display_name} / {safe_name}: arquivo ausente no disco"
                )
                continue
            archive_name = _unique_filename(safe_name, used_names)
            entries.append((source, f"{root_name}/{folder}/{archive_name}"))
            report_entries += 1
        if report_entries:
            included_reports.append(report)
        else:
            omissions.append(f"{report.display_name}: nenhum documento disponível")

    if not entries:
        raise EmptyReportArchiveError(
            "Nenhum documento disponível para gerar o arquivo ZIP."
        )

    temporary_root.mkdir(parents=True, exist_ok=True)
    estimated_bytes = sum(source.stat().st_size for source, _ in entries)
    if shutil.disk_usage(temporary_root).free < estimated_bytes + _SPACE_RESERVE_BYTES:
        raise InsufficientReportArchiveSpace(
            "Espaço insuficiente para montar temporariamente o arquivo ZIP."
        )

    descriptor, raw_path = tempfile.mkstemp(
        prefix="tenable-reports-",
        suffix=".zip",
        dir=temporary_root,
    )
    os.close(descriptor)
    archive_path = Path(raw_path)
    try:
        with zipfile.ZipFile(
            archive_path,
            "w",
            compression=zipfile.ZIP_STORED,
            allowZip64=True,
        ) as package:
            for source, archive_name in entries:
                package.write(source, archive_name)
            package.writestr(
                f"{root_name}/RESUMO.txt",
                _summary_text(
                    period_id=period_id,
                    reports=included_reports,
                    omissions=omissions,
                    included_documents=len(entries),
                ).encode("utf-8"),
            )
    except Exception:
        archive_path.unlink(missing_ok=True)
        raise

    return ReportArchiveResult(
        path=archive_path,
        download_name=download_name,
        included_clients=len(included_reports),
        included_documents=len(entries),
        omissions=tuple(omissions),
    )


def build_report_set_archive(
    *,
    data_root: str | Path,
    temporary_root: str | Path,
    report: ArchiveReportSet,
) -> ReportArchiveResult:
    client_name = _download_component(
        report.display_name,
        fallback=report.client_id,
    )
    period_id = _safe_component(report.period_id, fallback="periodo")
    return _build_archive(
        data_root=Path(data_root),
        temporary_root=Path(temporary_root),
        period_id=period_id,
        reports=(report,),
        initial_omissions=(),
        download_name=f"{client_name}-Relatorios-Tenable-{period_id}.zip",
    )


def build_monthly_report_archive(
    *,
    data_root: str | Path,
    temporary_root: str | Path,
    period_id: str,
    clients: Sequence[ArchiveClient],
) -> ReportArchiveResult:
    if not _MONTH_PATTERN.fullmatch(period_id):
        raise ValueError("O período mensal deve usar o formato AAAA-MM.")
    selected: list[ArchiveReportSet] = []
    omissions: list[str] = []
    for client in clients:
        candidates = [
            report
            for report in client.reports
            if report.period_id == period_id and report.is_main and not report.deleted
        ]
        if not candidates:
            omissions.append(
                f"{client.display_name}: sem conjunto MAIN para {period_id}"
            )
            continue
        selected.append(candidates[0])
        if len(candidates) > 1:
            omissions.append(
                f"{client.display_name}: mais de um MAIN encontrado; usado {candidates[0].run_id}"
            )
    return _build_archive(
        data_root=Path(data_root),
        temporary_root=Path(temporary_root),
        period_id=period_id,
        reports=selected,
        initial_omissions=omissions,
        download_name=f"Relatorios-Tenable-{period_id}.zip",
    )
