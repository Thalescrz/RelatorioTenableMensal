from __future__ import annotations

import inspect
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from tenable_reports.config.profile import ClientProfile
from tenable_reports.domain.models import utc_now_iso
from tenable_reports.domain.execution_control import ExecutionInterruptedError
from tenable_reports.infrastructure.tenable_vm.client import (
    TagScopeLimitExceeded,
    TenableVmClient,
)


@dataclass(frozen=True, slots=True)
class VmTag:
    uuid: str
    category_uuid: str
    category_name: str
    value: str

    @property
    def label(self) -> str:
        return f"{self.category_name}: {self.value}"

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TagAssetScope:
    tag: VmTag
    asset_ids: frozenset[str]


@dataclass(frozen=True, slots=True)
class TagScopeCollection:
    path: Path
    scopes: tuple[TagAssetScope, ...]
    warnings: tuple[dict[str, Any], ...] = ()

    @property
    def tags(self) -> tuple[VmTag, ...]:
        return tuple(scope.tag for scope in self.scopes)

    @property
    def asset_ids(self) -> frozenset[str]:
        return frozenset(
            asset_id for scope in self.scopes for asset_id in scope.asset_ids
        )


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def parse_tag_values(records: Iterable[Mapping[str, Any]]) -> tuple[VmTag, ...]:
    tags: list[VmTag] = []
    seen: set[str] = set()
    for record in records:
        uuid = _text(record.get("uuid") or record.get("value_uuid") or record.get("id"))
        category_uuid = _text(record.get("category_uuid") or record.get("category_id"))
        category_name = _text(record.get("category_name") or record.get("category"))
        value = _text(record.get("value") or record.get("name"))
        # A API tambem pode devolver categorias ainda sem valores associados.
        if not uuid or not category_name or not value or uuid in seen:
            continue
        seen.add(uuid)
        tags.append(VmTag(
            uuid=uuid,
            category_uuid=category_uuid,
            category_name=category_name,
            value=value,
        ))
    return tuple(sorted(tags, key=lambda item: (
        item.category_name.casefold(), item.value.casefold(), item.uuid
    )))


def resolve_tag_selectors(
    available_tags: Sequence[VmTag], selectors: Iterable[str]
) -> tuple[VmTag, ...]:
    by_uuid = {item.uuid.casefold(): item for item in available_tags}
    by_label = {item.label.casefold(): item for item in available_tags}
    selected: list[VmTag] = []
    seen: set[str] = set()
    for raw_selector in selectors:
        selector = raw_selector.strip()
        tag = by_uuid.get(selector.casefold()) or by_label.get(selector.casefold())
        if tag is None:
            raise ValueError(
                f"Tag nao encontrada: {selector}. Use o UUID ou o formato Categoria: Valor."
            )
        if tag.uuid not in seen:
            selected.append(tag)
            seen.add(tag.uuid)
    return tuple(selected)


def validate_single_category(tags: Iterable[VmTag]) -> tuple[VmTag, ...]:
    selected = tuple(tags)
    categories = {item.category_name.casefold() for item in selected}
    if len(categories) > 1:
        raise ValueError(
            "Selecione valores de uma unica categoria de tag por execucao. "
            "A API Tenable combina categorias diferentes com AND."
        )
    return selected


def parse_number_selection(text: str, maximum: int) -> tuple[int, ...]:
    value = text.strip().casefold()
    if value in {"todos", "todas", "all", "*"}:
        return tuple(range(1, maximum + 1))
    if not value:
        raise ValueError("Informe ao menos uma opcao.")
    selected: set[int] = set()
    for part in value.split(","):
        token = part.strip()
        if not token:
            raise ValueError("Selecao possui um item vazio.")
        if "-" in token:
            start_text, end_text = token.split("-", 1)
            try:
                start, end = int(start_text), int(end_text)
            except ValueError as exc:
                raise ValueError("Use numeros, virgulas e intervalos como 1,3-5.") from exc
            if start > end:
                raise ValueError("O inicio do intervalo nao pode ser maior que o fim.")
            selected.update(range(start, end + 1))
        else:
            try:
                selected.add(int(token))
            except ValueError as exc:
                raise ValueError("Use numeros, virgulas e intervalos como 1,3-5.") from exc
    if not selected or min(selected) < 1 or max(selected) > maximum:
        raise ValueError(f"Escolha valores entre 1 e {maximum}.")
    return tuple(sorted(selected))


def _ask_selection(
    prompt: str,
    maximum: int,
    *,
    input_fn: Callable[[str], str],
    output_fn: Callable[[str], None],
) -> tuple[int, ...]:
    while True:
        try:
            return parse_number_selection(input_fn(prompt), maximum)
        except ValueError as exc:
            output_fn(f"Selecao invalida: {exc}")


