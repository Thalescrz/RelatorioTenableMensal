"""Presentation adapters for generated report artifacts."""

from tenable_reports.presentation.base_report_docx import (
    BaseReportRenderResult,
    create_base_template,
    generate_base_report,
)
from tenable_reports.presentation.full_base_report_docx import (
    FullBaseReportRenderResult,
    generate_full_base_report,
)
from tenable_reports.presentation.tag_report_docx import (
    TagReportRenderResult,
    generate_tag_report,
)

__all__ = [
    "BaseReportRenderResult",
    "create_base_template",
    "generate_base_report",
    "FullBaseReportRenderResult",
    "generate_full_base_report",
    "TagReportRenderResult",
    "generate_tag_report",
]
