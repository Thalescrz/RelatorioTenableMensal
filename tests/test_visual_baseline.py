from __future__ import annotations

import json
import sys
import tomllib
import warnings
import zipfile
from pathlib import Path
from types import SimpleNamespace

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]


def _write_docx(path: Path, *, created: str, body: str = "fixture") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as package:
        package.writestr(
            "docProps/core.xml",
            (
                "<cp:coreProperties "
                "xmlns:cp=\"http://schemas.openxmlformats.org/package/2006/metadata/core-properties\" "
                "xmlns:dcterms=\"http://purl.org/dc/terms/\">"
                f"<dcterms:created>{created}</dcterms:created>"
                f"<dcterms:modified>{created}</dcterms:modified>"
                "<cp:lastModifiedBy>fixture-runner</cp:lastModifiedBy>"
                "</cp:coreProperties>"
            ),
        )
        package.writestr("word/document.xml", f"<document>{body}</document>")


def test_pytest_configuration_ignores_operational_directories() -> None:
    configuration = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert configuration["tool"]["pytest"]["ini_options"]["testpaths"] == ["tests"]


def test_capture_manifest_covers_all_official_report_kinds(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tools import capture_visual_baseline as visual

    project_root = tmp_path / "project"
    output_root = tmp_path / "candidate"
    soffice = tmp_path / "office" / "soffice.exe"
    soffice.parent.mkdir(parents=True)
    soffice.write_bytes(b"fixture")

    def generate_documents(*, project_root: Path, output_root: Path):
        documents = {}
        for kind in ("general", "customizations", "tag", "cloud"):
            document = output_root / "documents" / f"{kind}.docx"
            _write_docx(document, created="2099-01-01T00:00:00Z", body=kind)
            documents[kind] = document
        return documents

    def render_document(*, document_path: Path, output_root: Path, soffice: Path):
        pdf = output_root / "pdf" / f"{document_path.stem}.pdf"
        pdf.parent.mkdir(parents=True, exist_ok=True)
        pdf.write_bytes(b"%PDF-fixture")
        page = output_root / "pages" / document_path.stem / "page-001.png"
        page.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (120, 160), "white").save(page)
        return pdf, (page,)

    monkeypatch.setattr(visual, "_generate_report_documents", generate_documents)
    monkeypatch.setattr(visual, "_render_document", render_document)
    monkeypatch.setattr(visual, "_libreoffice_version", lambda _: "LibreOffice 26.2.4.2")
    monkeypatch.setattr(visual.platform, "system", lambda: "Windows")
    monkeypatch.setattr(visual.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(sys, "version_info", (3, 14, 5, "final", 0))

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        manifest_path = visual.capture_visual_baseline(
            project_root=project_root,
            output_root=output_root,
            soffice=soffice,
        )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "visual-baseline-v1"
    assert manifest["profile"] == "windows-calibri-v1"
    assert {report["kind"] for report in manifest["reports"]} == {
        "general",
        "customizations",
        "tag",
        "cloud",
    }
    assert all(report["page_count"] == 1 for report in manifest["reports"])
    assert all(report["pages"][0]["width"] == 120 for report in manifest["reports"])
    assert all(not Path(report["docx"]).is_absolute() for report in manifest["reports"])
    serialized = manifest_path.read_text(encoding="utf-8")
    assert str(tmp_path) not in serialized
    assert "cliente" not in serialized.casefold()


def test_normalized_docx_comparison_ignores_only_volatile_metadata(
    tmp_path: Path,
) -> None:
    from tools.capture_visual_baseline import normalized_docx_digest

    first = tmp_path / "first.docx"
    second = tmp_path / "second.docx"
    changed = tmp_path / "changed.docx"
    _write_docx(first, created="2026-10-16T10:00:00Z")
    _write_docx(second, created="2026-10-16T11:00:00Z")
    _write_docx(changed, created="2026-10-16T11:00:00Z", body="changed")

    assert normalized_docx_digest(first) == normalized_docx_digest(second)
    assert normalized_docx_digest(first) != normalized_docx_digest(changed)


def test_cloud_fixture_loader_does_not_require_repo_on_sys_path(monkeypatch) -> None:
    from tools import capture_visual_baseline as visual

    monkeypatch.setattr(
        sys,
        "path",
        [entry for entry in sys.path if Path(entry or ".").resolve() != ROOT],
    )

    builder = visual._load_sanitized_cloud_dataset_builder(ROOT)

    assert callable(builder)
    assert builder()["schema_version"] == 1


def test_render_prepare_loader_does_not_require_repo_on_sys_path(monkeypatch) -> None:
    from tools import capture_visual_baseline as visual

    monkeypatch.setattr(
        sys,
        "path",
        [entry for entry in sys.path if Path(entry or ".").resolve() != ROOT],
    )

    prepare = visual._load_render_prepare(ROOT)

    assert callable(prepare)


def test_libreoffice_version_uses_console_binary_without_window_on_windows(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tools import capture_visual_baseline as visual

    executable = tmp_path / "program" / "soffice.exe"
    console_executable = executable.with_suffix(".com")
    console_executable.parent.mkdir(parents=True)
    executable.write_bytes(b"fixture")
    console_executable.write_bytes(b"fixture")
    observed = {}

    def runner(command, **kwargs):
        observed["command"] = command
        observed["creationflags"] = kwargs.get("creationflags")
        return SimpleNamespace(stdout="LibreOffice 26.2.4.2\n", stderr="")

    monkeypatch.setattr(visual.platform, "system", lambda: "Windows")

    assert visual._libreoffice_version(executable, runner=runner) == "LibreOffice 26.2.4.2"
    assert observed["command"] == [str(console_executable), "--version"]
    assert observed["creationflags"] == visual.subprocess.CREATE_NO_WINDOW
