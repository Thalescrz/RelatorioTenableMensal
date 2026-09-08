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
from typing import Callable, Mapping, Sequence


ArchiveProgressCallback = Callable[[Mapping[str, object]], None]


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
    main_set_at: str | None = None


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
_ALLOWED_DOCUMENT_SUFFIXES = {".docx", ".pdf"}
_SPACE_RESERVE_BYTES = 16 * 1024 * 1024


def _emit_progress(
    callback: ArchiveProgressCallback | None,
    *,
    stage: str,
    message: str,
    progress_percent: int,
    completed_items: int,
    total_items: int,
) -> None:
    if callback is None:
        return
    try:
        callback({
            "stage": stage,
            "message": message,
            "progress_percent": max(0, min(99, int(progress_percent))),
            "completed_items": max(0, int(completed_items)),
            "total_items": max(0, int(total_items)),
        })
    except Exception:
        # Observabilidade nunca deve impedir a criação do pacote.
        return


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


def _validate_month(period_id: str) -> None:
    try:
        parsed = datetime.strptime(period_id, "%Y-%m")
    except ValueError as exc:
        raise ValueError("O período mensal deve usar o formato AAAA-MM.") from exc
    if parsed.strftime("%Y-%m") != period_id:
        raise ValueError("O período mensal deve usar o formato AAAA-MM.")


def _main_selection_key(report: ArchiveReportSet) -> tuple[datetime, str]:
    try:
        selected_at = datetime.fromisoformat(
            str(report.main_set_at or "").replace("Z", "+00:00")
        )
        if selected_at.tzinfo is None:
            selected_at = selected_at.replace(tzinfo=UTC)
        selected_at = selected_at.astimezone(UTC)
    except ValueError:
        selected_at = datetime.min.replace(tzinfo=UTC)
    return selected_at, report.run_id


def _build_archive(
    *,
    data_root: Path,
    temporary_root: Path,
    period_id: str,
    reports: Sequence[ArchiveReportSet],
    initial_omissions: Sequence[str],
    download_name: str,
    allow_empty: bool = False,
    progress_callback: ArchiveProgressCallback | None = None,
) -> ReportArchiveResult:
    root_name = _safe_component(
        f"Relatorios-Tenable-{period_id}",
        fallback="Relatorios-Tenable",
    )
    omissions = list(initial_omissions)
    entries: list[tuple[Path, str]] = []
    included_reports: list[ArchiveReportSet] = []
    used_folders: set[str] = set()

    report_total = len(reports)
    _emit_progress(
        progress_callback,
        stage="SCANNING_DOCUMENTS",
        message="Verificando documentos disponíveis",
        progress_percent=25,
        completed_items=0,
        total_items=report_total,
    )
    for report_index, report in enumerate(reports, start=1):
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
        _emit_progress(
            progress_callback,
            stage="SCANNING_DOCUMENTS",
            message="Verificando documentos disponíveis",
            progress_percent=25 + round(15 * report_index / max(1, report_total)),
            completed_items=report_index,
            total_items=report_total,
        )

    if not entries and not allow_empty:
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
        entry_total = len(entries)
        _emit_progress(
            progress_callback,
            stage="BUILDING_ARCHIVE",
            message="Montando arquivo ZIP",
            progress_percent=45,
            completed_items=0,
            total_items=entry_total,
        )
        with zipfile.ZipFile(
            archive_path,
            "w",
            compression=zipfile.ZIP_STORED,
            allowZip64=True,
        ) as package:
            for entry_index, (source, archive_name) in enumerate(entries, start=1):
                package.write(source, archive_name)
                _emit_progress(
                    progress_callback,
                    stage="BUILDING_ARCHIVE",
                    message="Montando arquivo ZIP",
                    progress_percent=(
                        45 + round(45 * entry_index / max(1, entry_total))
                    ),
                    completed_items=entry_index,
                    total_items=entry_total,
                )
            package.writestr(
                f"{root_name}/RESUMO.txt",
                _summary_text(
                    period_id=period_id,
                    reports=included_reports,
                    omissions=omissions,
                    included_documents=len(entries),
                ).encode("utf-8"),
            )
        _emit_progress(
            progress_callback,
            stage="FINALIZING",
            message="Finalizando arquivo ZIP",
            progress_percent=98,
            completed_items=entry_total,
            total_items=entry_total,
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
    progress_callback: ArchiveProgressCallback | None = None,
) -> ReportArchiveResult:
    client_name = _download_component(
        report.display_name,
        fallback=report.client_id,
    )
    period_id = _safe_component(report.period_id, fallback="periodo")
    _emit_progress(
        progress_callback,
        stage="SELECTING_REPORTS",
        message="Selecionando conjunto de relatórios",
        progress_percent=20,
        completed_items=1,
        total_items=1,
    )
    return _build_archive(
        data_root=Path(data_root),
        temporary_root=Path(temporary_root),
        period_id=period_id,
        reports=(report,),
        initial_omissions=(),
        download_name=f"{client_name}-Relatorios-Tenable-{period_id}.zip",
        progress_callback=progress_callback,
    )


def build_monthly_report_archive(
    *,
    data_root: str | Path,
    temporary_root: str | Path,
    period_id: str,
    clients: Sequence[ArchiveClient],
    download_scope: str | None = None,
    progress_callback: ArchiveProgressCallback | None = None,
) -> ReportArchiveResult:
    _validate_month(period_id)
    selected: list[ArchiveReportSet] = []
    omissions: list[str] = []
    client_total = len(clients)
    _emit_progress(
        progress_callback,
        stage="SELECTING_REPORTS",
        message="Selecionando relatórios MAIN",
        progress_percent=5,
        completed_items=0,
        total_items=client_total,
    )
    for client_index, client in enumerate(clients, start=1):
        candidates = [
            report
            for report in client.reports
            if report.period_id == period_id and report.is_main and not report.deleted
        ]
        if not candidates:
            omissions.append(
                f"{client.display_name}: sem conjunto MAIN para {period_id}"
            )
        else:
            candidates.sort(key=_main_selection_key, reverse=True)
            selected.append(candidates[0])
            if len(candidates) > 1:
                omissions.append(
                    f"{client.display_name}: mais de um MAIN encontrado; "
                    f"usado {candidates[0].run_id} por ser a promoção MAIN mais recente"
                )
        _emit_progress(
            progress_callback,
            stage="SELECTING_REPORTS",
            message="Selecionando relatórios MAIN",
            progress_percent=5 + round(15 * client_index / max(1, client_total)),
            completed_items=client_index,
            total_items=client_total,
        )
    if not clients:
        _emit_progress(
            progress_callback,
            stage="SELECTING_REPORTS",
            message="Selecionando relatórios MAIN",
            progress_percent=20,
            completed_items=0,
            total_items=0,
        )
    scope = (
        f"-{_download_component(download_scope, fallback='Responsavel')}"
        if str(download_scope or "").strip()
        else ""
    )
    return _build_archive(
        data_root=Path(data_root),
        temporary_root=Path(temporary_root),
        period_id=period_id,
        reports=selected,
        initial_omissions=omissions,
        download_name=f"Relatorios-Tenable{scope}-{period_id}.zip",
        allow_empty=True,
        progress_callback=progress_callback,
    )
