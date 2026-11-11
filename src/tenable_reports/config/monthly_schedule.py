from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping


_TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


@dataclass(frozen=True, slots=True)
class MonthlyScheduleConfig:
    enabled: bool = False
    day_of_month: int = 1
    local_start_time: str = "00:05"
    task_name: str = "Relatorios Tenable - Mensal"
    selected_client_ids: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if self.day_of_month != 1:
            raise ValueError("A automação mensal deve executar no dia 1.")
        if not _TIME_PATTERN.fullmatch(self.local_start_time):
            raise ValueError("Horário inválido; use HH:MM entre 00:00 e 23:59.")
        if not self.task_name.strip() or len(self.task_name) > 120:
            raise ValueError("Nome da tarefa mensal inválido.")
        selected = self.selected_client_ids
        if selected is not None:
            if not isinstance(selected, (list, tuple)) or any(
                not isinstance(item, str) or not item.strip() for item in selected
            ):
                raise ValueError("A seleção de clientes deve ser uma lista de IDs não vazios.")
            object.__setattr__(
                self, "selected_client_ids", tuple(dict.fromkeys(item.strip() for item in selected))
            )

    def eligible_client_ids(self, clients: Iterable[Mapping[str, Any]]) -> list[str]:
        selected = None if self.selected_client_ids is None else set(self.selected_client_ids)
        return [
            row["client_id"]
            for row in clients
            if row.get("enabled") and row.get("credentials_ready")
            and (selected is None or row["client_id"] in selected)
        ]

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any] | None) -> "MonthlyScheduleConfig":
        data = dict(values or {})
        return cls(
            enabled=bool(data.get("enabled", False)),
            day_of_month=int(data.get("day_of_month", 1)),
            local_start_time=str(data.get("local_start_time") or "00:05").strip(),
            task_name=str(data.get("task_name") or "Relatorios Tenable - Mensal").strip(),
            selected_client_ids=data.get("selected_client_ids"),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "day_of_month": self.day_of_month,
            "local_start_time": self.local_start_time,
            "task_name": self.task_name,
            "selected_client_ids": (
                None if self.selected_client_ids is None else list(self.selected_client_ids)
            ),
        }
