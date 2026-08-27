from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.validate_project_guidance import validate_guidance


REQUIRED_FILES = (
    "README.md",
    "DESIGN.md",
    "docs/README.md",
    "docs/19-visao-geral-e-objetivos.md",
    "docs/20-arquitetura-e-fluxo-de-dados.md",
    "docs/21-catalogo-de-dados-e-metricas.md",
    "docs/22-guia-operacional.md",
    "docs/23-guia-de-desenvolvimento.md",
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

    def test_reports_broken_local_markdown_link(self) -> None:
        self.write_minimum_valid_tree()
        (self.root / "README.md").write_text(
            "[Documento ausente](docs/ausente.md)\n",
            encoding="utf-8",
        )

        issues = validate_guidance(self.root)

        self.assertIn("BROKEN_LOCAL_LINK", {item.code for item in issues})

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

    def test_cloud_fixture_renderer_builds_two_documents_from_one_hash(self) -> None:
        from scripts.render_cloud_report_fixture import render_cloud_fixture

        output_root = self.root / "cloud-prototype"
        manifest = render_cloud_fixture(output_root)

        self.assertEqual(
            {item["variant"] for item in manifest["documents"]},
            {"base", "expanded"},
        )
        self.assertEqual(
            len({item["dataset_sha256"] for item in manifest["documents"]}),
            1,
        )
        self.assertTrue((output_root / "cloud-modelo-base.docx").is_file())
        self.assertTrue((output_root / "cloud-modelo-ampliado.docx").is_file())
        self.assertTrue((output_root / "cloud-prototype-manifest.json").is_file())

    def test_cloud_fixture_loads_qa_toolkit_by_project_path(self) -> None:
        from scripts.render_cloud_report_fixture import _qa_toolkit

        toolkit = _qa_toolkit()

        self.assertTrue(callable(toolkit.inventory_docx))
        self.assertTrue(callable(toolkit.render_pdf))

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
            "Modelo Base",
            "Modelo Ampliado",
            "fotografia Cloud",
            "RelatorioCloudTenable",
        ):
            self.assertIn(required, documentation)

if __name__ == "__main__":
    unittest.main()
