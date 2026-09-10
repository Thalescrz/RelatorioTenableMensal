from __future__ import annotations

import json
import re
import zipfile
from dataclasses import replace
from pathlib import Path
from xml.etree import ElementTree as ET

from docx import Document
from docx.oxml.ns import qn
import pytest


from tenable_reports.config.profile import DistributionRecipient, load_client_profile
from tenable_reports.presentation.cloud_editorial_catalog import (
    approved_cloud_editorial_paragraphs,
)
from tenable_reports.presentation.cloud_report_docx import (
    CloudReportVariant,
    generate_cloud_report,
)
from tenable_reports.presentation.cloud_report_sections import (
    CloudDocumentBuilder,
    SEVERITY_FILLS,
)
from tenable_reports.presentation.full_base_report_docx import generate_full_base_report


ROOT = Path(__file__).resolve().parents[1]
LEGACY_CLOUD_TEMPLATE = ROOT / "templates/corporate/cloud-base-v1.docx"
OFFICIAL_TEMPLATE = ROOT / "templates/corporate/base-v1.docx"
CLOUD_TEMPLATE = OFFICIAL_TEMPLATE
PROFILE = ROOT / "clients/examples/client-profile.json"
BASE_DATASET = ROOT / "tests/fixtures/report-dataset-phase5.json"


def _all_text(path: Path) -> str:
    document = Document(path)
    chunks = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            chunks.extend(cell.text for cell in row.cells)
    for section in document.sections:
        for container in (section.header, section.footer):
            chunks.extend(paragraph.text for paragraph in container.paragraphs)
            for table in container.tables:
                for row in table.rows:
                    chunks.extend(cell.text for cell in row.cells)
    return "\n".join(chunks)


def _cell_fill(cell) -> str | None:
    shading = cell._tc.get_or_add_tcPr().find(qn("w:shd"))
    return None if shading is None else shading.get(qn("w:fill"))


def _control_tables(path: Path) -> list[dict[str, object]]:
    document = Document(path)
    result = []
    for table in document.tables[:3]:
        result.append({
            "title": table.cell(0, 0).text,
            "headers": [cell.text for cell in table.rows[1].cells],
            "rows": [
                [cell.text for cell in row.cells]
                for row in table.rows[2:]
            ],
        })
    return result


def test_general_and_cloud_reports_share_document_control_tables(tmp_path: Path) -> None:
    profile = _profile()
    profile = replace(
        profile,
        document_control=replace(
            profile.document_control,
            standard_distribution_recipients=(
                DistributionRecipient(
                    name="Contato Padrao",
                    organization="Organizacao Padrao",
                    email="padrao@empresa.example",
                ),
            ),
            additional_distribution_recipients=(
                DistributionRecipient(
                    name="Contato Cliente",
                    organization="Organizacao Cliente",
                    email="cliente@empresa.example",
                ),
            ),
        ),
    )
    general_output = tmp_path / "general-control.docx"
    cloud_output = tmp_path / "cloud-control.docx"

    generate_full_base_report(
        template_path=OFFICIAL_TEMPLATE,
        dataset_path=BASE_DATASET,
        profile=profile,
        output_path=general_output,
        mask_sensitive=False,
    )
    generate_cloud_report(
        template_path=OFFICIAL_TEMPLATE,
        dataset_path=_dataset(tmp_path),
        profile=profile,
        output_path=cloud_output,
        variant=CloudReportVariant.EXPANDED,
    )

    general = _control_tables(general_output)
    cloud = _control_tables(cloud_output)
    assert [table["title"] for table in general] == [
        "Preparação",
        "Controle de Versionamento",
        "Lista de Distribuição",
    ]
    assert [table["headers"] for table in cloud] == [
        ["Ação", "Nome", "Data"],
        ["Versão", "Data da Versão", "Seções Afetadas", "Alteração", "Alterado por"],
        ["Nome", "Organização", "E-mail"],
    ]
    assert [table["title"] for table in cloud] == [
        table["title"] for table in general
    ]
    assert cloud[2]["rows"] == general[2]["rows"] == [
        ["Contato Padrao", "Organizacao Padrao", "padrao@empresa.example"],
        ["Contato Cliente", "Organizacao Cliente", "cliente@empresa.example"],
    ]


