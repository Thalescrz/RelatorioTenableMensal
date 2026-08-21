from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from dataclasses import replace
from pathlib import Path
from zipfile import ZipFile

from tenable_reports.application.publishing import (
    PublicationDocument,
    create_publication_manifest,
)
from tenable_reports.config.profile import (
    TagReportSelection,
    TagReportsConfig,
    load_client_profile,
)
from tenable_reports.domain.reporting import previous_calendar_month
from tenable_reports.presentation.customizations_report_docx import (
    generate_customizations_report,
)
from tenable_reports.presentation.full_base_report_docx import (
    generate_full_base_report,
)
from tenable_reports.presentation.report_filenames import (
    report_filename,
    tag_report_filename,
)
from tenable_reports.presentation.tag_report_docx import generate_tag_report


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "corporate" / "base-v1.docx"
SOURCE_DATASET = ROOT / "tests" / "fixtures" / "report-dataset-phase5.json"
SOURCE_PROFILE = (
    ROOT / "clients" / "examples" / "client-profile-all-customizations.json"
)


def _month(
    source: dict,
    period_id: str,
    label: str,
    non_mitigated: int,
    mitigated: int,
    new: int,
) -> dict:
    quarter = non_mitigated // 4
    return {
        "period_id": period_id,
        "label": label,
        "availability": "AVAILABLE",
        "non_mitigated": non_mitigated,
        "non_mitigated_by_severity": {
            "critical": quarter,
            "high": quarter,
            "medium": quarter,
            "low": non_mitigated - 3 * quarter,
        },
        "mitigated": mitigated,
        "mitigated_by_severity": {
            "critical": 0,
            "high": mitigated // 2,
            "medium": mitigated - mitigated // 2,
            "low": 0,
        },
        "new": new,
        "new_by_severity": {
            "critical": 0,
            "high": new // 2,
            "medium": new - new // 2,
            "low": 0,
        },
        "top_assets": source.get("top_assets", [])[:3],
    }


def _tag_dataset(
    source: dict,
    selection: TagReportSelection,
) -> dict:
    dataset = json.loads(json.dumps(source))
    dataset["document_kind"] = "tag"
    dataset["tag"] = {
        "tag_uuid": selection.tag_uuid,
        "category_uuid": selection.category_uuid,
        "category_name": selection.category_name,
        "value": selection.value,
        "include_temporal_comparison": selection.include_temporal_comparison,
    }
    dataset["tag_history_status"] = "AVAILABLE"
    dataset["tag_history"] = [
        _month(dataset, "2026-01", "Janeiro/2026", 40, 5, 8),
        _month(dataset, "2026-02", "Fevereiro/2026", 35, 7, 2),
        _month(dataset, "2026-07", "Julho/2026", 28, 9, 4),
    ]
    if selection.include_temporal_comparison:
        dataset["tag_comparison"] = {
            "periods": [
                {
                    "period_id": "2026-02",
                    "label": "Fevereiro/2026",
                    "top_assets": dataset.get("top_assets", [])[:3],
                },
                {
                    "period_id": "2026-07",
                    "label": "Julho/2026",
                    "top_assets": dataset.get("top_assets", [])[:3],
                },
            ]
        }
    else:
        dataset.pop("tag_comparison", None)
    return dataset


def _word_payload_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with ZipFile(path) as package:
        for name in sorted(
            item for item in package.namelist() if item.startswith("word/")
        ):
            digest.update(name.encode("utf-8"))
            digest.update(package.read(name))
    return digest.hexdigest()


