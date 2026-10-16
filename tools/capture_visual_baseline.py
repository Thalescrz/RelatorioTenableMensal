"""Capture deterministic, sanitized visual evidence for official reports."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import importlib.metadata
import json
import platform
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree

from PIL import Image


REPORT_KINDS = ("general", "customizations", "tag", "cloud")
SCHEMA_VERSION = "visual-baseline-v1"
_VOLATILE_CORE_NAMES = {
    "created",
    "modified",
    "lastModifiedBy",
    "lastPrinted",
    "revision",
}


def _normalized_member(name: str, payload: bytes) -> bytes:
    if name != "docProps/core.xml":
        return payload
    root = ElementTree.fromstring(payload)
    for element in root.iter():
        local_name = element.tag.rsplit("}", 1)[-1]
        if local_name in _VOLATILE_CORE_NAMES:
            element.text = ""
    return ElementTree.tostring(root, encoding="utf-8")


def normalized_docx_digest(path: Path) -> str:
    """Hash a DOCX while ignoring only known volatile core properties."""

    digest = hashlib.sha256()
    with zipfile.ZipFile(path) as package:
        for member in sorted(item for item in package.namelist() if not item.endswith("/")):
            payload = _normalized_member(member, package.read(member))
            digest.update(member.encode("utf-8"))
            digest.update(b"\0")
            digest.update(payload)
            digest.update(b"\0")
    return digest.hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pixel_signature(path: Path) -> str:
    with Image.open(path) as image:
        gray = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
        pixels = list(gray.get_flattened_data())
    bits = []
    for row in range(8):
        offset = row * 9
        bits.extend(pixels[offset + column] > pixels[offset + column + 1] for column in range(8))
    value = sum((1 << index) for index, enabled in enumerate(bits) if enabled)
    return f"{value:016x}"


def _library_versions() -> dict[str, str]:
    result: dict[str, str] = {}
    for distribution in ("Pillow", "python-docx", "docxcompose"):
        try:
            result[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            result[distribution] = "UNAVAILABLE"
    return result


def _silent_soffice(soffice: Path) -> tuple[Path, int]:
    executable = soffice
    creationflags = 0
    if platform.system() == "Windows":
        console_executable = soffice.with_suffix(".com")
        if console_executable.is_file():
            executable = console_executable
        creationflags = subprocess.CREATE_NO_WINDOW
    return executable, creationflags


def _libreoffice_version(soffice: Path, *, runner=subprocess.run) -> str:
    executable, creationflags = _silent_soffice(soffice)
    completed = runner(
        [str(executable), "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=creationflags,
    )
    return (completed.stdout or completed.stderr).strip()


def _profile_for_host() -> str:
    return "windows-calibri-v1" if platform.system() == "Windows" else "linux-carlito-v1"


def _safe_tag_dataset(source: Path, destination: Path) -> Path:
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["document_kind"] = "tag"
    payload["tag"] = {
        "tag_uuid": "tag-fixture",
        "category_uuid": "category-fixture",
        "category_name": "Categoria de Teste",
        "value": "Escopo Sanitizado",
        "include_temporal_comparison": False,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return destination


def _load_sanitized_cloud_dataset_builder(project_root: Path):
    return _load_repo_callable(
        project_root,
        Path("scripts/render_cloud_report_fixture.py"),
        "sanitized_cloud_dataset",
    )


def _load_repo_callable(project_root: Path, relative_path: Path, name: str):
    module_path = project_root / relative_path
    specification = importlib.util.spec_from_file_location(
        f"tenable_reports_visual_{module_path.stem}_{name}",
        module_path,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError(f"Nao foi possivel carregar {module_path.name}.")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    callable_value = getattr(module, name, None)
    if not callable(callable_value):
        raise RuntimeError(f"{module_path.name} nao expoe {name}.")
    return callable_value


def _load_render_prepare(project_root: Path):
    return _load_repo_callable(
        project_root,
        Path("scripts/prepare_docx_qa_render.py"),
        "prepare",
    )


def _load_pdf_renderer(project_root: Path):
    return _load_repo_callable(project_root, Path("tools/docx_inventory.py"), "render_pdf")


def _generate_report_documents(*, project_root: Path, output_root: Path) -> dict[str, Path]:
    from tenable_reports.config.profile import load_client_profile
    from tenable_reports.presentation.cloud_report_docx import (
        CloudReportVariant,
        generate_cloud_report,
    )
    from tenable_reports.presentation.customizations_report_docx import (
        generate_customizations_report,
    )
    from tenable_reports.presentation.full_base_report_docx import generate_full_base_report
    from tenable_reports.presentation.tag_report_docx import generate_tag_report

    documents = output_root / "documents"
    inputs = output_root / "inputs"
    documents.mkdir(parents=True, exist_ok=True)
    inputs.mkdir(parents=True, exist_ok=True)
    template = project_root / "templates" / "corporate" / "base-v1.docx"
    dataset = project_root / "tests" / "fixtures" / "report-dataset-phase5.json"
    profile_path = project_root / "clients" / "examples" / "client-profile.json"
    custom_profile_path = (
        project_root / "clients" / "examples" / "client-profile-all-customizations.json"
    )
    profile = load_client_profile(profile_path)
    sanitized_cloud_dataset = _load_sanitized_cloud_dataset_builder(project_root)

    general = documents / "general.docx"
    generate_full_base_report(
        template_path=template,
        dataset_path=dataset,
        profile=profile,
        output_path=general,
        mask_sensitive=True,
    )

    customizations = documents / "customizations.docx"
    generate_customizations_report(
        template_path=template,
        dataset_path=dataset,
        profile=load_client_profile(custom_profile_path),
        output_path=customizations,
        mask_sensitive=True,
    )

    tag = documents / "tag.docx"
    generate_tag_report(
        template_path=template,
        dataset_path=_safe_tag_dataset(dataset, inputs / "tag-dataset.json"),
        profile=profile,
        output_path=tag,
        mask_sensitive=True,
    )

    cloud_data = inputs / "cloud-dataset.json"
    cloud_data.write_text(
        json.dumps(sanitized_cloud_dataset(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    cloud_profile = replace(
        profile,
        cloud_security_scope=replace(
            profile.cloud_security_scope,
            enabled=True,
            layout="comparison",
        ),
    )
    cloud = documents / "cloud.docx"
    generate_cloud_report(
        template_path=template,
        dataset_path=cloud_data,
        profile=cloud_profile,
        output_path=cloud,
        variant=CloudReportVariant.EXPANDED,
        mask_sensitive=True,
    )
    return {
        "general": general,
        "customizations": customizations,
        "tag": tag,
        "cloud": cloud,
    }


def _render_document(
    *,
    document_path: Path,
    output_root: Path,
    soffice: Path,
) -> tuple[Path, tuple[Path, ...]]:
    project_root = Path(__file__).resolve().parents[1]
    prepare = _load_render_prepare(project_root)
    render_pdf = _load_pdf_renderer(project_root)

    render_input = output_root / "render-input" / document_path.name
    render_input.parent.mkdir(parents=True, exist_ok=True)
    prepare(document_path, render_input)
    pdf_root = output_root / "pdf"
    pdf_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tenable-lo-") as user_profile:
        profile_uri = Path(user_profile).resolve().as_uri()
        executable, creationflags = _silent_soffice(soffice)
        completed = subprocess.run(
            [
                str(executable),
                "--headless",
                f"-env:UserInstallation={profile_uri}",
                "--convert-to",
                "pdf",
                "--outdir",
                str(pdf_root),
                str(render_input),
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            creationflags=creationflags,
        )
    pdf = pdf_root / f"{render_input.stem}.pdf"
    if completed.returncode != 0 or not pdf.is_file():
        diagnostic = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(f"LibreOffice nao gerou {pdf.name}: {diagnostic}")
    pages = tuple(render_pdf(pdf, output_root / "pages" / document_path.stem, 144))
    if not pages:
        raise RuntimeError(f"PDF sem paginas renderizadas: {pdf.name}")
    return pdf, pages


def _relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _report_entry(
    *,
    kind: str,
    document: Path,
    pdf: Path,
    pages: Iterable[Path],
    output_root: Path,
) -> dict[str, object]:
    page_entries = []
    for page in pages:
        with Image.open(page) as image:
            width, height = image.size
        page_entries.append(
            {
                "file": _relative(page, output_root),
                "width": width,
                "height": height,
                "sha256": _sha256(page),
                "dhash": _pixel_signature(page),
            }
        )
    return {
        "kind": kind,
        "docx": _relative(document, output_root),
        "normalized_docx_sha256": normalized_docx_digest(document),
        "pdf": _relative(pdf, output_root),
        "pdf_sha256": _sha256(pdf),
        "page_count": len(page_entries),
        "pages": page_entries,
    }


def capture_visual_baseline(
    *,
    project_root: Path,
    output_root: Path,
    soffice: Path,
) -> Path:
    project_root = project_root.resolve()
    output_root = output_root.resolve()
    soffice = soffice.resolve()
    if not soffice.is_file():
        raise FileNotFoundError(f"LibreOffice nao encontrado: {soffice.name}")
    output_root.mkdir(parents=True, exist_ok=True)
    documents = _generate_report_documents(
        project_root=project_root,
        output_root=output_root,
    )
    if tuple(documents) != REPORT_KINDS:
        raise RuntimeError("A captura deve produzir os quatro tipos oficiais.")

    reports = []
    for kind in REPORT_KINDS:
        document = documents[kind]
        pdf, pages = _render_document(
            document_path=document,
            output_root=output_root,
            soffice=soffice,
        )
        reports.append(
            _report_entry(
                kind=kind,
                document=document,
                pdf=pdf,
                pages=pages,
                output_root=output_root,
            )
        )

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "profile": _profile_for_host(),
        "environment": {
            "platform": platform.system(),
            "architecture": platform.machine(),
            "python": ".".join(str(item) for item in sys.version_info[:3]),
            "libraries": _library_versions(),
            "libreoffice": _libreoffice_version(soffice),
            "font_families": ["Calibri", "Arial"]
            if platform.system() == "Windows"
            else ["Carlito", "Liberation Sans"],
        },
        "reports": reports,
    }
    manifest_path = output_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--soffice", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    expected_profile = _profile_for_host()
    if args.profile != expected_profile:
        parser.error(
            f"perfil {args.profile!r} incompativel com o host; esperado {expected_profile!r}"
        )
    manifest = capture_visual_baseline(
        project_root=args.project_root,
        output_root=args.output_root,
        soffice=args.soffice,
    )
    print(json.dumps({"status": "ok", "manifest": str(manifest)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