def test_general_and_cloud_mask_distribution_recipient_fields(tmp_path: Path) -> None:
    profile = _profile()
    profile = replace(
        profile,
        document_control=replace(
            profile.document_control,
            standard_distribution_recipients=(
                DistributionRecipient(
                    name="Contato Padrao",
                    organization="Organizacao Padrao",
                    email="padrao@empresa.example",
                ),
            ),
            additional_distribution_recipients=(
                DistributionRecipient(
                    name="Contato Cliente",
                    organization="Organizacao Cliente",
                    email="cliente@empresa.example",
                ),
            ),
        ),
    )
    general_output = tmp_path / "general-control-masked.docx"
    cloud_output = tmp_path / "cloud-control-masked.docx"

    generate_full_base_report(
        template_path=OFFICIAL_TEMPLATE,
        dataset_path=BASE_DATASET,
        profile=profile,
        output_path=general_output,
        mask_sensitive=True,
    )
    generate_cloud_report(
        template_path=OFFICIAL_TEMPLATE,
        dataset_path=_dataset(tmp_path),
        profile=profile,
        output_path=cloud_output,
        variant=CloudReportVariant.EXPANDED,
        mask_sensitive=True,
    )

    assert _control_tables(general_output)[2]["rows"] == [
        ["", "", ""],
        ["", "", ""],
    ]
    assert _control_tables(cloud_output)[2]["rows"] == [
        ["", "", ""],
        ["", "", ""],
    ]


def test_cloud_table_uses_its_existing_palette_for_semantic_risk_labels() -> None:
    document = Document()
    anchor = document.add_paragraph("fixture-anchor")
    table = CloudDocumentBuilder(document, anchor).table(
        ("Crítica", "Alta", "Média", "Baixa"),
        (("Crítica", "Alta", "Média", "Baixa"),),
    )
    assert table is not None
    expected = [
        SEVERITY_FILLS["CRITICAL"],
        SEVERITY_FILLS["HIGH"],
        SEVERITY_FILLS["MEDIUM"],
        SEVERITY_FILLS["LOW"],
    ]
    assert [_cell_fill(cell) for cell in table.rows[0].cells] == expected
    assert [_cell_fill(cell) for cell in table.rows[1].cells] == expected


def test_cloud_builder_uses_general_report_typography() -> None:
    document = Document()
    anchor = document.add_paragraph("fixture-anchor")
    builder = CloudDocumentBuilder(document, anchor)

    paragraph = builder.paragraph("Corpo")
    heading = builder.heading("Título", 2)
    table = builder.table(("Nome",), (("Valor",),))

    assert paragraph.runs[0].font.name == "Calibri"
    assert paragraph.runs[0].font.size.pt == 9
    assert heading.runs[0].font.name == "Calibri"
    assert heading.runs[0].font.size.pt == 11
    assert heading.style.name == "Heading 2"
    assert table is not None
    assert table.rows[0].cells[0].paragraphs[0].runs[0].font.name == "Calibri"
    assert table.rows[1].cells[0].paragraphs[0].runs[0].font.name == "Calibri"


def test_generated_cloud_report_has_no_legacy_arial_or_times_runs(tmp_path: Path) -> None:
    output = tmp_path / "cloud-calibri.docx"
    generate_cloud_report(
        template_path=CLOUD_TEMPLATE,
        dataset_path=_dataset(tmp_path),
        profile=_profile(),
        output_path=output,
        variant=CloudReportVariant.EXPANDED,
    )

    document = Document(output)
    font_names = {
        run.font.name
        for paragraph in document.paragraphs
        for run in paragraph.runs
        if run.text.strip() and run.font.name
    }
    for table in document.tables:
        font_names.update(
            run.font.name
            for row in table.rows
            for cell in row.cells
            for paragraph in cell.paragraphs
            for run in paragraph.runs
            if run.text.strip() and run.font.name
        )
    assert "Times New Roman" not in font_names
    assert "Calibri" in font_names
    body_headings = [
        paragraph
        for paragraph in document.paragraphs
        if paragraph.style is not None
        and paragraph.style.name.startswith("Heading ")
    ]
    assert body_headings
    assert all(
        run.font.name == "Calibri"
        for paragraph in body_headings
        for run in paragraph.runs
        if run.text.strip()
    )


def _profile():
    profile = load_client_profile(PROFILE)
    return replace(
        profile,
        cloud_security_scope=replace(
            profile.cloud_security_scope,
            enabled=True,
            layout="comparison",
        ),
    )


