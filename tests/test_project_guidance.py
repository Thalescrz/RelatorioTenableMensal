from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from tools.validate_project_guidance import validate_guidance


REQUIRED_FILES = (
    "CONTEXTO.md",
    "README.md",
    "DESIGN.md",
    "docs/README.md",
    "docs/19-visao-geral-e-objetivos.md",
    "docs/20-arquitetura-e-fluxo-de-dados.md",
    "docs/21-catalogo-de-dados-e-metricas.md",
    "docs/22-guia-operacional.md",
    "docs/23-guia-de-desenvolvimento.md",
    "docs/24-integracao-apis-tenable-cloud-e-licenciamento.md",
    "docs/25-implantacao-windows-linux.md",
    "AGENTS.md",
    "src/tenable_reports/AGENTS.md",
    "tests/AGENTS.md",
    "clients/AGENTS.md",
    ".agents/skills/operating-tenable-reports/SKILL.md",
    ".agents/skills/operating-tenable-reports/references/runbook.md",
    ".agents/skills/validating-tenable-report-data/SKILL.md",
    ".agents/skills/validating-tenable-report-data/references/data-contract.md",
)


class ProjectGuidanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def write_minimum_valid_tree(self) -> None:
        for relative in REQUIRED_FILES:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.name != "SKILL.md":
                path.write_text("# Documento válido\n", encoding="utf-8")
                continue
            skill_name = path.parent.name
            reference_name = (
                "runbook.md"
                if skill_name == "operating-tenable-reports"
                else "data-contract.md"
            )
            path.write_text(
                "---\n"
                f"name: {skill_name}\n"
                "description: Use when exercising a project reference skill.\n"
                "---\n\n"
                f"# Skill\n\n[Referência](references/{reference_name})\n",
                encoding="utf-8",
            )

    def test_reports_missing_required_guidance_files(self) -> None:
        issues = validate_guidance(self.root)

        self.assertIn("MISSING_REQUIRED_FILE", {item.code for item in issues})

    def test_requires_project_context_document(self) -> None:
        self.write_minimum_valid_tree()
        (self.root / "CONTEXTO.md").unlink()

        issues = validate_guidance(self.root)

        self.assertIn(
            ("MISSING_REQUIRED_FILE", "CONTEXTO.md"),
            {(item.code, item.path) for item in issues},
        )

    def test_requires_current_cloud_api_and_licensing_guide(self) -> None:
        self.write_minimum_valid_tree()
        guide = self.root / "docs/24-integracao-apis-tenable-cloud-e-licenciamento.md"
        guide.unlink()

        issues = validate_guidance(self.root)

        self.assertIn(
            (
                "MISSING_REQUIRED_FILE",
                "docs/24-integracao-apis-tenable-cloud-e-licenciamento.md",
            ),
            {(item.code, item.path) for item in issues},
        )

    def test_reports_broken_local_markdown_link(self) -> None:
        self.write_minimum_valid_tree()
        (self.root / "README.md").write_text(
            "[Documento ausente](docs/ausente.md)\n",
            encoding="utf-8",
        )

        issues = validate_guidance(self.root)

        self.assertIn("BROKEN_LOCAL_LINK", {item.code for item in issues})

    def test_reports_broken_link_in_historical_markdown(self) -> None:
        self.write_minimum_valid_tree()
        historical = self.root / "docs/01-historico.md"
        historical.write_text(
            "[Ausente](referencia-ausente.md)\n",
            encoding="utf-8",
        )

        issues = validate_guidance(self.root)

        self.assertIn(
            ("BROKEN_LOCAL_LINK", "docs/01-historico.md"),
            {(item.code, item.path) for item in issues},
        )

    def test_ignores_runtime_markdown_directories(self) -> None:
        self.write_minimum_valid_tree()
        runtime = self.root / ".tmp/session/nota.md"
        runtime.parent.mkdir(parents=True)
        runtime.write_text("[Ausente](arquivo.md)\n", encoding="utf-8")

        self.assertEqual(validate_guidance(self.root), ())

    def test_ignores_offline_release_copies(self) -> None:
        self.write_minimum_valid_tree()
        release_copy = (
            self.root
            / "deployment-transfers/releases/linux/tenable-reports/docs/README.md"
        )
        release_copy.parent.mkdir(parents=True)
        release_copy.write_text("[Ausente](../tests/AGENTS.md)\n", encoding="utf-8")

        excluded_root = self.root / "deployment-transfers"
        original_scandir = os.scandir

        def guarded_scandir(path):
            candidate = Path(path)
            if candidate == excluded_root or excluded_root in candidate.parents:
                raise AssertionError("offline release directory was traversed")
            return original_scandir(path)

        with mock.patch("os.scandir", side_effect=guarded_scandir):
            self.assertEqual(validate_guidance(self.root), ())

    def test_ignores_links_inside_fenced_code_blocks(self) -> None:
        self.write_minimum_valid_tree()
        historical = self.root / "docs/01-historico.md"
        historical.write_text(
            "```markdown\n[Exemplo](arquivo-ausente.md)\n```\n",
            encoding="utf-8",
        )

        self.assertEqual(validate_guidance(self.root), ())

    def test_ignores_links_inside_inline_code_spans(self) -> None:
        self.write_minimum_valid_tree()
        historical = self.root / "docs/01-historico.md"
        historical.write_text(
            "Exemplo: `[Documento](arquivo-ausente.md)`.\n",
            encoding="utf-8",
        )

        self.assertEqual(validate_guidance(self.root), ())

    def test_reports_invalid_skill_frontmatter(self) -> None:
        self.write_minimum_valid_tree()
        skill = (
            self.root
            / ".agents/skills/operating-tenable-reports/SKILL.md"
        )
        skill.write_text("# Sem frontmatter\n", encoding="utf-8")

        issues = validate_guidance(self.root)

        self.assertIn("INVALID_SKILL_FRONTMATTER", {item.code for item in issues})

    def test_reports_scaffold_marker(self) -> None:
        self.write_minimum_valid_tree()
        (self.root / "docs/README.md").write_text(
            "# Documentação\n\nT" + "ODO: completar\n",
            encoding="utf-8",
        )

        issues = validate_guidance(self.root)

        self.assertIn("SCAFFOLD_MARKER", {item.code for item in issues})

    def test_accepts_complete_guidance_tree(self) -> None:
        self.write_minimum_valid_tree()

        self.assertEqual(validate_guidance(self.root), ())

    def test_cloud_fixture_renderer_builds_one_standard_document(self) -> None:
        from scripts.render_cloud_report_fixture import render_cloud_fixture

        output_root = self.root / "cloud-prototype"
        manifest = render_cloud_fixture(output_root)

        self.assertEqual(len(manifest["documents"]), 1)
        self.assertEqual(manifest["documents"][0]["variant"], "expanded")
        self.assertEqual(
            len({item["dataset_sha256"] for item in manifest["documents"]}),
            1,
        )
        self.assertTrue((output_root / "cloud-relatorio-padrao.docx").is_file())
        self.assertTrue((output_root / "cloud-prototype-manifest.json").is_file())

    def test_cloud_fixture_loads_qa_toolkit_by_project_path(self) -> None:
        from scripts.render_cloud_report_fixture import _qa_toolkit

        toolkit = _qa_toolkit()

        self.assertTrue(callable(toolkit.inventory_docx))
        self.assertTrue(callable(toolkit.render_pdf))

    def test_cloud_fixture_correctable_rankings_have_consistent_totals(self) -> None:
        from scripts.render_cloud_report_fixture import sanitized_cloud_dataset

        dataset = sanitized_cloud_dataset()

        for key in (
            "top_correctable_virtual_machines",
            "top_correctable_container_images",
        ):
            for row in dataset[key]:
                severity_total = sum(
                    int(row[severity])
                    for severity in ("critical", "high", "medium", "low")
                )
                self.assertEqual(
                    severity_total,
                    row["correctable_vulnerabilities"],
                )

    def test_current_guidance_documents_complete_cloud_workflow(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        documentation = "\n".join(
            (project_root / relative).read_text(encoding="utf-8")
            for relative in (
                "README.md",
                "docs/19-visao-geral-e-objetivos.md",
                "docs/20-arquitetura-e-fluxo-de-dados.md",
                "docs/21-catalogo-de-dados-e-metricas.md",
                "docs/22-guia-operacional.md",
                "docs/23-guia-de-desenvolvimento.md",
                "templates/corporate/README.md",
            )
        )

        for required in (
            "TCS_API_SECRET",
            "Testar API Cloud",
            "Tentar Cloud novamente",
            "relatório Cloud padrão",
            "fotografia Cloud",
            "RelatorioCloudTenable",
        ):
            self.assertIn(required, documentation)

    def test_current_guidance_documents_cover_durable_batch_controls(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        documentation = "\n".join(
            (project_root / relative).read_text(encoding="utf-8")
            for relative in (
                "README.md",
                "docs/20-arquitetura-e-fluxo-de-dados.md",
                "docs/22-guia-operacional.md",
                "docs/23-guia-de-desenvolvimento.md",
                ".agents/skills/operating-tenable-reports/references/runbook.md",
            )
        )

        for required in (
            "Pausar após o atual",
            "Parar lote",
            "Retomar lote",
            "Tentar falhas/interrompidos",
            "Gerar todos novamente",
            "COMPLETE_WITH_FAILURES",
            "CANCELLED_BY_USER",
            "export remoto",
            "import-web-batch-recovery",
            "rollback",
            "fila sequencial",
        ):
            self.assertIn(required, documentation)

    def test_current_guidance_documents_cover_offline_cross_platform_deployment(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        documentation = "\n".join(
            (project_root / relative).read_text(encoding="utf-8")
            for relative in (
                "README.md",
                "CONTEXTO.md",
                "docs/20-arquitetura-e-fluxo-de-dados.md",
                "docs/22-guia-operacional.md",
                "docs/23-guia-de-desenvolvimento.md",
                "docs/25-implantacao-windows-linux.md",
                "templates/corporate/README.md",
            )
        )

        for required in (
            "Windows x86_64",
            "Ubuntu Server 24.04 x86_64",
            "windows-calibri-v1",
            "linux-carlito-v1",
            "build_offline_bundle.py",
            "verify_offline_bundle.py",
            "deployment-export",
            "deployment-import",
            "IMPORT <transfer_id>",
            "systemd",
            "sem acesso à internet",
            "não inicia coleta",
        ):
            self.assertIn(required, documentation)


if __name__ == "__main__":
    unittest.main()
