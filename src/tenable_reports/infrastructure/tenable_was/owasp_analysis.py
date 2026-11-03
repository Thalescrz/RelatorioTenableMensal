from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from tenable_reports.infrastructure.tenable_vm.client import ApiError

from .client import TenableWasClient


ANALYSIS_PATH = "/api/v3/findings/vulnerabilities/webapp/analysis"
MAX_ANALYSIS_PAGES = 1000
MAX_ANALYSIS_ROWS = 100000
CATEGORY_PATTERN = re.compile(r"^A(0[1-9]|10)$")
SEVERITY_RANK = {
    "INFO": 0,
    "LOW": 1,
    "MEDIUM": 2,
    "HIGH": 3,
    "CRITICAL": 4,
}
SEVERITY_BY_VALUE = {
    0: "INFO",
    1: "LOW",
    2: "MEDIUM",
    3: "HIGH",
    4: "CRITICAL",
}


@dataclass(frozen=True, slots=True)
class Owasp2025PluginGroup:
    category: str
    plugin_id: int
    name: str | None
    family: str | None
    severity: str
    vpr_score: float | None
    instances: int


def _iso_milliseconds(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("O periodo OWASP deve possuir fuso horario.")
    utc_value = value.astimezone(timezone.utc)
    return utc_value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _payload(
    *,
    category: str,
    start_at: datetime,
    end_at: datetime,
    cursor: str | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "filter": {
            "and": [
                {"property": "state", "operator": "neq", "value": ["FIXED"]},
                {"property": "severity", "operator": "neq", "value": [0]},
                {
                    "property": "risk_modified",
                    "operator": "neq",
                    "value": ["ACCEPTED"],
                },
                {
                    "property": "owasp_2025",
                    "operator": "eq",
                    "value": [f"A{int(category[1:])}"],
                },
                {
                    "property": "last_seen",
                    "operator": "between",
                    "operatorVariation": "between",
                    "value": [
                        _iso_milliseconds(start_at),
                        _iso_milliseconds(end_at - timedelta(milliseconds=1)),
                    ],
                },
            ]
        },
        "template": "group",
        "parameters": {
            "group_by": "definition.id",
            "stats": [
                "max(severity)",
                "first(definition.name)",
                "first(definition.family)",
                "first(definition.vpr.score)",
                "first(definition.id)",
                "count",
            ],
            "sort": [
                {"property": "first(definition.vpr.score)", "order": "desc"}
            ],
        },
    }
    if cursor is not None:
        payload["next"] = cursor
    return payload


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_group(category: str, value: Any) -> Owasp2025PluginGroup:
    if not isinstance(value, Mapping):
        raise ApiError("Resposta da analise OWASP 2025 possui grupo invalido.")
    statistics = value.get("stats")
    if value.get("field") != "definition.id" or not isinstance(statistics, Mapping):
        raise ApiError("Resposta da analise OWASP 2025 possui grupo invalido.")
    try:
        plugin_id = int(value["value"])
        reported_plugin_id = int(statistics["first(definition.id)"])
        instances = int(statistics["count"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ApiError("Resposta da analise OWASP 2025 possui grupo invalido.") from exc
    if plugin_id <= 0 or reported_plugin_id != plugin_id or instances < 0:
        raise ApiError("Resposta da analise OWASP 2025 possui grupo invalido.")

    try:
        severity = SEVERITY_BY_VALUE[int(statistics["max(severity)"])]
    except (KeyError, TypeError, ValueError) as exc:
        raise ApiError("Resposta da analise OWASP 2025 possui severidade invalida.")
    raw_vpr = statistics.get("first(definition.vpr.score)")
    if raw_vpr is None:
        vpr_score = None
    else:
        try:
            vpr_score = float(raw_vpr)
        except (TypeError, ValueError) as exc:
            raise ApiError("Resposta da analise OWASP 2025 possui VPR invalido.") from exc
        if not math.isfinite(vpr_score) or vpr_score < 0:
            raise ApiError("Resposta da analise OWASP 2025 possui VPR invalido.")
    return Owasp2025PluginGroup(
        category=category,
        plugin_id=plugin_id,
        name=_optional_text(statistics.get("first(definition.name)")),
        family=_optional_text(statistics.get("first(definition.family)")),
        severity=severity,
        vpr_score=vpr_score,
        instances=instances,
    )


def _sort_key(item: Owasp2025PluginGroup) -> tuple[Any, ...]:
    return (
        item.vpr_score is None,
        -(item.vpr_score or 0.0),
        -SEVERITY_RANK[item.severity],
        -item.instances,
        item.plugin_id,
    )


class TenableWasOwaspAnalysisClient:
    def __init__(self, client: TenableWasClient) -> None:
        self._client = client

    def list_category_plugins(
        self,
        *,
        category: str,
        start_at: datetime,
        end_at: datetime,
        page_size: int = 200,
        max_pages: int = MAX_ANALYSIS_PAGES,
        max_rows: int = MAX_ANALYSIS_ROWS,
    ) -> tuple[Owasp2025PluginGroup, ...]:
        normalized_category = str(category or "").strip().upper()
        if not CATEGORY_PATTERN.fullmatch(normalized_category):
            raise ValueError("A categoria OWASP deve estar entre A01 e A10.")
        if start_at.tzinfo is None or end_at.tzinfo is None or start_at >= end_at:
            raise ValueError("O periodo OWASP deve possuir inicio anterior ao fim.")
        if int(page_size) < 1:
            raise ValueError("page_size deve ser positivo.")
        page_budget = max(1, min(int(max_pages), MAX_ANALYSIS_PAGES))
        row_budget = max(1, min(int(max_rows), MAX_ANALYSIS_ROWS))

        rows: list[Owasp2025PluginGroup] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        page_count = 0
        while True:
            page_count += 1
            data = self._client.request(
                "POST",
                ANALYSIS_PATH,
                json_body=_payload(
                    category=normalized_category,
                    start_at=start_at,
                    end_at=end_at,
                    cursor=cursor,
                ),
            ).json()
            if not isinstance(data, Mapping):
                raise ApiError("Resposta da analise OWASP 2025 possui formato invalido.")
            items = data.get("groups")
            if not isinstance(items, list):
                raise ApiError("Resposta da analise OWASP 2025 possui formato invalido.")
            if len(rows) + len(items) > row_budget:
                raise ApiError("Analise OWASP 2025 excedeu o limite de linhas.")
            rows.extend(_parse_group(normalized_category, item) for item in items)
            raw_next = data.get("next")
            if raw_next in (None, ""):
                break
            if page_count >= page_budget:
                raise ApiError("Analise OWASP 2025 excedeu o limite de paginacao.")
            next_cursor = str(raw_next).strip()
            if not next_cursor or next_cursor in seen_cursors:
                raise ApiError("Resposta da analise OWASP 2025 repetiu a paginacao.")
            seen_cursors.add(next_cursor)
            cursor = next_cursor

        return tuple(sorted(rows, key=_sort_key))
