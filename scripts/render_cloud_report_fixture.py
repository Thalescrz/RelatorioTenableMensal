from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from tenable_reports.config.profile import load_client_profile
from tenable_reports.presentation.cloud_report_docx import (
    CloudReportVariant,
    generate_cloud_report,
)


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "corporate" / "cloud-base-v1.docx"
PROFILE = ROOT / "clients" / "examples" / "client-profile.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, payload: Mapping[str, Any]) -> Path:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _host(index: int, *, total: int) -> dict[str, Any]:
    critical = max(1, 7 - index)
    high = max(2, total // 3)
    medium = max(1, total // 4)
    low = max(0, total - critical - high - medium)
    return {
        "kind": "virtual_machine",
        "asset_id": f"vm-demo-{index:02d}",
        "name": f"vm-demo-{index:02d}.invalid",
        "account_id": "account-demo-a" if index % 2 else "account-demo-b",
        "ip_addresses": [f"192.0.2.{10 + index}"],
        "repository_uri": None,
        "digest": None,
        "components": ["openssl", "linux-kernel"],
        "vulnerabilities": total,
        "critical": critical,
        "high": high,
        "medium": medium,
        "low": low,
    }


def _image(index: int, *, total: int) -> dict[str, Any]:
    critical = max(1, 5 - index)
    high = max(2, total // 3)
    medium = max(1, total // 4)
    low = max(0, total - critical - high - medium)
    return {
        "kind": "container_image",
        "asset_id": f"image-demo-{index:02d}",
        "name": f"image-demo-{index:02d}",
        "account_id": "account-demo-containers",
        "ip_addresses": [],
        "repository_uri": f"registry.invalid/demo/image-{index:02d}",
        "digest": f"sha256:demonstracao{index:02d}",
        "components": ["openssl", "application-runtime"],
        "vulnerabilities": total,
        "critical": critical,
        "high": high,
        "medium": medium,
        "low": low,
    }


def _asset_reference(asset: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: asset.get(key)
        for key in (
            "kind",
            "asset_id",
            "name",
            "account_id",
            "ip_addresses",
            "repository_uri",
            "digest",
            "components",
        )
    }


def _critical_cves(
    hosts: list[dict[str, Any]],
    images: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    scores: tuple[float | None, ...] = (9.9, 9.6, 0.0, None, 8.4)
    rows: list[dict[str, Any]] = []
    for index, vpr in enumerate(scores, start=1):
        cvss = round(9.9 - (index - 1) * 0.2, 1)
        assets = (
            _asset_reference(hosts[(index - 1) % len(hosts)]),
            _asset_reference(images[(index - 1) % len(images)]),
        )
        rows.append({
            "cve": f"CVE-2099-{1000 + index}",
            "severity": "CRITICAL",
            "vpr": vpr,
            "vpr_display": (
                "N/D" if vpr is None else "0" if vpr == 0 else f"{vpr:.1f}"
            ),
            "cvss": cvss,
            "cvss_display": f"{cvss:.1f}",
            "affected_assets": len(assets),
            "affected_virtual_machines": 1,
            "affected_container_images": 1,
            "components": ["openssl", f"demo-component-{index}"],
            "description": (
                "Descrição técnica sintética para homologação do relatório. "
                "O registro demonstra a apresentação de contexto, impacto e "
                "prioridade sem representar uma vulnerabilidade ou ativo real."
            ),
            "assets": list(assets),
        })
    return rows


def _correctable(
    critical: list[dict[str, Any]],
    hosts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    correction_types = (
        ("patch_update", "Patch/Atualização"),
        ("configuration", "Configuração"),
        ("version_upgrade", "Atualização de versão"),
        ("image_rebuild", "Reconstrução de imagem"),
        ("compensating_control", "Controle compensatório"),
    )
    rows: list[dict[str, Any]] = []
    for index in range(10):
        base = critical[index] if index < len(critical) else {
            "cve": f"CVE-2099-{2001 + index}",
            "severity": "HIGH" if index < 8 else "MEDIUM",
            "vpr": round(8.0 - index * 0.3, 1),
            "vpr_display": f"{round(8.0 - index * 0.3, 1):.1f}",
            "cvss": round(8.8 - index * 0.2, 1),
            "cvss_display": f"{round(8.8 - index * 0.2, 1):.1f}",
            "affected_assets": 1 + index % 3,
            "affected_virtual_machines": 1,
            "affected_container_images": 0,
            "components": [f"demo-component-{index + 1}"],
            "description": "Registro sintético com correção disponível.",
            "assets": [_asset_reference(hosts[index % len(hosts)])],
        }
        correction, display = correction_types[index % len(correction_types)]
        rows.append({
            **base,
            "correction_type": correction,
            "correction_type_display": display,
            "correction_origin": (
                "explicit" if index % 2 == 0 else "deterministic_rule"
            ),
            "recommended_action": (
                "Aplicar a atualização validada pelo fornecedor em janela de "
                "mudança e confirmar a remediação por nova verificação."
            ),
            "remediation_steps": [
                "Validar a versão alvo.",
                "Aplicar a correção em ambiente controlado.",
                "Executar nova verificação.",
            ],
            "correlated_findings": 1 + index,
            "software": f"demo-component-{index % 4 + 1}",
            "fixed_by": ([] if index == 3 else [f"{index + 2}.0.1"]),
            "fixed_by_display": (
                "N/D" if index == 3 else f"{index + 2}.0.1"
            ),
        })
    return rows


def _container_image_overview(
    images: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    severities = ("CRITICAL", "HIGH", "HIGH", "MEDIUM", "MEDIUM")
    overview = []
    for image_index, image in enumerate(images[:5], start=1):
        rows = []
        for row_index, severity in enumerate(severities, start=1):
            cve_index = 4000 + image_index * 10 + min(row_index, 2)
            vpr = round(9.8 - image_index * 0.2 - row_index * 0.3, 1)
            fixed_by = None if row_index == 5 else f"{row_index + 1}.2.0"
            rows.append(
                {
                    "cve": f"CVE-2099-{cve_index}",
                    "severity": severity,
                    "vpr": vpr,
                    "vpr_display": f"{vpr:.1f}",
                    "software": f"demo-library-{row_index}",
                    "fixed_by": fixed_by,
                    "fixed_by_display": fixed_by or "N/D",
                }
            )
        overview.append({"asset": image, "rows": rows})
    return overview


def sanitized_cloud_dataset() -> dict[str, Any]:
    hosts = [_host(index, total=44 - index * 4) for index in range(1, 7)]
    images = [_image(index, total=35 - index * 4) for index in range(1, 6)]
    critical = _critical_cves(hosts, images)
    return {
        "schema_version": 1,
        "document_kind": "cloud",
        "metric_definition_version": "cloud-metrics-v2",
        "connector_version": "cloud-graphql-v1",
        "period": {
            "start_at": "2026-07-01T03:00:00+00:00",
            "end_at": "2026-08-01T03:00:00+00:00",
            "reference_at": "2026-08-01T12:00:00+00:00",
            "timezone": "America/Fortaleza",
            "period_id": "2026-07",
        },
        "collected_at": "2026-08-01T12:00:00+00:00",
        "snapshot_context": {
            "historical_reconstruction": "EXACT_SNAPSHOT",
            "warning": None,
        },
        "overview": {
            "assets": 11,
            "virtual_machines": 6,
            "container_images": 5,
            "vulnerability_occurrences": 247,
            "unique_cves": 73,
            "severity_counts": {
                "CRITICAL": 28,
                "HIGH": 86,
                "MEDIUM": 101,
                "LOW": 32,
            },
            "posture_findings": 18,
        },
        "top_critical_cves": critical,
        "top_vulnerable_hosts": hosts,
        "top_vulnerable_images": images,
        "container_image_vulnerability_overview": _container_image_overview(images),
        "workload_status": {
            "total_virtual_machines": 6,
            "by_max_severity": {
                "CRITICAL": 3,
                "HIGH": 2,
                "MEDIUM": 1,
                "LOW": 0,
                "NONE": 0,
            },
        },
        "top_components": [
            {
                "component": "openssl",
                "affected_assets": 8,
                "vulnerabilities": 19,
                "occurrences": 42,
            },
            {
                "component": "linux-kernel",
                "affected_assets": 6,
                "vulnerabilities": 14,
                "occurrences": 31,
            },
            {
                "component": "application-runtime",
                "affected_assets": 5,
                "vulnerabilities": 11,
                "occurrences": 24,
            },
        ],
        "top_posture_findings": [
            {
                "policy": "Criptografia de armazenamento",
                "category": "Data Protection",
                "severity": "HIGH",
                "provider": "Cloud Demo",
                "findings": 7,
                "affected_resources": 4,
            },
            {
                "policy": "Exposição pública de serviço",
                "category": "Network",
                "severity": "CRITICAL",
                "provider": "Cloud Demo",
                "findings": 5,
                "affected_resources": 3,
            },
            {
                "policy": "Privilégio administrativo amplo",
                "category": "IAM",
                "severity": "HIGH",
                "provider": "Cloud Demo",
                "findings": 6,
                "affected_resources": 5,
            },
        ],
        "top_correctable_vulnerabilities": _correctable(critical, hosts),
        "aging": {
            "0-30": 74,
            "31-60": 48,
            "61-90": 39,
            "91-180": 51,
            ">180": 29,
            "data_indisponivel": 6,
        },
        "remediation_performance": {
            "resolved": 37,
            "average_resolution_days": 18.4,
            "period_interval": "[start_at, end_at)",
        },
        "inventory": {
            "total_resources": 42,
            "by_provider": [
                {"provider": "Cloud Demo A", "resources": 25},
                {"provider": "Cloud Demo B", "resources": 17},
            ],
            "by_region": [
                {"region": "demo-south-1", "resources": 21},
                {"region": "demo-east-1", "resources": 13},
                {"region": "demo-west-1", "resources": 8},
            ],
        },
        "source_status": {
            "virtual_machines": "COMPLETE",
            "container_images": "COMPLETE",
            "findings": "COMPLETE",
            "inventory": "COMPLETE",
            "lifecycle": "COMPLETE",
        },
        "quality_issues": [],
        "capabilities": {
            "required_ready": True,
            "sources": {
                "virtual_machines": "AVAILABLE",
                "container_images": "AVAILABLE",
                "findings": "AVAILABLE",
                "inventory": "AVAILABLE",
                "lifecycle": "AVAILABLE",
            },
        },
        "history": [
            {
                "period_id": "2026-05",
                "label": "Mai/26",
                "availability": "AVAILABLE",
                "overview": {"vulnerability_occurrences": 219},
            },
            {
                "period_id": "2026-06",
                "label": "Jun/26",
                "availability": "AVAILABLE",
                "overview": {"vulnerability_occurrences": 232},
            },
            {
                "period_id": "2026-07",
                "label": "Jul/26",
                "availability": "AVAILABLE",
                "overview": {"vulnerability_occurrences": 247},
            },
        ],
        "table_provenance": {"schema_version": 1, "tables": {}},
    }


def _find_libreoffice(explicit: Path | None = None) -> Path:
    candidates = (
        explicit,
        Path("C:/Codex/LibreOfficePortable/App/libreoffice/program/soffice.exe"),
        Path("C:/Program Files/LibreOffice/program/soffice.exe"),
    )
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate.resolve()
    discovered = shutil.which("soffice")
    if discovered:
        return Path(discovered).resolve()
    raise FileNotFoundError("LibreOffice não encontrado para a validação visual.")


def _qa_toolkit():
    source = ROOT / "tools" / "docx_inventory.py"
    spec = importlib.util.spec_from_file_location(
        "tenable_reports_cloud_fixture_docx_inventory",
        source,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Não foi possível carregar a ferramenta de QA: {source}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _render_qa(
    *,
    output_root: Path,
    documents: list[dict[str, Any]],
    libreoffice: Path | None,
) -> None:
    qa_toolkit = _qa_toolkit()
    executable = _find_libreoffice(libreoffice)
    for document in documents:
        docx = Path(document["path"])
        pdf = output_root / f"{docx.stem}.pdf"
        if pdf.is_file():
            pdf.unlink()
        subprocess.run(
            [
                str(executable),
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(output_root),
                str(docx),
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if not pdf.is_file():
            raise RuntimeError(f"LibreOffice não gerou o PDF esperado: {pdf}")
        qa_root = (output_root / f"qa-{document['variant']}").resolve()
        if not qa_root.is_relative_to(output_root.resolve()):
            raise RuntimeError("Diretório de QA fora da saída permitida.")
        if qa_root.exists():
            shutil.rmtree(qa_root)
        qa_root.mkdir(parents=True)
        _write_json(qa_root / "structure.json", qa_toolkit.inventory_docx(docx))
        pages = qa_toolkit.render_pdf(pdf, qa_root / "pages", 144)
        sheets = qa_toolkit.make_contact_sheets(pages, qa_root)
        document["page_count"] = len(pages)
        document["qa"] = {
            "status": "RENDERED_PENDING_VISUAL_REVIEW",
            "pdf": str(pdf.resolve()),
            "structure": str((qa_root / "structure.json").resolve()),
            "contact_sheets": [str(path.resolve()) for path in sheets],
        }


def render_cloud_fixture(
    output_root: Path,
    *,
    run_qa: bool = False,
    libreoffice: Path | None = None,
) -> dict[str, Any]:
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    dataset_path = _write_json(
        output_root / "cloud-report-dataset.json",
        sanitized_cloud_dataset(),
    )
    profile = load_client_profile(PROFILE)
    profile = replace(
        profile,
        display_name="Cliente Demonstração",
        cloud_security_scope=replace(
            profile.cloud_security_scope,
            enabled=True,
            layout="expanded",
        ),
    )
    documents: list[dict[str, Any]] = []
    targets = ((
        CloudReportVariant.EXPANDED,
        output_root / "cloud-relatorio-padrao.docx",
    ),)
    for variant, output_path in targets:
        result = generate_cloud_report(
            template_path=TEMPLATE,
            dataset_path=dataset_path,
            profile=profile,
            output_path=output_path,
            variant=variant,
        )
        documents.append({
            "variant": variant.value,
            "path": str(output_path.resolve()),
            "dataset_sha256": result.dataset_sha256,
            "docx_sha256": _sha256(output_path),
            "page_count": None,
            "rendered_sections": list(result.rendered_sections),
            "omitted_sections": list(result.omitted_sections),
            "coverage_warnings": [],
            "qa": {"status": "NOT_RENDERED"},
        })
    if run_qa:
        _render_qa(
            output_root=output_root,
            documents=documents,
            libreoffice=libreoffice,
        )
    dataset_hashes = {item["dataset_sha256"] for item in documents}
    if len(dataset_hashes) != 1:
        raise RuntimeError("O relatório padrão não preservou o hash do dataset.")
    manifest = {
        "schema_version": 1,
        "fixture": {
            "sanitized": True,
            "contains_real_tenant_data": False,
            "client_id": profile.client_id,
            "period_id": "2026-07",
        },
        "dataset": {
            "path": str(dataset_path.resolve()),
            "sha256": _sha256(dataset_path),
        },
        "documents": documents,
    }
    _write_json(output_root / "cloud-prototype-manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Gera o relatório Cloud padrão com dados sanitizados."
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "artifacts" / "cloud-prototype",
    )
    parser.add_argument(
        "--qa",
        action="store_true",
        help="Renderiza PDFs e contact sheets com LibreOffice.",
    )
    parser.add_argument("--libreoffice", type=Path)
    args = parser.parse_args()
    manifest = render_cloud_fixture(
        args.output_root,
        run_qa=args.qa,
        libreoffice=args.libreoffice,
    )
    print(json.dumps({
        "status": "OK",
        "manifest": str(
            (args.output_root.resolve() / "cloud-prototype-manifest.json")
        ),
        "documents": [
            {
                "variant": item["variant"],
                "path": item["path"],
                "page_count": item["page_count"],
            }
            for item in manifest["documents"]
        ],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())