def _dataset(tmp_path: Path, *, populated: bool = True) -> Path:
    cve = {
        "cve": "CVE-2099-1000",
        "severity": "CRITICAL",
        "vpr": 0.0,
        "vpr_display": "0",
        "cvss": 9.8,
        "cvss_display": "9.8",
        "affected_assets": 2,
        "affected_virtual_machines": 1,
        "affected_container_images": 1,
        "components": ["fixture-component"],
        "description": " ".join(
            f"Long technical sentence {index}." for index in range(80)
        ),
        "assets": [
            {
                "kind": "virtual_machine",
                "asset_id": "vm-fixture",
                "name": "vm-fixture.invalid",
                "account_id": "account-fixture",
                "ip_addresses": ["192.0.2.10"],
                "repository_uri": None,
                "digest": None,
                "components": ["fixture-component"],
            },
            {
                "kind": "container_image",
                "asset_id": "image-fixture",
                "name": "image-fixture",
                "account_id": "account-fixture",
                "ip_addresses": [],
                "repository_uri": "registry.invalid/image-fixture",
                "digest": "sha256:fixture",
                "components": ["fixture-component"],
            },
        ],
    }
    hosts = [
        {
            "kind": "virtual_machine",
            "asset_id": "vm-fixture",
            "name": "vm-fixture.invalid",
            "account_id": "account-fixture",
            "ip_addresses": ["192.0.2.10"],
            "repository_uri": None,
            "digest": None,
            "components": ["fixture-component"],
            "vulnerabilities": 4,
            "critical": 1,
            "high": 2,
            "medium": 1,
            "low": 0,
        }
    ]
    images = [
        {
            "kind": "container_image",
            "asset_id": "image-fixture",
            "name": "image-fixture",
            "account_id": "account-fixture",
            "ip_addresses": [],
            "repository_uri": "registry.invalid/image-fixture",
            "digest": "sha256:fixture",
            "components": ["fixture-component"],
            "vulnerabilities": 3,
            "critical": 1,
            "high": 1,
            "medium": 1,
            "low": 0,
        }
    ]
    correctable = {
        **cve,
        "correction_type": "patch_update",
        "correction_type_display": "Patch/Atualização",
        "correction_origin": "explicit",
        "recommended_action": "Apply the vendor security patch.",
        "remediation_steps": ["Apply the vendor security patch."],
        "correlated_findings": 2,
        "software": "fixture-component",
        "fixed_by": ["2.0.1"],
        "fixed_by_display": "2.0.1",
    }
    payload = {
        "schema_version": 1,
        "document_kind": "cloud",
        "metric_definition_version": "cloud-metrics-v2",
        "connector_version": "cloud-graphql-v1",
        "period": {
            "start_at": "2026-07-01T00:00:00+00:00",
            "end_at": "2026-08-01T00:00:00+00:00",
            "reference_at": "2026-08-26T00:00:00+00:00",
            "timezone": "UTC",
            "period_id": "2026-07",
        },
        "collected_at": "2026-08-26T12:00:00+00:00",
        "snapshot_context": {
            "historical_reconstruction": "EXACT_SNAPSHOT",
            "warning": None,
        },
        "overview": {
            "assets": 2 if populated else 0,
            "virtual_machines": 1 if populated else 0,
            "container_images": 1 if populated else 0,
            "vulnerability_occurrences": 7 if populated else 0,
            "unique_cves": 1 if populated else 0,
            "severity_counts": {
                "CRITICAL": 2 if populated else 0,
                "HIGH": 3 if populated else 0,
                "MEDIUM": 2 if populated else 0,
                "LOW": 0,
            },
            "posture_findings": 0,
        },
        "top_critical_cves": [cve] if populated else [],
        "top_vulnerable_hosts": hosts if populated else [],
        "top_vulnerable_images": images if populated else [],
        "container_image_vulnerability_overview": (
            [
                {
                    "asset": images[0],
                    "rows": [
                        {
                            "cve": "CVE-2099-1000",
                            "severity": "CRITICAL",
                            "vpr_display": "0",
                            "software": "fixture-component",
                            "fixed_by_display": "2.0.1",
                        }
                    ],
                }
            ]
            if populated
            else []
        ),
        "workload_status": {
            "total_virtual_machines": 1 if populated else 0,
            "by_max_severity": {
                "CRITICAL": 1 if populated else 0,
                "HIGH": 0,
                "MEDIUM": 0,
                "LOW": 0,
                "NONE": 0,
            },
        },
        "top_components": [],
        "top_posture_findings": [],
        "top_correctable_vulnerabilities": (
            [
                correctable,
                {
                    **correctable,
                    "software": "fixture-component-without-text-action",
                    "fixed_by": ["3.0.0"],
                    "fixed_by_display": "3.0.0",
                    "recommended_action": None,
                    "remediation_steps": [],
                },
            ] if populated else []
        ),
        "aging": {
            "0-30": 1 if populated else 0,
            "31-60": 0,
            "61-90": 0,
            "91-180": 0,
            ">180": 0,
            "data_indisponivel": 0,
        },
        "remediation_performance": {
            "resolved": 0,
            "average_resolution_days": None,
            "period_interval": "[start_at, end_at)",
        },
        "inventory": {
            "total_resources": 0,
            "by_provider": [],
            "by_region": [],
        },
        "source_status": {
            "virtual_machines": "COMPLETE",
            "container_images": "COMPLETE",
            "findings": "UNAVAILABLE",
            "lifecycle": "COMPLETE",
        },
        "quality_issues": [],
        "capabilities": {},
        "history": [],
        "table_provenance": {"schema_version": 1, "tables": {}},
    }
    path = tmp_path / "cloud-dataset.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_sanitized_cloud_template_keeps_three_page_families() -> None:
    assert LEGACY_CLOUD_TEMPLATE.is_file()
    document = Document(LEGACY_CLOUD_TEMPLATE)
    text = _all_text(LEGACY_CLOUD_TEMPLATE)

    assert len(document.sections) == 3
    assert "{{CLIENT_NAME}}" in text
    assert "{{REPORT_MONTH_YEAR}}" in text
    assert "{{CLOUD_CONTENT_START}}" in text
    assert "01/07/2026" not in text
    assert "TRT8" not in text.upper()
    assert len(document.inline_shapes) == 0

    with zipfile.ZipFile(LEGACY_CLOUD_TEMPLATE) as package:
        all_xml = "\n".join(
            package.read(name).decode("utf-8", errors="ignore")
            for name in package.namelist()
            if name.endswith(".xml")
        )
        header_text = "\n".join(
            "".join(
                node.text or ""
                for node in ET.fromstring(package.read(name)).iter()
                if node.tag.rsplit("}", 1)[-1] == "t"
            )
            for name in package.namelist()
            if name.startswith("word/header") and name.endswith(".xml")
        )
    assert "TRT8" not in all_xml.upper()
    image_nodes = re.findall(r"<wp:docPr\b[^>]*>", all_xml)
    assert image_nodes
    assert all(
        'descr="' in node and 'descr=""' not in node
        for node in image_nodes
    )
    assert not re.search(
        r"n[ºo]\s*\d+\s*/\s*\d{4}",
        header_text,
        re.IGNORECASE,
    )
    assert "dc:creator></dc:creator" in all_xml or "dc:creator/>" in all_xml