def prompt_tag_selection(
    available_tags: Sequence[VmTag],
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> tuple[VmTag, ...]:
    if not available_tags:
        raise ValueError("O tenant nao retornou valores de tag selecionaveis.")
    grouped: dict[str, list[VmTag]] = {}
    for tag in available_tags:
        grouped.setdefault(tag.category_name, []).append(tag)
    categories = sorted(grouped, key=str.casefold)
    output_fn("Categorias de tags disponiveis:")
    for index, category in enumerate(categories, start=1):
        output_fn(f"  {index}. {category} ({len(grouped[category])} valores)")
    category_indexes = _ask_selection(
        "Selecione uma categoria: ",
        len(categories),
        input_fn=input_fn,
        output_fn=output_fn,
    )
    if len(category_indexes) != 1:
        raise ValueError("Selecione exatamente uma categoria por execucao.")
    category = categories[category_indexes[0] - 1]
    values = grouped[category]
    output_fn(f"Valores da categoria {category}:")
    for index, tag in enumerate(values, start=1):
        output_fn(f"  {index}. {tag.value}")
    value_indexes = _ask_selection(
        "Selecione valores (ex.: 1,3-5 ou todos): ",
        len(values),
        input_fn=input_fn,
        output_fn=output_fn,
    )
    return tuple(values[index - 1] for index in value_indexes)


def _write_exclusive(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _write_replace(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _collect_large_tag_scope(
    *,
    client: TenableVmClient,
    tag: VmTag,
    progress_callback: Callable[[Mapping[str, Any]], None] | None,
    cancellation_probe: Callable[[], bool] | None,
) -> tuple[list[str], dict[str, Any]]:
    job = client.start_asset_export_v1_job(
        filters={f"tag.{tag.category_name}": [tag.value]},
        chunk_size=5000,
    )

    def emit(status: str, **details: Any) -> None:
        if progress_callback is not None:
            progress_callback({
                "event": "TENABLE_EXPORT_PROGRESS",
                "source": "tenable_vm_asset_export_v1_tag_scope",
                "export_uuid": job.export_uuid,
                "origin": job.origin,
                "status": status,
                "tag_uuid": tag.uuid,
                "tag_label": tag.label,
                **details,
            })

    emit("STARTED", completed_chunks=0, total_chunks=0, progress_made=False)
    wait_arguments: dict[str, Any] = {}
    parameters = inspect.signature(client.wait_for_asset_completion).parameters
    if "progress_callback" in parameters:
        wait_arguments["progress_callback"] = lambda status: emit(
            str(status.get("status") or "PROCESSING").upper(),
            **{
                str(key): value
                for key, value in status.items()
                if key != "status"
            },
        )
    if "cancellation_probe" in parameters:
        wait_arguments["cancellation_probe"] = cancellation_probe
    _, chunk_ids = client.wait_for_asset_completion(job.export_uuid, **wait_arguments)
    asset_ids: set[str] = set()
    for chunk_id in chunk_ids:
        if cancellation_probe is not None and cancellation_probe():
            raise ExecutionInterruptedError(
                "Execucao interrompida com export de ativos por TAG preservado.",
                export_uuid=job.export_uuid,
            )
        for item in client.download_asset_chunk(job.export_uuid, chunk_id):
            asset_id = _text(item.get("id") or item.get("uuid") or item.get("asset_uuid"))
            if asset_id:
                asset_ids.add(asset_id)
    emit(
        "FINISHED",
        completed_chunks=len(chunk_ids),
        total_chunks=len(chunk_ids),
        progress_made=bool(chunk_ids),
        asset_count=len(asset_ids),
    )
    return sorted(asset_ids), {
        "scope_source": "tenable_vm_asset_export_v1",
        "export_uuid": job.export_uuid,
        "export_origin": job.origin,
        "chunk_ids": [int(chunk_id) for chunk_id in chunk_ids],
    }


def collect_tag_scope_snapshot(
    *,
    client: TenableVmClient,
    profile: ClientProfile,
    tags: Sequence[VmTag],
    output_root: str | Path,
    run_id: str,
    retry_unavailable: bool = False,
    progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
    cancellation_probe: Callable[[], bool] | None = None,
) -> TagScopeCollection:
    selected = tuple(tags)
    if not selected:
        raise ValueError("A coleta de escopo exige ao menos uma tag selecionada.")
    path = (
        Path(output_root)
        / "snapshots"
        / profile.client_id
        / run_id
        / "tenable_vm_tag_scope.snapshot.json"
    )
    existing_payload: dict[str, Any] | None = None
    if path.is_file():
        existing = read_tag_scope_snapshot(path)
        existing_payload = existing
        if (
            str(existing.get("run_id") or "") != run_id
            or str(existing.get("client_id") or "") != profile.client_id
            or str(existing.get("tenant_id") or "") != profile.tenant_id
        ):
            raise FileExistsError(
                "Snapshot de tags existente pertence a outra identidade."
            )
        rows = tuple(
            row
            for row in existing.get("selected_tags") or ()
            if isinstance(row, Mapping)
        )
        warnings = tuple(
            dict(warning)
            for warning in existing.get("warnings") or ()
            if isinstance(warning, Mapping)
        )
        observed_tag_ids = {
            _text(row.get("uuid")) for row in rows if _text(row.get("uuid"))
        } | {
            _text(warning.get("tag_uuid"))
            for warning in warnings
            if _text(warning.get("tag_uuid"))
        }
        if observed_tag_ids != {tag.uuid for tag in selected}:
            raise FileExistsError(
                "Snapshot de tags existente usa uma seleção diferente."
            )
        selected_by_uuid = {tag.uuid: tag for tag in selected}
        scopes: list[TagAssetScope] = []
        for row in rows:
            tag = selected_by_uuid[_text(row.get("uuid"))]
            if (
                _text(row.get("category_uuid")) != tag.category_uuid
                or _text(row.get("category_name")) != tag.category_name
                or _text(row.get("value")) != tag.value
            ):
                raise FileExistsError(
                    "Snapshot de tags existente diverge da configuração atual."
                )
            scopes.append(
                TagAssetScope(
                    tag=tag,
                    asset_ids=frozenset(
                        _text(asset_id)
                        for asset_id in row.get("asset_ids") or ()
                        if _text(asset_id)
                    ),
                )
            )
        if not retry_unavailable or len(scopes) == len(selected):
            return TagScopeCollection(
                path=path,
                scopes=tuple(scopes),
                warnings=warnings,
            )
    selected_rows = [
        dict(row)
        for row in ((existing_payload or {}).get("selected_tags") or ())
        if isinstance(row, Mapping)
    ]
    selected_row_ids = {_text(row.get("uuid")) for row in selected_rows}
    retry_tag_ids = {tag.uuid for tag in selected if tag.uuid not in selected_row_ids}
    warnings: list[dict[str, Any]] = [
        dict(warning)
        for warning in ((existing_payload or {}).get("warnings") or ())
        if isinstance(warning, Mapping)
        and _text(warning.get("tag_uuid")) not in retry_tag_ids
    ]
    for tag in selected:
        if tag.uuid in selected_row_ids:
            continue
        scope_details: dict[str, Any] = {"scope_source": "tenable_vm_workbench"}
        try:
            assets = client.list_assets_for_tag(tag.category_name, tag.value)
            asset_ids = sorted({
                _text(item.get("id") or item.get("uuid") or item.get("asset_uuid"))
                for item in assets
                if _text(item.get("id") or item.get("uuid") or item.get("asset_uuid"))
            })
        except TagScopeLimitExceeded:
            try:
                asset_ids, scope_details = _collect_large_tag_scope(
                    client=client,
                    tag=tag,
                    progress_callback=progress_callback,
                    cancellation_probe=cancellation_probe,
                )
            except ExecutionInterruptedError:
                raise
            except Exception as exc:
                warnings.append({
                    "code": "TAG_SCOPE_UNAVAILABLE",
                    "tag_uuid": tag.uuid,
                    "tag_label": tag.label,
                    "stage": "tag_asset_export_v1",
                    "message": str(exc)[:500],
                })
                continue
        except ExecutionInterruptedError:
            raise
        except Exception as exc:
            warnings.append({
                "code": "TAG_SCOPE_UNAVAILABLE",
                "tag_uuid": tag.uuid,
                "tag_label": tag.label,
                "stage": "tag_asset_scope",
                "message": str(exc)[:500],
            })
            continue
        selected_rows.append({
            **tag.to_dict(),
            "asset_count": len(asset_ids),
            "asset_ids": asset_ids,
            **scope_details,
        })
        selected_row_ids.add(tag.uuid)
    rows_by_uuid = {_text(row.get("uuid")): row for row in selected_rows}
    selected_rows = [rows_by_uuid[tag.uuid] for tag in selected if tag.uuid in rows_by_uuid]
    scopes = [
        TagAssetScope(
            tag=tag,
            asset_ids=frozenset(
                _text(asset_id)
                for asset_id in rows_by_uuid[tag.uuid].get("asset_ids") or ()
                if _text(asset_id)
            ),
        )
        for tag in selected
        if tag.uuid in rows_by_uuid
    ]
    data = {
        "schema_version": 2,
        "source": "tenable_vm_tags",
        "run_id": run_id,
        "client_id": profile.client_id,
        "tenant_id": profile.tenant_id,
        "collected_at": utc_now_iso(),
        "match_operator": "INDEPENDENT_TAG_SCOPES",
        "selected_asset_count": len({
            asset_id for scope in scopes for asset_id in scope.asset_ids
        }),
        "selected_tags": selected_rows,
        "warnings": warnings,
    }
    content = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if existing_payload is None:
        _write_exclusive(path, content)
    else:
        _write_replace(path, content)
    return TagScopeCollection(
        path=path,
        scopes=tuple(scopes),
        warnings=tuple(warnings),
    )


def read_tag_scope_snapshot(path: str | Path) -> dict[str, Any]:
    snapshot_path = Path(path)
    try:
        data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Nao foi possivel ler o escopo de tags: {snapshot_path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Snapshot de tags invalido na linha {exc.lineno}.") from exc
    if not isinstance(data, dict) or not isinstance(data.get("selected_tags"), list):
        raise ValueError("Snapshot de tags possui formato invalido.")
    return data
