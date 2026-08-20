from __future__ import annotations

import re
from datetime import datetime, timedelta
from collections.abc import Sequence
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from docx.shared import Pt, RGBColor


def _safe(value: Any, *, limit: int = 140) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or ""))
    text = " ".join(text.split())
    return text[:limit]


def _entry(
    dataset: Mapping[str, Any], table_id: str, *, tag_uuid: str | None = None
) -> Mapping[str, Any] | None:
    provenance = dataset.get("table_provenance")
    if not isinstance(provenance, Mapping):
        return None
    tables = provenance.get("tables")
    if not isinstance(tables, Mapping):
        return None
    value = tables.get(table_id)
    if isinstance(value, Mapping):
        return value
    if isinstance(value, list):
        for item in value:
            if isinstance(item, Mapping) and (
                tag_uuid is None
                or str(item.get("tag_uuid") or "") == str(tag_uuid or "")
            ):
                return item
    return None


def format_source_filter_note(
    dataset: Mapping[str, Any],
    table_id: str,
    *,
    tag_uuid: str | None = None,
    extra_filters: Mapping[str, Any] | None = None,
    period_label: str | None = None,
    period_labels: Sequence[str] | None = None,
) -> str | None:
    item = _entry(dataset, table_id, tag_uuid=tag_uuid)
    if item is None or item.get("platform_validation_available") is False:
        return None
    parts: list[str] = []
    view = _safe(item.get("view"))
    if view:
        parts.append(view)
    source = _safe(item.get("source"))
    if source and not view:
        parts.append(f"fonte {source}")
    state_labels = {
        "OPEN": ("Active", "New"),
        "REOPENED": ("Resurfaced",),
        "FIXED": ("Fixed",),
    }

    def state_part(value: Mapping[str, Any]) -> str | None:
        states: list[str] = []
        for raw_state in value.get("states") or ():
            normalized = _safe(raw_state).upper()
            for label in state_labels.get(normalized, (_safe(raw_state),)):
                if label and label not in states:
                    states.append(label)
        return "State = " + ", ".join(states) if states else None

    def date_parts(value: Mapping[str, Any], label: str | None) -> list[str]:
        fields = [
            _safe(field)
            for field in value.get("date_fields") or ()
            if _safe(field)
        ]
        if not fields:
            field = _safe(value.get("date_field"))
            if field:
                fields = [field]
        safe_label = _safe(label)
        if safe_label:
            return (
                [f"{field} = {safe_label}" for field in fields]
                if fields
                else [f"Período = {safe_label}"]
            )
        start, end = _display_period(value)
        if not start or not end:
            return []
        return (
            [f"{field} = {start} a {end}" for field in fields]
            if fields
            else [f"Período = {start} a {end}"]
        )

    validation_queries = item.get("validation_queries")
    if isinstance(validation_queries, list) and validation_queries:
        query_texts: list[str] = []
        for index, query in enumerate(validation_queries):
            if not isinstance(query, Mapping):
                continue
            merged = {**item, **query}
            query_parts: list[str] = []
            state = state_part(merged)
            if state:
                query_parts.append(state)
            query_period = (
                period_labels[index]
                if period_labels is not None and index < len(period_labels)
                else period_label
            )
            query_parts.extend(date_parts(merged, query_period))
            for key, value in (query.get("platform_filters") or {}).items():
                safe_key, safe_value = _safe(key), _safe(value)
                if safe_key and safe_value:
                    query_parts.append(f"{safe_key} = {safe_value}")
            query_label = _safe(query.get("label"))
            if query_parts:
                query_text = "; ".join(query_parts)
                query_texts.append(
                    f"{query_label}: {query_text}" if query_label else query_text
                )
        if query_texts:
            parts.append(" | ".join(query_texts))
    else:
        state = state_part(item)
        if state:
            parts.append(state)
    severities = [
        _safe(value).title() for value in item.get("severities") or () if _safe(value)
    ]
    if severities:
        parts.append("Severity = " + ", ".join(severities))
    if not (isinstance(validation_queries, list) and validation_queries):
        parts.extend(date_parts(item, period_label))
    if item.get("tag_category") and item.get("tag_value"):
        category, value = _safe(item.get("tag_category")), _safe(item.get("tag_value"))
        if category or value:
            parts.append(f"Tag = {category}:{value}".strip(":"))
    for key, value in (extra_filters or {}).items():
        safe_key, safe_value = _safe(key), _safe(value)
        if safe_key and safe_value:
            parts.append(f"{safe_key} = {safe_value}")
    for key, value in (item.get("platform_filters") or {}).items():
        safe_key, safe_value = _safe(key), _safe(value)
        if safe_key and safe_value:
            parts.append(f"{safe_key} = {safe_value}")
    group_by = _safe(item.get("group_by"))
    if group_by:
        parts.append(f"Agrupar por {group_by}")
    order_by = _safe(item.get("order_by"))
    if order_by:
        parts.append(f"Ordenar por {order_by}")
    limit = _safe(item.get("limit"))
    if limit:
        parts.append(f"Top {limit}")
    if not parts:
        return None
    note = "Validação rápida na Tenable: " + "; ".join(parts) + "."
    rule = _safe(item.get("rule"), limit=260)
    if rule:
        note += f" Regra: {rule}."
    return note


def _display_period(item: Mapping[str, Any]) -> tuple[str, str]:
    raw_start = str(item.get("period_start_at") or "").strip()
    raw_end = str(item.get("period_end_at") or "").strip()
    if not raw_start or not raw_end:
        return "", ""
    try:
        start = datetime.fromisoformat(raw_start.replace("Z", "+00:00"))
        end = datetime.fromisoformat(raw_end.replace("Z", "+00:00"))
        timezone_name = str(item.get("timezone") or "").strip()
        if timezone_name:
            timezone = ZoneInfo(timezone_name)
            start = start.astimezone(timezone)
            end = end.astimezone(timezone)
        end -= timedelta(microseconds=1)
    except (ValueError, ZoneInfoNotFoundError):
        return _safe(raw_start), _safe(raw_end)
    return start.strftime("%d/%m/%Y"), end.strftime("%d/%m/%Y")


def add_source_filter_note(
    document: Any,
    dataset: Mapping[str, Any],
    table_id: str,
    *,
    enabled: bool,
    tag_uuid: str | None = None,
    extra_filters: Mapping[str, Any] | None = None,
    period_label: str | None = None,
    period_labels: Sequence[str] | None = None,
) -> None:
    if not enabled:
        return
    note = format_source_filter_note(
        dataset,
        table_id,
        tag_uuid=tag_uuid,
        extra_filters=extra_filters,
        period_label=period_label,
        period_labels=period_labels,
    )
    if not note:
        return
    paragraph = document.add_paragraph(note)
    paragraph.style = document.styles["Normal"]
    paragraph.paragraph_format.space_before = Pt(2)
    paragraph.paragraph_format.space_after = Pt(6)
    if paragraph.runs:
        run = paragraph.runs[0]
        run.italic = False
        run.font.size = Pt(8)
        run.font.color.rgb = RGBColor(0x7A, 0x83, 0x8C)
