#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


HEADING_RE = re.compile(
    r"^\s*([0-9]+(\.[0-9]+){0,4}\.?\s+|SUMÁRIO|CONTROLE|OBJETIVO|SENSOR|VISÃO|VULNERABILIDADES|TENABLE|INCREMENTANDO|RESUMO|EVOLUÇÃO)",
    re.IGNORECASE,
)


def main() -> int:
    for raw_path in sys.argv[1:]:
        path = Path(raw_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        print(f"=== {path.parent.name} ===")
        for key in ("counts", "core_properties", "styles_used", "direct_fonts", "direct_sizes_pt", "direct_colors"):
            print(key.upper(), json.dumps(data[key], ensure_ascii=False))
        print("SECTIONS", json.dumps(data["sections"], ensure_ascii=False))
        print("OOXML", json.dumps(data["ooxml"], ensure_ascii=False))
        print("HEADING CANDIDATES")
        for paragraph in data["paragraphs"]:
            text = paragraph["text"]
            style = paragraph["style"] or ""
            if text and (re.search(r"Heading|Título|Titulo", style, re.IGNORECASE) or HEADING_RE.search(text)):
                print(f"P{paragraph['index']} [{style}] {text}")
        print("TABLE HEADERS")
        for table in data["tables"]:
            first = " | ".join(table["matrix"][0]) if table["matrix"] else ""
            print(
                f"T{table['index']} rows={table['rows']} cols={table['columns']} "
                f"style={table['style']} repeat={table['has_repeat_header']} :: {first}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