def test_cloud_report_uses_shared_official_shell_and_native_heading_hierarchy(
    tmp_path: Path,
) -> None:
    output = tmp_path / "cloud-official-shell.docx"
    generate_cloud_report(
        template_path=OFFICIAL_TEMPLATE,
        dataset_path=_expanded_dataset(tmp_path),
        profile=_profile(),
        output_path=output,
        variant=CloudReportVariant.EXPANDED,
    )

    document = Document(output)
    text = _all_text(output)
    headings = [
        (paragraph.style.name, paragraph.text)
        for paragraph in document.paragraphs
        if paragraph.style is not None
        and paragraph.style.name.startswith("Heading ")
    ]

    assert len(document.sections) == 3
    assert "FORTALEZA - CE" in text
    assert "BELÉM" in text
    assert "PORTUGAL" in text
    assert ("Heading 1", "1. CONTROLE DE DOCUMENTO") in headings
    assert ("Heading 1", "2. OBJETIVO") in headings
    assert ("Heading 1", "3. TENABLE CLOUD SECURITY") in headings
    assert ("Heading 2", "3.4. Principais Vulnerabilidades Críticas (TOP 5 CVEs)") in headings
    assert ("Heading 1", "4. Conclusão") in headings
    first_heading = next(
        paragraph
        for paragraph in document.paragraphs
        if paragraph.style is not None and paragraph.style.name == "Heading 1"
    )
    assert first_heading._p.get_or_add_pPr().find(qn("w:pageBreakBefore")) is not None
    with zipfile.ZipFile(output) as package:
        document_xml = package.read("word/document.xml").decode("utf-8")
        settings_xml = package.read("word/settings.xml").decode("utf-8")
    assert 'TOC \\o "1-3" \\h \\z' in document_xml
    assert 'TOC \\o "1-4"' not in document_xml
    assert re.search(r'<w:updateFields\b[^>]*w:val="true"', settings_xml)


