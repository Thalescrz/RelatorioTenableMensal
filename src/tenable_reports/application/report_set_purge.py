from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence


class ReportSetPurgeError(RuntimeError):
    """Base error for permanent report-set deletion."""


class MainReportReplacementRequired(ReportSetPurgeError):
    pass


class ActiveReportSetError(ReportSetPurgeError):
    pass


class UnsafeReportSetPath(ReportSetPurgeError):
    pass


class ReportSetPurgeFinalizationError(ReportSetPurgeError):
    pass


@dataclass(frozen=True, slots=True)
class ReportSetPurgeRecord:
    run_id: str
    client_id: str
    period_id: str
    disk_paths: tuple[str, ...]
    document_count: int
    is_main: bool
    compatible_replacement_run_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ReportSetPurgePreview:
    run_id: str
    client_id: str
    period_id: str
    document_count: int
    file_count: int
    total_bytes: int
    is_main: bool
    compatible_replacement_run_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "client_id": self.client_id,
            "period_id": self.period_id,
            "document_count": self.document_count,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "is_main": self.is_main,
            "requires_main_gap_confirmation": (
                self.is_main and not self.compatible_replacement_run_ids
            ),
            "compatible_replacement_run_ids": list(
                self.compatible_replacement_run_ids
            ),
        }


@dataclass(frozen=True, slots=True)
class ReportSetPurgeResult:
    run_id: str
    deleted_files: int
    deleted_bytes: int
    replacement_run_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "deleted": True,
            "deleted_files": self.deleted_files,
            "deleted_bytes": self.deleted_bytes,
            "replacement_run_id": self.replacement_run_id,
        }


class ReportSetPurgeRepository(Protocol):
    def describe(self, run_id: str) -> ReportSetPurgeRecord: ...

    def purge(
        self,
        run_id: str,
        *,
        actor: str,
        reason: str,
        replacement_run_id: str | None,
        allow_main_gap: bool = False,
    ) -> None: ...


def _required(value: str, field: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{field} é obrigatório.")
    return normalized


def _move_file(source: Path, destination: Path) -> None:
    source.replace(destination)


class ReportSetPurgeService:
    def __init__(
        self,
        *,
        data_root: Path,
        repository: ReportSetPurgeRepository,
        active_jobs: Callable[[], Sequence[Mapping[str, Any]]],
        move_file: Callable[[Path, Path], None] = _move_file,
        remove_tree: Callable[[Path], None] = shutil.rmtree,
    ) -> None:
        self.data_root = data_root.resolve()
        self.repository = repository
        self.active_jobs = active_jobs
        self.move_file = move_file
        self.remove_tree = remove_tree

    def _paths(self, record: ReportSetPurgeRecord) -> tuple[Path, ...]:
        paths: list[Path] = []
        seen: set[Path] = set()
        for raw_path in record.disk_paths:
            candidate = Path(raw_path)
            if not candidate.is_absolute():
                candidate = self.data_root / candidate
            resolved = candidate.resolve()
            if not resolved.is_relative_to(self.data_root):
                raise UnsafeReportSetPath(
                    "A exclusão foi bloqueada porque um artefato está fora da pasta data."
                )
            if resolved.exists() and not resolved.is_file():
                raise UnsafeReportSetPath(
                    "A exclusão aceita somente arquivos exatos do conjunto."
                )
            if resolved not in seen:
                paths.append(resolved)
                seen.add(resolved)
        return tuple(paths)

    def preview(self, run_id: str) -> ReportSetPurgePreview:
        record = self.repository.describe(_required(run_id, "run_id"))
        paths = self._paths(record)
        existing = tuple(path for path in paths if path.is_file())
        return ReportSetPurgePreview(
            run_id=record.run_id,
            client_id=record.client_id,
            period_id=record.period_id,
            document_count=record.document_count,
            file_count=len(existing),
            total_bytes=sum(path.stat().st_size for path in existing),
            is_main=record.is_main,
            compatible_replacement_run_ids=(
                record.compatible_replacement_run_ids
            ),
        )

    def _ensure_inactive(self, record: ReportSetPurgeRecord) -> None:
        for job in self.active_jobs():
            if str(job.get("status") or "").upper() not in {"QUEUED", "RUNNING"}:
                continue
            if str(job.get("client_id") or "") == record.client_id:
                raise ActiveReportSetError(
                    "A exclusão foi bloqueada porque este cliente possui uma geração ativa."
                )

    @staticmethod
    def _restore(staged: Sequence[tuple[Path, Path]]) -> None:
        for original, temporary in reversed(staged):
            if not temporary.exists():
                continue
            original.parent.mkdir(parents=True, exist_ok=True)
            temporary.replace(original)

    def purge(
        self,
        run_id: str,
        *,
        actor: str,
        reason: str,
        confirmation: str,
        replacement_run_id: str | None = None,
        allow_main_gap: bool = False,
    ) -> ReportSetPurgeResult:
        if confirmation.strip() != "EXCLUIR":
            raise ValueError('Digite exatamente "EXCLUIR" para confirmar.')
        actor = _required(actor, "actor")
        reason = _required(reason, "reason")
        record = self.repository.describe(_required(run_id, "run_id"))
        self._ensure_inactive(record)
        replacement = str(replacement_run_id or "").strip() or None
        if replacement and allow_main_gap:
            raise ValueError(
                "Escolha uma substituta ou confirme a lacuna de MAIN, não ambos."
            )
        if record.is_main:
            if replacement:
                if replacement not in record.compatible_replacement_run_ids:
                    raise MainReportReplacementRequired(
                        "Selecione uma geração substituta compatível antes de excluir o MAIN."
                    )
            elif record.compatible_replacement_run_ids:
                raise MainReportReplacementRequired(
                    "Selecione uma geração substituta compatível antes de excluir o MAIN."
                )
            elif not allow_main_gap:
                raise MainReportReplacementRequired(
                    "Confirme explicitamente a exclusão do único MAIN deste período."
                )
        elif allow_main_gap:
            raise ValueError(
                "A lacuna de MAIN só pode ser confirmada ao excluir o relatório MAIN."
            )
        paths = self._paths(record)
        existing = tuple(path for path in paths if path.is_file())
        deleted_bytes = sum(path.stat().st_size for path in existing)
        quarantine = (
            self.data_root / ".purge" / f"{record.run_id}-{uuid.uuid4().hex}"
        )
        staged: list[tuple[Path, Path]] = []
        try:
            for original in existing:
                temporary = quarantine / original.relative_to(self.data_root)
                temporary.parent.mkdir(parents=True, exist_ok=True)
                self.move_file(original, temporary)
                staged.append((original, temporary))
            self.repository.purge(
                record.run_id,
                actor=actor,
                reason=reason,
                replacement_run_id=replacement,
                allow_main_gap=allow_main_gap,
            )
        except Exception:
            self._restore(staged)
            if quarantine.exists():
                shutil.rmtree(quarantine, ignore_errors=True)
            raise
        try:
            if quarantine.exists():
                self.remove_tree(quarantine)
        except Exception as exc:
            raise ReportSetPurgeFinalizationError(
                "O banco foi atualizado, mas a limpeza final dos arquivos não terminou."
            ) from exc
        return ReportSetPurgeResult(
            run_id=record.run_id,
            deleted_files=len(existing),
            deleted_bytes=deleted_bytes,
            replacement_run_id=replacement,
        )