def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def render_fixture(output_root: Path) -> Path:
    output_root = output_root.resolve()
    fixture_root = (ROOT / ".tmp").resolve()
    try:
        relative_output = output_root.relative_to(fixture_root)
    except ValueError as exc:
        raise ValueError(
            f"A fixture só pode ser gerada dentro de {fixture_root}."
        ) from exc
    if not relative_output.parts:
        raise ValueError("A raiz .tmp não pode ser usada diretamente como saída.")
    if output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True)
    control_root = output_root / ".invariance-control"
    control_root.mkdir()

    source = json.loads(SOURCE_DATASET.read_text(encoding="utf-8"))
    dataset_path = _write_json(output_root / "report-dataset.json", source)
    period = previous_calendar_month(
        reference_at="2026-08-01T12:00:00Z",
        timezone_name="America/Fortaleza",
    )
    profile = load_client_profile(SOURCE_PROFILE)
    tag_with_comparison = TagReportSelection(
        tag_uuid="tag-equipe-infraestrutura",
        category_uuid="category-equipe",
        category_name="Equipe",
        value="Infraestrutura",
        generate_report=True,
        include_temporal_comparison=True,
    )
    tag_without_comparison = TagReportSelection(
        tag_uuid="tag-local-fortaleza",
        category_uuid="category-local",
        category_name="Local",
        value="Fortaleza",
        generate_report=True,
        include_temporal_comparison=False,
    )
    enabled_profile = replace(
        profile,
        report=replace(
            profile.report,
            tag_reports=TagReportsConfig(
                enabled=True,
                tags=(tag_with_comparison, tag_without_comparison),
            ),
        ),
    )
    disabled_profile = replace(
        enabled_profile,
        report=replace(
            enabled_profile.report,
            tag_reports=TagReportsConfig(enabled=False),
        ),
    )

    base_path = output_root / report_filename(
        enabled_profile.display_name, period, "base"
    )
    custom_path = output_root / report_filename(
        enabled_profile.display_name, period, "custom"
    )
    generate_full_base_report(
        template_path=TEMPLATE,
        dataset_path=dataset_path,
        profile=enabled_profile,
        output_path=base_path,
        mask_sensitive=True,
    )
    generate_customizations_report(
        template_path=TEMPLATE,
        dataset_path=dataset_path,
        profile=enabled_profile,
        output_path=custom_path,
        mask_sensitive=True,
    )

    disabled_base = control_root / base_path.name
    disabled_custom = control_root / custom_path.name
    generate_full_base_report(
        template_path=TEMPLATE,
        dataset_path=dataset_path,
        profile=disabled_profile,
        output_path=disabled_base,
        mask_sensitive=True,
    )
    generate_customizations_report(
        template_path=TEMPLATE,
        dataset_path=dataset_path,
        profile=disabled_profile,
        output_path=disabled_custom,
        mask_sensitive=True,
    )
    invariance = {
        "base_word_payload_equal": (
            _word_payload_hash(base_path) == _word_payload_hash(disabled_base)
        ),
        "custom_word_payload_equal": (
            _word_payload_hash(custom_path) == _word_payload_hash(disabled_custom)
        ),
    }
    if not all(invariance.values()):
        raise RuntimeError(
            "Habilitar relatórios por TAG alterou um documento geral de controle."
        )

    tag_documents: list[PublicationDocument] = []
    comparison_flags: dict[str, bool] = {}
    for selection in (tag_with_comparison, tag_without_comparison):
        tag_dataset_path = _write_json(
            output_root / f"dataset-{selection.tag_uuid}.json",
            _tag_dataset(source, selection),
        )
        tag_output = output_root / tag_report_filename(
            enabled_profile.display_name,
            period,
            selection.category_name,
            selection.value,
            selection.tag_uuid,
        )
        rendered = generate_tag_report(
            template_path=TEMPLATE,
            dataset_path=tag_dataset_path,
            profile=enabled_profile,
            output_path=tag_output,
            mask_sensitive=True,
        )
        comparison_flags[selection.tag_uuid] = rendered.comparison_rendered
        tag_documents.append(PublicationDocument(
            path=tag_output,
            document_kind="tag",
            tag_uuid=selection.tag_uuid,
            tag_category=selection.category_name,
            tag_value=selection.value,
        ))
    if comparison_flags != {
        "tag-equipe-infraestrutura": True,
        "tag-local-fortaleza": False,
    }:
        raise RuntimeError("A autorização do comparativo por TAG não foi respeitada.")

    manifest_path = create_publication_manifest(
        output_path=output_root / "publication-manifest.json",
        client_id=enabled_profile.client_id,
        tenant_id=enabled_profile.tenant_id,
        run_id="fixture-tag-reports-2026-07",
        execution_type="AUTOMATIC_MONTHLY",
        period=source["period"],
        dataset_path=dataset_path,
        documents=(
            PublicationDocument(base_path, "base"),
            PublicationDocument(custom_path, "custom"),
            *tag_documents,
        ),
        history_database=None,
        history_store={
            "backend": "fixture",
            "location": None,
            "compact_tag_history": True,
        },
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["fixture_validation"] = {
        "document_count": len(manifest["documents"]),
        "comparison_rendered_by_tag": comparison_flags,
        "general_document_invariance": invariance,
        "sensitive_fields_masked": True,
    }
    _write_json(manifest_path, manifest)
    shutil.rmtree(control_root)
    return manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Gera quatro DOCX sanitizados para validar relatórios por TAG."
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / ".tmp" / "e2e-tag-reports",
    )
    args = parser.parse_args()
    manifest = render_fixture(args.output_root.resolve())
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    print(json.dumps({
        "status": "OK",
        "manifest": str(manifest),
        "documents": len(payload["documents"]),
        "validation": payload["fixture_validation"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