def test_standard_cloud_report_keeps_approved_sections_and_detailed_top_five(
    tmp_path: Path,
) -> None:
    output = tmp_path / "cloud-standard.docx"
    calls: list[str] = []

    def translator(text: str, source: str, target: str) -> str:
        calls.append(text)
        return f"TRADUZIDO: {text}"

    result = generate_cloud_report(
        template_path=CLOUD_TEMPLATE,
        dataset_path=_expanded_dataset(tmp_path),
        profile=_profile(),
        output_path=output,
        variant=CloudReportVariant.EXPANDED,
        translator=translator,
    )
    text = _all_text(output)
    document = Document(output)
    assert output.is_file()
    assert result.variant is CloudReportVariant.EXPANDED
    assert {
        "executive_overview",
        "components_products",
        "vulnerability_aging",
        "remediation_performance",
        "monthly_evolution",
    }.issubset(set(result.rendered_sections))
    assert "cloud_inventory" not in result.rendered_sections
    assert "Principais Vulnerabilidades Críticas" in text
    assert "Principais Vulnerabilidades com Correção Disponível" in text
    assert "CVE-2099-1000" in text
    assert "Tipo de correção" in text
    assert "3.3.1. Overview das Vulnerabilidades das Imagens de Contêiner" in text
    assert "2.0.1" in text
    table_headers = [
        [cell.text for cell in table.rows[0].cells]
        for table in document.tables
    ]
    assert [
        "CVE",
        "Severidade",
        "VPR",
        "Software",
        "Fixed by",
    ] in table_headers
    assert [
        "CVE",
        "VPR",
        "CVSS",
        "Severidade",
        "Ativos afetados",
        "Software",
        "Fixed by",
    ] in table_headers
    assert "Ativos afetados" in text
    assert "VPR: 0" in text
    assert "TRADUZIDO:" in text
    assert "TRADUZIDO: Apply the vendor security patch." in text
    assert len(calls) > 1
    assert "{{" not in text
    for paragraph in approved_cloud_editorial_paragraphs():
        assert paragraph in text

def test_empty_cloud_table_has_monthly_message_not_blank_page(
    tmp_path: Path,
) -> None:
    output = tmp_path / "cloud-empty.docx"
    generate_cloud_report(
        template_path=CLOUD_TEMPLATE,
        dataset_path=_dataset(tmp_path, populated=False),
        profile=_profile(),
        output_path=output,
        variant=CloudReportVariant.EXPANDED,
    )

    text = _all_text(output)
    assert "Neste mês não foram identificadas" in text
    assert "{{" not in text


def test_empty_translation_preserves_original_with_explicit_notice(
    tmp_path: Path,
) -> None:
    output = tmp_path / "cloud-translation-fallback.docx"

    generate_cloud_report(
        template_path=CLOUD_TEMPLATE,
        dataset_path=_dataset(tmp_path),
        profile=_profile(),
        output_path=output,
        variant=CloudReportVariant.EXPANDED,
        translator=lambda *_: "",
    )

    text = _all_text(output)
    assert "Long technical sentence 0." in text
    assert "A tradução automática não pôde ser concluída" in text


