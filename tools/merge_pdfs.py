#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

from pypdf import PdfReader, PdfWriter


def main() -> int:
    output = Path(sys.argv[1])
    writer = PdfWriter()
    for raw_path in sys.argv[2:]:
        reader = PdfReader(raw_path)
        for page in reader.pages:
            writer.add_page(page)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as stream:
        writer.write(stream)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
