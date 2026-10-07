from __future__ import annotations

import json
import tempfile
import zipfile
from dataclasses import replace
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from PIL import Image as PilImage

from tenable_reports.config.profile import load_client_profile
from tenable_reports.presentation.customizations_report_docx import (
    _bar_chart,
    generate_customizations_report,
)


ROOT = Path(__file__).resolve().parents[1]


def test_executive_bar_chart_keeps_bars_out_of_the_label_column(tmp_path: Path) -> None:
    output = tmp_path / "executive-mixed-signs.png"

    _bar_chart(
        output,
        "Evolução de Vulnerabilidades",
        (
            {"label": "Categoria com descrição longa", "change": -100},
            {"label": "Outra categoria", "change": 80},
        ),
        (("change", "Variação", "#2E59FC"),),
    )

    with PilImage.open(output) as image:
        pixels = image.load()
        blue_positions = [
            x
            for y in range(image.height)
            for x in range(image.width)
            if pixels[x, y] == (46, 89, 252)
        ]

    assert blue_positions
    assert min(blue_positions) >= 360


def _text(document):
    values = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            values.extend(cell.text for cell in row.cells)
    return "\n".join(values)


def test_customizations_are_kept_outside_the_base_document() -> None:
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "custom.docx"
        result = generate_customizations_report(
            template_path=ROOT / "templates/corporate/base-v1.docx",
            dataset_path=ROOT / "tests/fixtures/report-dataset-phase5.json",
            profile=load_client_profile(
                ROOT / "clients/examples/client-profile-all-customizations.json"
            ),
            output_path=output,
            mask_sensitive=True,
        )
        assert set(result.rendered_modules) == {
            "vm_monthly_volume",
            "vm_previous_period_delta",
            "scan_auth_health",
            "vm_plugin_family",
            "vm_eol_software",
            "vm_executive_evolution",
            "vm_monthly_evolution",

            "vm_exploit_vector",
            "was_unsupported_tech",
        }
        assert result.requested_modules == tuple(
            load_client_profile(
                ROOT / "clients/examples/client-profile-all-customizations.json"
            ).report.intelligence_modules
        )
        assert result.omitted_modules == (
            {
                "module_id": "vm_network_comparison",
                "reason": "MOVED_TO_TAG_REPORT",
            },
            {
                "module_id": "cloud_container_images",
                "reason": "MOVED_TO_CLOUD_REPORT",
            },
        )
        document = Document(output)
        text = _text(document)
        assert "JULHO/2026" in text
        assert "Comparativo Mensal de Vulnerabilidades" in text
        assert "Vulnerabilidades “Não Mitigadas”" in text
        assert "Vulnerabilidades “Mitigadas”" in text
        assert "Geral" in text
        assert "Servidores" in text
        assert "Sistemas operacionais e softwares sem suporte" in text
        assert "TENABLE CLOUD SECURITY (CONTAINER IMAGES)" not in text
        assert "Vulnerabilidades Exploráveis por Vetor de Ataque" in text
        assert "Dados indisponíveis para este indicador." in text
        assert "Exploitable" in text
        assert "Principais ativos Vulneráveis por Rede" not in text
        assert "Principais Aplicações “Unsupported”" in text
        assert (
            "SUA MELHOR ALIADA NA JORNADA DA PROTEÇÃO DIGITAL."
            in " ".join(text.split())
        )
        assert "METODOLOGIA, QUALIDADE E LIMITAÇÕES" not in text
        # A capa oficial usa imagens ancoradas; os elementos inline restantes
        # preservam os gráficos mensais e demais visuais do relatório.
        assert len(document.inline_shapes) >= 11


def test_customizations_report_uses_official_cover_and_back_cover_shell() -> None:
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "custom-official-shell.docx"
        generate_customizations_report(
            template_path=ROOT / "templates/corporate/base-v1.docx",
            dataset_path=ROOT / "tests/fixtures/report-dataset-phase5.json",
            profile=load_client_profile(
                ROOT / "clients/examples/client-profile-all-customizations.json"
            ),
            output_path=output,
            mask_sensitive=True,
        )

        document = Document(output)
        text = _text(document)
        assert len(document.sections) == 3
        assert "FORTALEZA - CE" in text
        assert "BELÉM" in text
        assert "PORTUGAL" in text
        with zipfile.ZipFile(output) as package:
            assert package.read("word/document.xml").count(b"<wp:anchor") >= 10


