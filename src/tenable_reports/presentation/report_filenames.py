from __future__ import annotations

import re
from datetime import timedelta
from zoneinfo import ZoneInfo

from tenable_reports.domain.reporting import ReportingPeriod


MONTHS_PT = ("JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ")
WINDOWS_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')


def _month_year(value) -> str:
    return f"{MONTHS_PT[value.month - 1]}{value.year % 100:02d}"


def period_suffix(period: ReportingPeriod) -> str:
    zone = ZoneInfo(period.timezone)
    start = period.start_at.astimezone(zone)
    exclusive_end = period.end_at.astimezone(zone)
    inclusive_end = exclusive_end - timedelta(microseconds=1)
    if start.day == 1 and exclusive_end.day == 1:
        first, last = _month_year(start), _month_year(inclusive_end)
        return first if first == last else f"{first}-{last}"
    return (
        f"{start.day:02d}{_month_year(start)}-"
        f"{inclusive_end.day:02d}{_month_year(inclusive_end)}"
    )


def _safe_windows_filename(value: str) -> str:
    sanitized = WINDOWS_INVALID.sub("", value)
    sanitized = " ".join(sanitized.split()).strip(" .")
    return sanitized or "Relatório Tenable"


def report_filename(
    display_name: str, period: ReportingPeriod, kind: str
) -> str:
    titles = {
        "base": "Relatório de Vulnerabilidades Tenable",
        "custom": "Inteligência e Customizações Tenable",
    }
    if kind not in titles:
        raise ValueError("kind deve ser base ou custom.")
    return _safe_windows_filename(
        f"[{display_name}] {titles[kind]} {period_suffix(period)}.docx"
    )
