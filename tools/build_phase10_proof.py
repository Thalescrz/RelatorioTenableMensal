from __future__ import annotations

import json
from pathlib import Path

from tenable_reports.application.publishing import create_publication_manifest
from tenable_reports.config.profile import load_client_profile
from tenable_reports.presentation.customizations_report_docx import (
    generate_customizations_report,
)
from tenable_reports.presentation.full_base_report_docx import generate_full_base_report


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    output = ROOT / "analysis_artifacts/phase10"
    output.mkdir(parents=True, exist_ok=True)
    profile = load_client_profile(
        ROOT / "clients/examples/client-profile-intelligence-expanded.json"
    )
    dataset = ROOT / "tests/fixtures/report-dataset-phase5.json"
    template = ROOT / "templates/corporate/base-v1.docx"
    base = generate_full_base_report(
        template_path=template,
        dataset_path=dataset,
        profile=profile,
        output_path=output / "01-relatorio-base-fase10-sanitizado.docx",
        assets_dir=ROOT / "templates/corporate/assets",
        mask_sensitive=True,
    )
    custom = generate_customizations_report(
        template_path=template,
        dataset_path=dataset,
        profile=profile,
        output_path=output / "02-inteligencia-e-customizacoes-fase10-sanitizado.docx",
        mask_sensitive=True,
    )
    manifest = create_publication_manifest(
        output_path=output / "publication-manifest.json",
        client_id=profile.client_id,
        tenant_id=profile.tenant_id,
        run_id="phase10-offline-proof",
        execution_type="MANUAL",
        period={"period_id": "2026-07", "mode": "MANUAL_EXPLICIT"},
        dataset_path=dataset,
        documents=(base.output_path, custom.output_path),
        history_database=None,
    )
    print(json.dumps({
        "base": str(base.output_path.resolve()),
        "custom": str(custom.output_path.resolve()),
        "manifest": str(manifest.resolve()),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
