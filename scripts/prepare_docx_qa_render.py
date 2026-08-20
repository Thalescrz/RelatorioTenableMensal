"""Create a render-only DOCX copy with cached field results made static.

Microsoft Word can spend an unbounded amount of time recalculating nested TOC
and PAGEREF fields during PDF export.  This helper removes only the field-code
markers from a copy, preserving their cached visible text and the original
deliverable unchanged.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn


def _staticize_part(part: object) -> int:
    element = getattr(part, "element", None)
    if element is None:
        return 0
    removed = 0
    for tag_name in ("w:fldChar", "w:instrText", "w:fldSimple"):
        for node in list(element.iter(qn(tag_name))):
            parent = node.getparent()
            if parent is None:
                continue
            if tag_name == "w:fldSimple":
                position = parent.index(node)
                for child in list(node):
                    node.remove(child)
                    parent.insert(position, child)
                    position += 1
            parent.remove(node)
            removed += 1
    return removed


def prepare(input_path: Path, output_path: Path) -> int:
    document = Document(input_path)
    parts = [document.part]
    for section in document.sections:
        parts.extend(
            (
                section.header.part,
                section.first_page_header.part,
                section.even_page_header.part,
                section.footer.part,
                section.first_page_footer.part,
                section.even_page_footer.part,
            )
        )
    unique_parts = {id(part): part for part in parts}
    removed = sum(_staticize_part(part) for part in unique_parts.values())
    settings = document.settings.element
    for node in list(settings.iter(qn("w:updateFields"))):
        parent = node.getparent()
        if parent is not None:
            parent.remove(node)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    document.save(output_path)
    return removed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    removed = prepare(args.input, args.output)
    print(f"render_copy={args.output.resolve()} field_markers_removed={removed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
