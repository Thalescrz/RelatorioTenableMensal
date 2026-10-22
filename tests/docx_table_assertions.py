from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH


def _iter_table_cells(tables: Iterable[Any]):
    seen_cells: set[Any] = set()
    for table in tables:
        for row in table.rows:
            for cell in row.cells:
                if cell._tc in seen_cells:
                    continue
                seen_cells.add(cell._tc)
                yield cell
                yield from _iter_table_cells(cell.tables)


def assert_all_body_table_cells_centered(document: Any) -> None:
    cells = list(_iter_table_cells(document.tables))
    assert cells, "O documento de teste deve conter tabelas no corpo."
    for cell in cells:
        assert cell.vertical_alignment == WD_CELL_VERTICAL_ALIGNMENT.CENTER
        for paragraph in cell.paragraphs:
            assert paragraph.alignment == WD_ALIGN_PARAGRAPH.CENTER