def _expanded_dataset(
    tmp_path: Path,
    *,
    findings_available: bool = True,
    with_history: bool = True,
) -> Path:
    path = _dataset(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["top_components"] = [
        {
            "component": "fixture-component",
            "affected_assets": 2,
            "vulnerabilities": 1,
            "occurrences": 2,
        }
    ]
    payload["top_posture_findings"] = [
        {
            "policy": "Fixture policy",
            "category": "Configuration",
            "severity": "HIGH",
            "provider": "Fixture Cloud",
            "findings": 2,
            "affected_resources": 1,
        }
    ]
    payload["capabilities"] = {
        "required_ready": True,
        "sources": {
            "findings": "AVAILABLE" if findings_available else "UNAVAILABLE",
        },
    }
    payload["inventory"] = {
        "total_resources": 3,
        "by_provider": [
            {"provider": "Fixture Cloud", "resources": 3},
        ],
        "by_region": [
            {"region": "fixture-region-1", "resources": 2},
            {"region": "fixture-region-2", "resources": 1},
        ],
    }
    payload["history"] = (
        [
            {
                "period_id": "2026-05",
                "label": "Mai/26",
                "availability": "AVAILABLE",
                "overview": {
                    "unique_cves": 4,
                    "vulnerability_occurrences": 9,
                    "severity_counts": {
                        "CRITICAL": 2,
                        "HIGH": 3,
                        "MEDIUM": 3,
                        "LOW": 1,
                    },
                },
            },
            {
                "period_id": "2026-06",
                "label": "Jun/26",
                "availability": "UNAVAILABLE",
            },
            {
                "period_id": "2026-07",
                "label": "Jul/26",
                "availability": "AVAILABLE",
                "overview": payload["overview"],
            },
        ]
        if with_history
        else []
    )
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_legacy_base_variant_is_not_renderable(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="base"):
        generate_cloud_report(
            template_path=CLOUD_TEMPLATE,
            dataset_path=_expanded_dataset(tmp_path),
            profile=_profile(),
            output_path=tmp_path / "legacy-base.docx",
            variant="base",
        )

def test_expanded_cloud_report_omits_unavailable_conditional_posture(
    tmp_path: Path,
) -> None:
    output = tmp_path / "cloud-expanded-no-findings.docx"
    result = generate_cloud_report(
        template_path=CLOUD_TEMPLATE,
        dataset_path=_expanded_dataset(
            tmp_path,
            findings_available=False,
        ),
        profile=_profile(),
        output_path=output,
        variant=CloudReportVariant.EXPANDED,
    )

    assert "Postura de Segurança em Nuvem" not in _all_text(output)
    assert "cloud_posture" in result.omitted_sections



def test_expanded_cloud_report_uses_current_period_when_history_is_missing(
    tmp_path: Path,
) -> None:
    output = tmp_path / "cloud-expanded-no-history-current-period.docx"
    result = generate_cloud_report(
        template_path=CLOUD_TEMPLATE,
        dataset_path=_expanded_dataset(tmp_path, with_history=False),
        profile=_profile(),
        output_path=output,
        variant=CloudReportVariant.EXPANDED,
    )

    text = _all_text(output)
    assert "3.11. Evolu\u00e7\u00e3o Mensal" in text
    assert "Jul/26" in text
    assert "hist\u00f3rico mensal compat\u00edvel" not in text
    assert "monthly_evolution_chart" not in result.omitted_sections
    document = Document(output)
    monthly_heading = next(
        paragraph
        for paragraph in document.paragraphs
        if paragraph.text.startswith("3.11.")
    )
    assert monthly_heading._p.getprevious().xpath('.//w:br[@w:type="page"]')



def test_expanded_cloud_report_is_the_approved_standard_editorial_model(
    tmp_path: Path,
) -> None:
    output = tmp_path / "cloud-standard.docx"
    result = generate_cloud_report(
        template_path=CLOUD_TEMPLATE,
        dataset_path=_expanded_dataset(tmp_path),
        profile=_profile(),
        output_path=output,
        variant=CloudReportVariant.EXPANDED,
    )

    document = Document(output)
    text = _all_text(output)
    assert "cloud_inventory" not in result.rendered_sections
    assert "Invent\u00e1rio Cloud" not in text
    assert "3.12. Evolu\u00e7\u00e3o Mensal" not in text
    assert "3.11. Evolu\u00e7\u00e3o Mensal" in text
    assert any(
        table.cell(0, 0).text == "CVE"
        and "A\u00e7\u00e3o recomendada"
        not in [cell.text for cell in table.rows[0].cells]
        for table in document.tables
    )
    assert "Inserir print da plataforma aqui" in text

    cve_heading = next(
        paragraph
        for paragraph in document.paragraphs
        if paragraph.text.startswith("3.4.1.")
    )
    assert cve_heading.style.name == "Heading 3"
    vpr_paragraph = next(
        paragraph
        for paragraph in document.paragraphs
        if paragraph.text.startswith("VPR: ")
    )
    assert any(run.font.size and run.font.size.pt == 9 for run in vpr_paragraph.runs)

    monthly_heading = next(
        paragraph
        for paragraph in document.paragraphs
        if paragraph.text.startswith("3.11.")
    )
    assert not monthly_heading._p.getprevious().xpath('.//w:br[@w:type="page"]')