def test_customizations_report_has_one_numbered_top_level_and_numbered_modules() -> None:
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "custom-numbered.docx"
        generate_customizations_report(
            template_path=ROOT / "templates/corporate/base-v1.docx",
            dataset_path=ROOT / "tests/fixtures/report-dataset-phase5.json",
            profile=load_client_profile(
                ROOT / "clients/examples/client-profile-all-customizations.json"
            ),
            output_path=output,
            mask_sensitive=True,
        )

        document = Document(output)
        headings = [
            (paragraph.style.name, paragraph.text)
            for paragraph in document.paragraphs
            if paragraph.style is not None
            and paragraph.style.name.startswith("Heading ")
        ]
        assert [text for style, text in headings if style == "Heading 1"] == [
            "1. INTELIGÊNCIA E CUSTOMIZAÇÕES TENABLE"
        ]
        first_heading = next(
            paragraph
            for paragraph in document.paragraphs
            if paragraph.style is not None and paragraph.style.name == "Heading 1"
        )
        assert first_heading._p.get_or_add_pPr().find(qn("w:pageBreakBefore")) is not None
        assert (
            "Heading 2",
            "1.1. Evolução mensal de vulnerabilidades",
        ) in headings
        assert (
            "Heading 2",
            "1.2. Comparativo Mensal de Vulnerabilidades Mitigadas e Não Mitigadas.",
        ) in headings
        assert (
            "Heading 2",
            "1.6. Sistemas operacionais e softwares sem suporte",
        ) in headings
        assert (
            "Heading 2",
            "1.9. Vulnerabilidades Exploráveis por Vetor de Ataque",
        ) in headings
        level_two = [text for style, text in headings if style == "Heading 2"]
        assert level_two.index("1.1. Evolução mensal de vulnerabilidades") < level_two.index(
            "1.2. Comparativo Mensal de Vulnerabilidades Mitigadas e Não Mitigadas."
        )
        with zipfile.ZipFile(output) as package:
            document_xml = package.read("word/document.xml").decode("utf-8")
        assert "Comparativo de Vulnerabilidades Novas 2026 por severidade e total" in document_xml


def test_eol_asset_ranking_is_limited_to_twenty_rows() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source = json.loads(
            (ROOT / "tests/fixtures/report-dataset-phase5.json").read_text(
                encoding="utf-8"
            )
        )
        source["customizations"]["eol_assets"] = [
            {
                "asset_key": f"asset-{index:02d}",
                "ip_address": "",
                "asset_name": "",
                "critical": 1,
                "high": 2,
                "medium": 3,
                "low": index,
                "total": index + 6,
            }
            for index in range(25)
        ]
        dataset = Path(directory) / "eol-top20.json"
        dataset.write_text(json.dumps(source), encoding="utf-8")
        output = Path(directory) / "custom-eol-top20.docx"

        generate_customizations_report(
            template_path=ROOT / "templates/corporate/base-v1.docx",
            dataset_path=dataset,
            profile=load_client_profile(
                ROOT / "clients/examples/client-profile-all-customizations.json"
            ),
            output_path=output,
            mask_sensitive=True,
        )

        document = Document(output)
        table = next(
            table
            for table in document.tables
            if tuple(cell.text for cell in table.rows[0].cells)
            == ("IP Address", "Asset Name", "Crítica", "Alta", "Média", "Baixa", "Total")
        )
        assert len(table.rows) == 21


def test_customizations_report_mirrors_branding_on_even_body_pages() -> None:
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "custom-even-pages.docx"
        generate_customizations_report(
            template_path=ROOT / "templates/corporate/base-v1.docx",
            dataset_path=ROOT / "tests/fixtures/report-dataset-phase5.json",
            profile=load_client_profile(
                ROOT / "clients/examples/client-profile-all-customizations.json"
            ),
            output_path=output,
            mask_sensitive=True,
        )

        document = Document(output)
        body_section = document.sections[1]
        even_header_xml = body_section.even_page_header._element.xml
        assert "Cliente Exemplo" in even_header_xml
        assert "<w:drawing" in even_header_xml


def test_customization_modules_without_data_are_omitted_with_reason() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source = json.loads(
            (ROOT / "tests/fixtures/report-dataset-phase5.json").read_text(
                encoding="utf-8"
            )
        )
        source["customizations"] = {}
        dataset = Path(directory) / "without-history.json"
        dataset.write_text(json.dumps(source), encoding="utf-8")
        result = generate_customizations_report(
            template_path=ROOT / "templates/corporate/base-v1.docx",
            dataset_path=dataset,
            profile=load_client_profile(
                ROOT / "clients/examples/client-profile-intelligence-expanded.json"
            ),
            output_path=Path(directory) / "custom.docx",
            mask_sensitive=True,
        )
        assert result.rendered_modules == ()
        reasons = {item["module_id"]: item["reason"] for item in result.omitted_modules}
        assert reasons["vm_monthly_volume"] == "NO_COMPATIBLE_HISTORY"
        assert reasons["vm_network_comparison"] == "MOVED_TO_TAG_REPORT"
        assert reasons["cloud_container_images"] == "MOVED_TO_CLOUD_REPORT"


