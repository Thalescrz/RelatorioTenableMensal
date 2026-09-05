from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from zipfile import BadZipFile, ZipFile
from xml.etree import ElementTree


REQUIRED_DOCX_MEMBERS = {
    "[Content_Types].xml",
    "word/document.xml",
    "word/styles.xml",
}


@dataclass(frozen=True, slots=True)
class PublicationDocument:
    path: str | Path
    document_kind: str
    document_variant: str | None = None
    tag_uuid: str | None = None
    tag_category: str | None = None
    tag_value: str | None = None

    def metadata(self) -> dict[str, str | None]:
        document_kind = str(self.document_kind or "").strip().lower()
        if document_kind not in {"base", "custom", "tag", "cloud"}:
            raise ValueError(f"Tipo de documento de publicacao invalido: {self.document_kind}")
        document_variant = str(self.document_variant or "").strip().lower() or None
        if document_kind == "cloud" and document_variant not in {"base", "expanded"}:
            raise ValueError("Documento Cloud requer variante base ou expanded.")
        if document_kind != "cloud" and document_variant is not None:
            raise ValueError("A variante de documento é exclusiva do relatório Cloud.")
        tag_uuid = str(self.tag_uuid or "").strip() or None
        if document_kind == "tag" and tag_uuid is None:
            raise ValueError("Documento por TAG sem tag_uuid.")
        return {
            "document_kind": document_kind,
            "document_variant": document_variant,
            "tag_uuid": tag_uuid,
            "tag_category": str(self.tag_category or "").strip() or None,
            "tag_value": str(self.tag_value or "").strip() or None,
        }


@dataclass(frozen=True, slots=True)
class PublicationDocumentReplacement:
    staged_path: str | Path
    destination: PublicationDocument


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
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(6):
            try:
                os.replace(temporary, destination)
                break
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(min(0.8, 0.05 * (2 ** attempt)))
    finally:
        temporary.unlink(missing_ok=True)
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
    primary_dataset_component: str = "vm",
    documents: Sequence[str | Path | PublicationDocument],
    additional_datasets: Mapping[str, str | Path] | None = None,
    history_database: str | Path | None,
    history_store: Mapping[str, Any] | None = None,
    origin: str | None = None,
    logical_job_id: str | None = None,
    attempt_number: int = 1,
) -> Path:
    primary_component = str(primary_dataset_component or "").strip().lower()
    if primary_component not in {"vm", "cloud"}:
        raise ValueError("Componente do dataset principal inválido.")
    dataset = Path(dataset_path)
    if not dataset.is_file():
        raise ValueError(f"Dataset de publicacao nao encontrado: {dataset}")
    source_dataset = {
        "path": str(dataset.resolve()),
        "size_bytes": dataset.stat().st_size,
        "sha256": sha256_file(dataset),
    }
    source_datasets: dict[str, dict[str, Any]] = {
        primary_component: source_dataset
    }
    for name, value in sorted((additional_datasets or {}).items()):
        dataset_name = str(name or "").strip().lower()
        if not dataset_name or dataset_name in source_datasets:
            raise ValueError("Nome de dataset adicional inválido ou reservado.")
        additional = Path(value)
        if not additional.is_file():
            raise ValueError(
                f"Dataset adicional de publicacao nao encontrado: {additional}"
            )
        source_datasets[dataset_name] = {
            "path": str(additional.resolve()),
            "size_bytes": additional.stat().st_size,
            "sha256": sha256_file(additional),
        }
    validated_documents = []
    for document in documents:
        if isinstance(document, PublicationDocument):
            validated = validate_docx_package(document.path)
            validated.update(document.metadata())
        else:
            validated = validate_docx_package(document)
            validated.update({
                "document_kind": None,
                "document_variant": None,
                "tag_uuid": None,
                "tag_category": None,
                "tag_value": None,
            })
        validated_documents.append(validated)
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
        "source_dataset": source_dataset,
        "source_datasets": source_datasets,
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

def upsert_publication_documents(
    *,
    manifest_path: str | Path,
    documents: Sequence[PublicationDocument],
    additional_datasets: Mapping[str, str | Path],
) -> Path:
    """Replace only Cloud publication entries while preserving the VM set."""

    source = Path(manifest_path).resolve()
    if not source.is_file():
        raise ValueError(f"Manifesto de publicacao nao encontrado: {source}")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Manifesto de publicacao invalido.") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("Manifesto de publicacao invalido.")
    updated = dict(payload)
    existing_documents = updated.get("documents")
    if not isinstance(existing_documents, list):
        raise ValueError("Manifesto sem documentos validos.")

    validated_cloud: list[dict[str, Any]] = []
    seen_variants: set[str] = set()
    for document in documents:
        metadata = document.metadata()
        if metadata["document_kind"] != "cloud":
            raise ValueError("A retentativa aceita somente documentos Cloud.")
        variant = str(metadata["document_variant"])
        if variant in seen_variants:
            raise ValueError(f"Variante Cloud duplicada: {variant}.")
        seen_variants.add(variant)
        validated = validate_docx_package(document.path)
        validated.update(metadata)
        validated_cloud.append(validated)

    preserved = [
        dict(item)
        for item in existing_documents
        if isinstance(item, Mapping)
        and str(item.get("document_kind") or "").lower() != "cloud"
    ]
    updated["documents"] = [*preserved, *validated_cloud]
    source_dataset = updated.get("source_dataset")
    source_datasets = updated.get("source_datasets")
    merged_datasets = (
        dict(source_datasets) if isinstance(source_datasets, Mapping) else {}
    )
    if "vm" not in merged_datasets and isinstance(source_dataset, Mapping):
        merged_datasets["vm"] = dict(source_dataset)
    for raw_name, raw_path in sorted(additional_datasets.items()):
        name = str(raw_name or "").strip().lower()
        if not name or name == "vm":
            raise ValueError("Nome de dataset adicional inválido ou reservado.")
        dataset = Path(raw_path)
        if not dataset.is_file():
            raise ValueError(f"Dataset adicional nao encontrado: {dataset}")
        merged_datasets[name] = {
            "path": str(dataset.resolve()),
            "size_bytes": dataset.stat().st_size,
            "sha256": sha256_file(dataset),
        }
    updated["source_datasets"] = merged_datasets
    updated["updated_at"] = datetime.now(timezone.utc).isoformat()
    return write_json_atomic(source, updated)


