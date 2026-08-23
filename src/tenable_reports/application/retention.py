from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Mapping


RUN_CATEGORIES = (
    "raw",
    "snapshots",
    "normalized",
    "report-datasets",
    "reports",
)
TRANSIENT_CATEGORIES = ("raw", "snapshots", "normalized", "report-datasets")
CLEANABLE_CATEGORIES = TRANSIENT_CATEGORIES + ("orchestration",)
FAILED_STATUSES = frozenset({"FAILED", "PARTIAL_FAILURE"})


@dataclass(frozen=True, slots=True)
class RetentionCandidate:
    path: Path
    category: str
    client_id: str
    run_id: str
    last_modified_at: str
    reason: str = "RETENTION_EXPIRED"
    status: str = "UNKNOWN"

    def to_dict(self) -> dict[str, str]:
        return {
            "path": str(self.path.resolve()),
            "category": self.category,
            "client_id": self.client_id,
            "run_id": self.run_id,
            "last_modified_at": self.last_modified_at,
            "reason": self.reason,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    failed_raw_days: int = 7
    successful_raw_days: int = 60
    normalized_days: int = 90
    documents_days: int = 395

    def __post_init__(self) -> None:
        for field_name in (
            "failed_raw_days",
            "successful_raw_days",
            "normalized_days",
            "documents_days",
        ):
            if int(getattr(self, field_name)) < 1:
                raise ValueError(f"{field_name} deve ser maior ou igual a 1.")

    def days_for(self, category: str, *, status: str) -> int:
        if (
            category in TRANSIENT_CATEGORIES
            and status.upper() in FAILED_STATUSES
        ):
            return self.failed_raw_days
        if category == "raw":
            return (
                self.failed_raw_days
                if status.upper() in {"FAILED", "PARTIAL_FAILURE"}
                else self.successful_raw_days
            )
        if category in {"normalized", "snapshots"}:
            return self.normalized_days
        return self.documents_days


@dataclass(frozen=True, slots=True)
class RetentionDecision:
    path: Path
    category: str
    client_id: str
    run_id: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {
            "path": str(self.path.resolve()),
            "category": self.category,
            "client_id": self.client_id,
            "run_id": self.run_id,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class TieredRetentionPlan:
    candidates: tuple[RetentionCandidate, ...]
    skipped: tuple[RetentionDecision, ...]

    def __iter__(self):
        return iter(self.candidates)


@dataclass(frozen=True, slots=True)
class CleanupPlan:
    candidates: tuple[RetentionCandidate, ...]


@dataclass(frozen=True, slots=True)
class CleanupFailure:
    path: Path
    reason: str


@dataclass(frozen=True, slots=True)
class CleanupResult:
    removed: tuple[Path, ...]
    removed_bytes: int
    failures: tuple[CleanupFailure, ...] = ()

    @property
    def status(self) -> str:
        if not self.failures:
            return "COMPLETE"
        return "PARTIAL" if self.removed else "FAILED"


@dataclass(frozen=True, slots=True)
class RetentionGuard:
    root: Path
    history_confirmed_run_ids: frozenset[str]
    main_run_ids: frozenset[str]
    active_run_ids: frozenset[str]
    retry_required_run_ids: frozenset[str]

    def refusal_reason(self, candidate: RetentionCandidate) -> str | None:
        target = candidate.path.resolve()
        try:
            relative = target.relative_to(self.root.resolve())
        except ValueError:
            return "OUTSIDE_ALLOWED_ROOT"
        if (
            len(relative.parts) != 3
            or relative.parts[0] != candidate.category
            or relative.parts[1] != candidate.client_id
            or relative.parts[2] != candidate.run_id
            or candidate.category not in RUN_CATEGORIES
        ):
            return "UNEXPECTED_PATH_SHAPE"
        if candidate.run_id in self.active_run_ids:
            return "RUN_ACTIVE"
        if candidate.run_id in self.retry_required_run_ids:
            return "RETRY_REQUIRED"
        if (
            candidate.category in {"raw", "snapshots", "normalized"}
            and candidate.run_id not in self.history_confirmed_run_ids
            and candidate.status.upper() not in FAILED_STATUSES
        ):
            return "HISTORY_NOT_CONFIRMED"
        if (
            candidate.category in {"reports", "report-datasets"}
            and candidate.run_id in self.main_run_ids
        ):
            return "MAIN_REFERENCE_PROTECTED"
        return None


def plan_tiered_retention(
    *,
    scoped_output_root: str | Path,
    policy: RetentionPolicy,
    run_status: Mapping[str, str] | None = None,
    history_confirmed_run_ids: Iterable[str] = (),
    main_run_ids: Iterable[str] = (),
    active_run_ids: Iterable[str] = (),
    retry_required_run_ids: Iterable[str] = (),
    now: datetime | None = None,
) -> TieredRetentionPlan:
    root = Path(scoped_output_root)
    if not root.exists():
        return TieredRetentionPlan((), ())
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    statuses = dict(run_status or {})
    guard = RetentionGuard(
        root=root,
        history_confirmed_run_ids=frozenset(history_confirmed_run_ids),
        main_run_ids=frozenset(main_run_ids),
        active_run_ids=frozenset(active_run_ids),
        retry_required_run_ids=frozenset(retry_required_run_ids),
    )
    candidates: list[RetentionCandidate] = []
    skipped: list[RetentionDecision] = []
    for category in RUN_CATEGORIES:
        category_root = root / category
        if not category_root.is_dir():
            continue
        for client_dir in sorted(path for path in category_root.iterdir() if path.is_dir()):
            for run_dir in sorted(path for path in client_dir.iterdir() if path.is_dir()):
                modified = datetime.fromtimestamp(run_dir.stat().st_mtime, tz=timezone.utc)
                horizon = policy.days_for(
                    category,
                    status=statuses.get(run_dir.name, "UNKNOWN"),
                )
                if modified >= current.astimezone(timezone.utc) - timedelta(days=horizon):
                    continue
                candidate = RetentionCandidate(
                    path=run_dir,
                    category=category,
                    client_id=client_dir.name,
                    run_id=run_dir.name,
                    last_modified_at=modified.isoformat(),
                    reason=f"EXPIRED_AFTER_{horizon}_DAYS",
                    status=statuses.get(run_dir.name, "UNKNOWN"),
                )
                refusal = guard.refusal_reason(candidate)
                if refusal:
                    skipped.append(RetentionDecision(
                        path=run_dir,
                        category=category,
                        client_id=client_dir.name,
                        run_id=run_dir.name,
                        reason=refusal,
                    ))
                elif category == "reports":
                    skipped.append(RetentionDecision(
                        path=run_dir,
                        category=category,
                        client_id=client_dir.name,
                        run_id=run_dir.name,
                        reason="DOCUMENTS_REQUIRE_EXPLICIT_DELETE",
                    ))
                else:
                    candidates.append(candidate)
    return TieredRetentionPlan(tuple(candidates), tuple(skipped))


def plan_retention(
    *,
    scoped_output_root: str | Path,
    retention_days: int | None,
    protected_run_ids: Iterable[str] = (),
    now: datetime | None = None,
) -> tuple[RetentionCandidate, ...]:
    if retention_days is None:
        return ()
    if retention_days < 1:
        raise ValueError("retention_days deve ser maior ou igual a 1.")
    root = Path(scoped_output_root)
    if not root.exists():
        return ()
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    cutoff = current.astimezone(timezone.utc) - timedelta(days=retention_days)
    protected = set(protected_run_ids)
    candidates: list[RetentionCandidate] = []
    for category in RUN_CATEGORIES:
        category_root = root / category
        if not category_root.is_dir():
            continue
        for client_dir in sorted(path for path in category_root.iterdir() if path.is_dir()):
            for run_dir in sorted(path for path in client_dir.iterdir() if path.is_dir()):
                if run_dir.name in protected:
                    continue
                modified = datetime.fromtimestamp(
                    run_dir.stat().st_mtime,
                    tz=timezone.utc,
                )
                if modified >= cutoff:
                    continue
                candidates.append(
                    RetentionCandidate(
                        path=run_dir,
                        category=category,
                        client_id=client_dir.name,
                        run_id=run_dir.name,
                        last_modified_at=modified.isoformat(),
                    )
                )
    return tuple(candidates)


def _safe_component(value: str, *, label: str) -> str:
    component = str(value).strip()
    if (
        not component
        or component in {".", ".."}
        or Path(component).name != component
        or "/" in component
        or "\\" in component
    ):
        raise ValueError(f"{label} inválido para limpeza.")
    return component


def plan_published_run_cleanup(
    *,
    scoped_output_root: str | Path,
    client_id: str,
    run_id: str,
    publication_confirmed: bool,
    history_confirmed: bool,
    compact_snapshot_confirmed: bool = True,
) -> CleanupPlan:
    if not publication_confirmed or not history_confirmed:
        raise ValueError("Publicação e histórico precisam estar confirmados.")
    if not compact_snapshot_confirmed:
        raise ValueError("O snapshot compacto precisa estar confirmado antes da limpeza.")
    client = _safe_component(client_id, label="client_id")
    run = _safe_component(run_id, label="run_id")
    root = Path(scoped_output_root).resolve()
    candidates: list[RetentionCandidate] = []
    for category in TRANSIENT_CATEGORIES:
        target = (root / category / client / run).resolve()
        try:
            relative = target.relative_to(root)
        except ValueError as exc:
            raise ValueError("Limpeza recusada fora da raiz permitida.") from exc
        if relative.parts != (category, client, run):
            raise ValueError("Limpeza recusada para caminho inesperado.")
        if target.is_dir():
            modified = datetime.fromtimestamp(target.stat().st_mtime, tz=timezone.utc)
            candidates.append(RetentionCandidate(
                path=target,
                category=category,
                client_id=client,
                run_id=run,
                last_modified_at=modified.isoformat(),
                reason="PUBLISHED_RUN_TRANSIENT",
                status="COMPLETE",
            ))
    return CleanupPlan(tuple(candidates))


def plan_orchestration_log_cleanup(
    *,
    scoped_output_root: str | Path,
    retention_days: int,
    now: datetime | None = None,
) -> CleanupPlan:
    if retention_days < 1:
        raise ValueError("retention_days deve ser maior ou igual a 1.")
    root = Path(scoped_output_root).resolve()
    orchestration_root = root / "orchestration"
    if not orchestration_root.is_dir():
        return CleanupPlan(())
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    cutoff = current.astimezone(timezone.utc) - timedelta(days=retention_days)
    candidates: list[RetentionCandidate] = []
    for orchestration_dir in sorted(
        path for path in orchestration_root.iterdir() if path.is_dir()
    ):
        for run_dir in sorted(path for path in orchestration_dir.iterdir() if path.is_dir()):
            modified = datetime.fromtimestamp(run_dir.stat().st_mtime, tz=timezone.utc)
            if modified >= cutoff:
                continue
            candidates.append(RetentionCandidate(
                path=run_dir,
                category="orchestration",
                client_id=orchestration_dir.name,
                run_id=run_dir.name,
                last_modified_at=modified.isoformat(),
                reason=f"LOGS_EXPIRED_AFTER_{retention_days}_DAYS",
                status="COMPLETE",
            ))
    return CleanupPlan(tuple(candidates))


def _directory_bytes(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            try:
                total += item.stat().st_size
            except OSError:
                continue
    return total


def apply_cleanup_plan(
    *,
    scoped_output_root: str | Path,
    candidates: Iterable[RetentionCandidate],
) -> CleanupResult:
    root = Path(scoped_output_root).resolve()
    removed: list[Path] = []
    failures: list[CleanupFailure] = []
    removed_bytes = 0
    for candidate in candidates:
        target = candidate.path.resolve()
        try:
            relative = target.relative_to(root)
        except ValueError as exc:
            raise ValueError("Limpeza recusada fora da raiz permitida.") from exc
        if (
            relative.parts != (
                candidate.category,
                candidate.client_id,
                candidate.run_id,
            )
            or candidate.category not in CLEANABLE_CATEGORIES
        ):
            raise ValueError("Limpeza recusada para caminho inesperado.")
        if not target.is_dir():
            continue
        size = _directory_bytes(target)
        try:
            shutil.rmtree(target)
        except OSError:
            failures.append(CleanupFailure(target, "REMOVE_FAILED"))
            continue
        removed.append(target)
        removed_bytes += size
    return CleanupResult(tuple(removed), removed_bytes, tuple(failures))


def apply_retention(
    *,
    scoped_output_root: str | Path,
    candidates: Iterable[RetentionCandidate],
) -> tuple[Path, ...]:
    root = Path(scoped_output_root).resolve()
    removed: list[Path] = []
    for candidate in candidates:
        target = candidate.path.resolve()
        try:
            relative = target.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"Retencao recusada fora da raiz permitida: {target}") from exc
        parts = relative.parts
        if len(parts) != 3 or parts[0] not in RUN_CATEGORIES:
            raise ValueError(f"Retencao recusada para caminho inesperado: {target}")
        if not target.is_dir():
            continue
        shutil.rmtree(target)
        removed.append(target)
    return tuple(removed)
