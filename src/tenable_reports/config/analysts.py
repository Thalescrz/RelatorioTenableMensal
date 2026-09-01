from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CATALOG_SCHEMA_VERSION = 1


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class AnalystRecord:
    analyst_id: str
    display_name: str
    active: bool
    created_at: datetime
    updated_at: datetime


class AnalystCatalog:
    def __init__(
        self,
        path: Path,
        *,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self.path = Path(path)
        self._now = now

    def list(self) -> Sequence[AnalystRecord]:
        return tuple(
            sorted(
                self._load(),
                key=lambda record: (record.display_name.casefold(), record.analyst_id),
            )
        )

    def get(self, analyst_id: str) -> AnalystRecord | None:
        normalized_id = str(analyst_id).strip()
        return next(
            (record for record in self._load() if record.analyst_id == normalized_id),
            None,
        )

    def create(self, *, display_name: str) -> AnalystRecord:
        normalized_name = _normalize_display_name(display_name)
        records = self._load()
        _ensure_unique_name(records, normalized_name)
        timestamp = _normalize_timestamp(self._now())
        created = AnalystRecord(
            analyst_id=uuid.uuid4().hex,
            display_name=normalized_name,
            active=True,
            created_at=timestamp,
            updated_at=timestamp,
        )
        self._write((*records, created))
        return created

    def update(
        self,
        analyst_id: str,
        *,
        display_name: str,
        active: bool,
    ) -> AnalystRecord:
        normalized_id = str(analyst_id).strip()
        normalized_name = _normalize_display_name(display_name)
        records = self._load()
        current = _require_record(records, normalized_id)
        _ensure_unique_name(records, normalized_name, excluding_id=normalized_id)
        updated = replace(
            current,
            display_name=normalized_name,
            active=bool(active),
            updated_at=_normalize_timestamp(self._now()),
        )
        self._write(
            tuple(updated if record.analyst_id == normalized_id else record for record in records)
        )
        return updated

    def deactivate(self, analyst_id: str) -> AnalystRecord:
        current = self.get(analyst_id)
        if current is None:
            raise ValueError("Analista não encontrado.")
        return self.update(
            current.analyst_id,
            display_name=current.display_name,
            active=False,
        )

    def delete(
        self,
        analyst_id: str,
        *,
        is_in_use: Callable[[str], bool],
    ) -> None:
        normalized_id = str(analyst_id).strip()
        records = self._load()
        _require_record(records, normalized_id)
        if is_in_use(normalized_id):
            raise ValueError("O analista está em uso e não pode ser excluído.")
        self._write(tuple(record for record in records if record.analyst_id != normalized_id))

    def _load(self) -> tuple[AnalystRecord, ...]:
        if not self.path.exists():
            return ()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise TypeError
            if payload.get("schema_version") != CATALOG_SCHEMA_VERSION:
                raise TypeError
            items = payload.get("analysts")
            if not isinstance(items, list):
                raise TypeError
            records = tuple(_record_from_dict(item) for item in items)
            if len({record.analyst_id for record in records}) != len(records):
                raise TypeError
            folded_names = {record.display_name.casefold() for record in records}
            if len(folded_names) != len(records):
                raise TypeError
            return records
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ValueError("Catálogo de analistas inválido.") from exc

    def _write(self, records: Sequence[AnalystRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        payload = {
            "schema_version": CATALOG_SCHEMA_VERSION,
            "analysts": [_record_to_dict(record) for record in records],
        }
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)


def _normalize_display_name(display_name: str) -> str:
    normalized = " ".join(str(display_name).split())
    if not normalized:
        raise ValueError("O nome do analista não pode ser vazio.")
    return normalized


def _normalize_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("O relógio do catálogo deve retornar data com fuso horário.")
    return value.astimezone(timezone.utc)


def _ensure_unique_name(
    records: Sequence[AnalystRecord],
    display_name: str,
    *,
    excluding_id: str | None = None,
) -> None:
    normalized = display_name.casefold()
    if any(
        record.analyst_id != excluding_id and record.display_name.casefold() == normalized
        for record in records
    ):
        raise ValueError("Já existe um analista com esse nome.")


def _require_record(
    records: Sequence[AnalystRecord],
    analyst_id: str,
) -> AnalystRecord:
    record = next((item for item in records if item.analyst_id == analyst_id), None)
    if record is None:
        raise ValueError("Analista não encontrado.")
    return record


def _record_to_dict(record: AnalystRecord) -> dict[str, Any]:
    return {
        "analyst_id": record.analyst_id,
        "display_name": record.display_name,
        "active": record.active,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


def _record_from_dict(payload: object) -> AnalystRecord:
    if not isinstance(payload, dict):
        raise TypeError
    analyst_id = payload.get("analyst_id")
    display_name = payload.get("display_name")
    active = payload.get("active")
    created_at = payload.get("created_at")
    updated_at = payload.get("updated_at")
    if not isinstance(analyst_id, str) or not analyst_id.strip():
        raise TypeError
    if not isinstance(display_name, str):
        raise TypeError
    if not isinstance(active, bool):
        raise TypeError
    if not isinstance(created_at, str) or not isinstance(updated_at, str):
        raise TypeError
    normalized_name = _normalize_display_name(display_name)
    if normalized_name != display_name:
        raise TypeError
    return AnalystRecord(
        analyst_id=analyst_id,
        display_name=normalized_name,
        active=active,
        created_at=_normalize_timestamp(datetime.fromisoformat(created_at)),
        updated_at=_normalize_timestamp(datetime.fromisoformat(updated_at)),
    )