def replace_publication_documents_atomically(
    *,
    manifest_path: str | Path,
    dataset_path: str | Path,
    replacements: Sequence[PublicationDocumentReplacement],
    commit_callback: Callable[[], None] | None = None,
) -> Path:
    """Replace validated VM documents and commit their manifest last."""

    source = Path(manifest_path).resolve()
    if not source.is_file():
        raise ValueError(f"Manifesto de publicacao nao encontrado: {source}")
    try:
        original = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Manifesto de publicacao invalido.") from exc
    if not isinstance(original, Mapping):
        raise ValueError("Manifesto de publicacao invalido.")
    existing_documents = original.get("documents")
    if not isinstance(existing_documents, list):
        raise ValueError("Manifesto sem documentos validos.")
    dataset = Path(dataset_path).resolve()
    if not dataset.is_file():
        raise ValueError(f"Dataset de publicacao nao encontrado: {dataset}")
    if not replacements:
        raise ValueError("Nenhum documento foi informado para substituicao.")

    prepared: list[tuple[Path, Path, dict[str, Any]]] = []
    replacement_keys: set[tuple[str, str | None]] = set()
    destinations: set[Path] = set()
    for replacement in replacements:
        staged = Path(replacement.staged_path).resolve()
        destination = Path(replacement.destination.path).resolve()
        metadata = replacement.destination.metadata()
        kind = str(metadata["document_kind"])
        if kind == "cloud":
            raise ValueError("Documentos Cloud nao pertencem a reparacao VM/WAS.")
        if staged == destination:
            raise ValueError("Documento staged precisa ser diferente do destino.")
        if staged.anchor.lower() != destination.anchor.lower():
            raise ValueError("Staging e destino precisam estar no mesmo volume.")
        if destination in destinations:
            raise ValueError(f"Destino de documento duplicado: {destination}")
        destinations.add(destination)
        identity = metadata["tag_uuid"] if kind == "tag" else None
        key = (kind, identity)
        if key in replacement_keys:
            raise ValueError(f"Documento de substituicao duplicado: {key}")
        replacement_keys.add(key)
        validated = validate_docx_package(staged)
        validated["path"] = str(destination)
        validated.update(metadata)
        prepared.append((staged, destination, validated))

    preserved = []
    for item in existing_documents:
        if not isinstance(item, Mapping):
            continue
        kind = str(item.get("document_kind") or "").lower()
        identity = str(item.get("tag_uuid") or "") or None if kind == "tag" else None
        if (kind, identity) not in replacement_keys:
            preserved.append(dict(item))

    source_dataset = {
        "path": str(dataset),
        "size_bytes": dataset.stat().st_size,
        "sha256": sha256_file(dataset),
    }
    updated = dict(original)
    updated["source_dataset"] = source_dataset
    source_datasets = updated.get("source_datasets")
    merged_datasets = (
        dict(source_datasets) if isinstance(source_datasets, Mapping) else {}
    )
    merged_datasets["vm"] = source_dataset
    updated["source_datasets"] = merged_datasets
    updated["documents"] = [
        *preserved,
        *(validated for _, _, validated in prepared),
    ]
    updated["updated_at"] = datetime.now(timezone.utc).isoformat()

    transaction_id = uuid.uuid4().hex
    backups: list[tuple[Path, Path | None]] = []
    committed_destinations: list[Path] = []
    try:
        for _, destination, _ in prepared:
            destination.parent.mkdir(parents=True, exist_ok=True)
            backup = None
            if destination.exists():
                backup = destination.with_name(
                    f".{destination.name}.was-backup-{transaction_id}"
                )
                destination.replace(backup)
            backups.append((destination, backup))
        for staged, destination, _ in prepared:
            staged.replace(destination)
            committed_destinations.append(destination)
        write_json_atomic(source, updated)
        if commit_callback is not None:
            commit_callback()
    except Exception:
        for destination, backup in reversed(backups):
            if backup is not None and backup.exists():
                backup.replace(destination)
            elif destination in committed_destinations:
                destination.unlink(missing_ok=True)
        write_json_atomic(source, original)
        raise
    for _, backup in backups:
        if backup is not None:
            backup.unlink(missing_ok=True)
    return source