def test_profile_without_customizations_keeps_official_back_cover() -> None:
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "custom.docx"
        result = generate_customizations_report(
            template_path=ROOT / "templates/corporate/base-v1.docx",
            dataset_path=ROOT / "tests/fixtures/report-dataset-phase5.json",
            profile=load_client_profile(
                ROOT / "clients/examples/client-profile-vm-standard.json"
            ),
            output_path=output,
            mask_sensitive=True,
        )
        assert result.rendered_modules == ()
        document = Document(output)
        assert len(document.sections) == 3
        assert "SUA MELHOR ALIADA" in _text(document)


def test_first_month_renders_current_baseline_and_explicit_no_data_messages() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source = json.loads(
            (ROOT / "tests/fixtures/report-dataset-phase5.json").read_text(encoding="utf-8")
        )
        source["customizations"] = {
            "monthly_history": [source["customizations"]["monthly_history"][-1]],
            "monthly_views": [{
                "id": "general", "label": "Geral",
                "history": [source["customizations"]["monthly_history"][-1]],
            }],
            "network_tag_snapshots": [{
                "tag_uuid": "tag-a", "network": "Rede A", "period_id": "2026-07",
                "assets": [{"asset_key": "a", "total": 3, "exploitable": 1}],
            }],
            "plugin_family": [], "eol_assets": [], "eol_software": [],
            "attack_vectors": [], "was_unsupported_tech": [],
            "scan_auth_health": {"success": 0, "failure": 0, "total": 0},
            "customization_statuses": {
                "scan_auth_health": "NO_OCCURRENCES",
                "vm_plugin_family": "NO_OCCURRENCES",
                "vm_eol_software": "NO_OCCURRENCES",
                "vm_exploit_vector": "NO_OCCURRENCES",
                "was_unsupported_tech": "NO_OCCURRENCES",
            },
            "history_status": {"status": "NO_IMMEDIATE_MAIN"},
        }
        dataset = Path(directory) / "first-month.json"
        dataset.write_text(json.dumps(source), encoding="utf-8")
        result = generate_customizations_report(
            template_path=ROOT / "templates/corporate/base-v1.docx",
            dataset_path=dataset,
            profile=load_client_profile(ROOT / "clients/examples/client-profile-all-customizations.json"),
            output_path=Path(directory) / "custom.docx",
            mask_sensitive=True,
        )
        text = _text(Document(result.output_path))
        assert "Não há histórico do período imediatamente anterior para comparação." in text
        assert "Baseline do período atual" not in text
        assert "Neste mês não foram identificadas vulnerabilidades mitigadas" in text
        assert "Neste mês não foram identificados sistemas ou softwares sem suporte." in text
        assert "Neste mês não foram identificadas vulnerabilidades exploráveis" in text
        assert "Neste mês não foram identificadas tecnologias WEB sem suporte." in text


def test_controlled_scope_override_renders_audit_notice_without_no_history() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source = json.loads(
            (ROOT / "tests/fixtures/report-dataset-phase5.json").read_text(
                encoding="utf-8"
            )
        )
        notice = "Comparação autorizada com alteração controlada da cobertura de ativos."
        source["customizations"]["history_status"] = {
            "status": "CONTROLLED_SCOPE_OVERRIDE",
            "predecessor_period_id": "2026-06",
            "predecessor_snapshot_id": "snapshot-sanitized",
            "allowed_scope_changes": ["vm_include_unlicensed"],
            "reason": "USER_AUTHORIZED_SCOPE_CHANGE",
            "message": notice,
        }
        dataset = Path(directory) / "controlled-scope.json"
        dataset.write_text(json.dumps(source), encoding="utf-8")

        result = generate_customizations_report(
            template_path=ROOT / "templates/corporate/base-v1.docx",
            dataset_path=dataset,
            profile=load_client_profile(
                ROOT / "clients/examples/client-profile-all-customizations.json"
            ),
            output_path=Path(directory) / "custom.docx",
            mask_sensitive=True,
        )
        text = _text(Document(result.output_path))

        assert notice in text
        assert "Não há histórico do período imediatamente anterior" not in text


def test_unavailable_was_customization_is_not_presented_as_no_occurrences() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source = json.loads(
            (ROOT / "tests/fixtures/report-dataset-phase5.json").read_text(
                encoding="utf-8"
            )
        )
        source.setdefault("customizations", {})["was_unsupported_tech"] = []
        source["customizations"].setdefault("customization_statuses", {})[
            "was_unsupported_tech"
        ] = "DATA_UNAVAILABLE"
        dataset = Path(directory) / "was-unavailable.json"
        dataset.write_text(json.dumps(source), encoding="utf-8")

        result = generate_customizations_report(
            template_path=ROOT / "templates/corporate/base-v1.docx",
            dataset_path=dataset,
            profile=load_client_profile(
                ROOT / "clients/examples/client-profile-all-customizations.json"
            ),
            output_path=Path(directory) / "custom.docx",
            mask_sensitive=True,
        )

        text = _text(Document(result.output_path))
        assert "Não foi possível concluir a coleta WEB" in text
        assert "Neste mês não foram identificadas tecnologias WEB sem suporte." not in text


