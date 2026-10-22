"""Compare one captured report set with an approved visual manifest."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class VisualDifference:
    report_kind: str
    field: str
    expected: object
    actual: object


@dataclass(frozen=True)
class VisualComparison:
    baseline_manifest: Path
    candidate_manifest: Path
    differences: tuple[VisualDifference, ...]

    @property
    def passed(self) -> bool:
        return not self.differences


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "visual-baseline-v1":
        raise ValueError(f"Manifesto visual incompativel: {path.name}")
    return payload


def _hamming(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


def compare_visual_baseline(
    *,
    baseline_manifest: Path,
    candidate_root: Path,
    dhash_tolerance: int = 2,
) -> VisualComparison:
    baseline_manifest = baseline_manifest.resolve()
    candidate_manifest = (candidate_root / "manifest.json").resolve()
    baseline = _load(baseline_manifest)
    candidate = _load(candidate_manifest)
    differences: list[VisualDifference] = []
    if baseline.get("profile") != candidate.get("profile"):
        differences.append(
            VisualDifference(
                "environment",
                "profile",
                baseline.get("profile"),
                candidate.get("profile"),
            )
        )
    expected_reports = {item["kind"]: item for item in baseline.get("reports", [])}
    actual_reports = {item["kind"]: item for item in candidate.get("reports", [])}
    for kind in sorted(set(expected_reports) | set(actual_reports)):
        expected = expected_reports.get(kind)
        actual = actual_reports.get(kind)
        if expected is None or actual is None:
            differences.append(VisualDifference(kind, "present", expected is not None, actual is not None))
            continue
        for field in ("normalized_docx_sha256", "page_count"):
            if expected.get(field) != actual.get(field):
                differences.append(VisualDifference(kind, field, expected.get(field), actual.get(field)))
        expected_pages = expected.get("pages", [])
        actual_pages = actual.get("pages", [])
        for index, (expected_page, actual_page) in enumerate(
            zip(expected_pages, actual_pages, strict=False),
            start=1,
        ):
            for field in ("width", "height"):
                if expected_page.get(field) != actual_page.get(field):
                    differences.append(
                        VisualDifference(
                            kind,
                            f"pages[{index}].{field}",
                            expected_page.get(field),
                            actual_page.get(field),
                        )
                    )
            expected_hash = expected_page.get("dhash")
            actual_hash = actual_page.get("dhash")
            if not isinstance(expected_hash, str) or not isinstance(actual_hash, str):
                differences.append(
                    VisualDifference(kind, f"pages[{index}].dhash", expected_hash, actual_hash)
                )
            elif _hamming(expected_hash, actual_hash) > dhash_tolerance:
                differences.append(
                    VisualDifference(kind, f"pages[{index}].dhash", expected_hash, actual_hash)
                )
    return VisualComparison(
        baseline_manifest=baseline_manifest,
        candidate_manifest=candidate_manifest,
        differences=tuple(differences),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--dhash-tolerance", type=int, default=2)
    args = parser.parse_args()
    result = compare_visual_baseline(
        baseline_manifest=args.baseline,
        candidate_root=args.candidate_root,
        dhash_tolerance=args.dhash_tolerance,
    )
    print(
        json.dumps(
            {
                "status": "ok" if result.passed else "different",
                "differences": [difference.__dict__ for difference in result.differences],
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
