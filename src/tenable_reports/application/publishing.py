from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from zipfile import BadZipFile, ZipFile
from xml.etree import ElementTree


REQUIRED_DOCX_MEMBERS = {
    "[Content_Types].xml",
    "word/document.xml",
    "word/styles.xml",
}


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_docx_package(path: str | Path) -> dict[str, Any]:
    document = Path(path)
    if not document.is_file():
        raise ValueError(f"Documento nao encontrado: {document}")
    try:
        with ZipFile(document) as package:
            members = set(package.namelist())
            missing = sorted(REQUIRED_DOCX_MEMBERS - members)
            corrupt_member = package.testzip()
            required_xml = {
                name: package.read(name)
                for name in REQUIRED_DOCX_MEMBERS
                if name in members
            }
    except BadZipFile as exc:
        raise ValueError(f"DOCX invalido: {document}") from exc
    if missing:
        raise ValueError(
            f"DOCX sem membros obrigatorios ({', '.join(missing)}): {document}"
        )
    if corrupt_member:
        raise ValueError(f"DOCX corrompido em {corrupt_member}: {document}")
    parsed_xml: dict[str, ElementTree.Element] = {}
    for name, content in required_xml.items():
        if not content.strip():
            raise ValueError(f"DOCX possui XML obrigatório vazio: {name}")
        try:
            parsed_xml[name] = ElementTree.fromstring(content)
        except ElementTree.ParseError as exc:
            raise ValueError(f"DOCX possui XML inválido em {name}: {document}") from exc
    document_root = parsed_xml["word/document.xml"]
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    body = document_root.find(f"{namespace}body")
    visible_text = "".join(
        str(node.text or "") for node in document_root.iter(f"{namespace}t")
    ).strip()
    if body is None or not visible_text:
        raise ValueError(f"DOCX sem conteúdo textual validável: {document}")
    return {
        "path": str(document.resolve()),
        "size_bytes": document.stat().st_size,
        "sha256": sha256_file(document),
        "package_status": "VALID",
    }


def write_json_atomic(path: str | Path, payload: Mapping[str, Any]) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)
    return destination


def create_publication_manifest(
    *,
    output_path: str | Path,
    client_id: str,
    tenant_id: str,
    run_id: str,
    execution_type: str,
    period: Mapping[str, Any],
    dataset_path: str | Path,
    documents: Sequence[str | Path],
    history_database: str | Path | None,
    history_store: Mapping[str, Any] | None = None,
    origin: str | None = None,
    logical_job_id: str | None = None,
    attempt_number: int = 1,
) -> Path:
    dataset = Path(dataset_path)
    if not dataset.is_file():
        raise ValueError(f"Dataset de publicacao nao encontrado: {dataset}")
    validated_documents = [validate_docx_package(path) for path in documents]
    payload = {
        "schema_version": 1,
        "status": "READY_FOR_CONTROLLED_DISTRIBUTION",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "client_id": client_id,
        "tenant_id": tenant_id,
        "run_id": run_id,
        "execution_type": execution_type,
        "origin": origin or ("SCHEDULED" if execution_type == "AUTOMATIC_MONTHLY" else "MANUAL"),
        "logical_job_id": logical_job_id or run_id,
        "attempt_number": int(attempt_number),
        "period": dict(period),
        "source_dataset": {
            "path": str(dataset.resolve()),
            "size_bytes": dataset.stat().st_size,
            "sha256": sha256_file(dataset),
        },
        "history_database": (
            str(Path(history_database).resolve()) if history_database else None
        ),
        "history_store": (
            dict(history_store)
            if history_store is not None
            else {
                "backend": "sqlite" if history_database else None,
                "location": (
                    str(Path(history_database).resolve()) if history_database else None
                ),
            }
        ),
        "documents": validated_documents,
        "distribution": {
            "external_delivery_performed": False,
            "note": "A entrega externa exige uma integracao e autorizacao explicitas.",
        },
    }
    return write_json_atomic(output_path, payload)