def test_source_filter_notes_cover_custom_data_tables() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source = json.loads(
            (ROOT / "tests/fixtures/report-dataset-phase5.json").read_text(
                encoding="utf-8"
            )
        )
        common = {
            "view": "Explore > Findings > Vulnerabilities",
            "period_start_at": "2026-07-01T00:00:00Z",
            "period_end_at": "2026-08-01T00:00:00Z",
            "severities": ["CRITICAL", "HIGH", "MEDIUM", "LOW"],
        }
        source["table_provenance"] = {
            "version": "table-provenance-v1",
            "tables": {
                "previous_period_overview": {
                    **common,
                    "validation_queries": [
                        {"label": "Não mitigadas", "states": ["OPEN", "REOPENED"], "date_fields": ["Last Seen"]},
                        {"label": "Mitigadas", "states": ["FIXED"], "date_fields": ["Last Fixed"]},
                    ],
                    "rule": "Período main anterior",
                },
                "network_tag_snapshots": [{
                    **common,
                    "states": ["OPEN", "REOPENED"],
                    "date_fields": ["Last Seen"],
                    "tag_uuid": "tag-rede-exemplo-a",
                    "tag_category": "Rede",
                    "tag_value": "Rede de exemplo A",
                    "rule": "Mesma rede em dois períodos",
                }],
                "network_asset_movement": [{
                    **common,
                    "tag_uuid": "tag-rede-exemplo-a",
                    "tag_category": "Rede",
                    "tag_value": "Rede de exemplo A",
                    "validation_queries": [
                        {"label": "Consulta 1", "states": ["OPEN", "REOPENED"], "date_fields": ["Last Seen"]},
                        {"label": "Consulta 2", "states": ["OPEN", "REOPENED"], "date_fields": ["Last Seen"]},
                    ],
                    "rule": "Variação de posição do ativo",
                }],
                "plugin_family": {**common, "states": ["FIXED"], "date_fields": ["Last Fixed"], "rule": "Agrupar por família de plugin"},
                "eol_assets": {**common, "states": ["OPEN", "REOPENED"], "date_fields": ["Last Seen"], "rule": "Catálogo textual de fim de suporte"},
                "eol_software": {**common, "states": ["OPEN", "REOPENED"], "date_fields": ["Last Seen"], "rule": "Plugins de fim de suporte"},
                "container_images": {**common, "platform_validation_available": False, "rule": "Agrupar por imagem de container"},
                "container_findings": {**common, "platform_validation_available": False, "rule": "Findings da imagem selecionada"},
                "attack_vectors": {**common, "states": ["OPEN", "REOPENED"], "date_fields": ["Last Seen"], "platform_filters": {"Plugin > Exploit Available": "Yes"}, "rule": "Exploit Available e vetor CVSS v3"},
                "was_unsupported_tech": {**common, "states": ["OPEN", "REOPENED"], "date_fields": ["Last Seen"], "rule": "Tecnologias WEB sem suporte"},
            },
        }
        dataset = Path(directory) / "custom-filters.json"
        dataset.write_text(json.dumps(source), encoding="utf-8")
        profile = load_client_profile(
            ROOT / "clients/examples/client-profile-all-customizations.json"
        )
        profile = replace(
            profile,
            presentation=replace(profile.presentation, show_source_filters=True),
        )
        output = Path(directory) / "custom-filters.docx"
        generate_customizations_report(
            template_path=ROOT / "templates/corporate/base-v1.docx",
            dataset_path=dataset,
            profile=profile,
            output_path=output,
            mask_sensitive=True,
        )
        text = _text(Document(output))
        for marker in (
            "Período main anterior",
            "Agrupar por família de plugin",
            "Catálogo textual de fim de suporte",
            "Exploit Available e vetor CVSS v3",
            "Tecnologias WEB sem suporte",
            "Mitigadas: State = Fixed; Last Fixed = Junho 2026",
            "State = Fixed; Severity = Critical, High, Medium, Low; Last Fixed = 01/07/2026 a 31/07/2026",
            "Plugin > Exploit Available = Yes",
            "Plugin ID = 990001, 990002",
            "Plugin ID = 981001, 981002",
        ):
            assert marker in text
        assert "Validação rápida na Tenable: Cloud Security" not in text
