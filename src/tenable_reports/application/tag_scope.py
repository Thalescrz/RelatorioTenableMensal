from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from tenable_reports.config.profile import ClientProfile
from tenable_reports.domain.models import utc_now_iso
from tenable_reports.infrastructure.tenable_vm.client import TenableVmClient


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
class TagScopeArtifact:
    path: Path
    tags: tuple[VmTag, ...]
    asset_ids: frozenset[str]


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
    return validate_single_category(selected)


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


def collect_tag_scope_snapshot(
    *,
    client: TenableVmClient,
    profile: ClientProfile,
    tags: Sequence[VmTag],
    output_root: str | Path,
    run_id: str,
) -> TagScopeArtifact:
    selected = validate_single_category(tags)
    if not selected:
        raise ValueError("A coleta de escopo exige ao menos uma tag selecionada.")
    selected_rows: list[dict[str, Any]] = []
    all_asset_ids: set[str] = set()
    for tag in selected:
        assets = client.list_assets_for_tag(tag.category_name, tag.value)
        asset_ids = sorted({
            _text(item.get("id") or item.get("uuid") or item.get("asset_uuid"))
            for item in assets
            if _text(item.get("id") or item.get("uuid") or item.get("asset_uuid"))
        })
        all_asset_ids.update(asset_ids)
        selected_rows.append({
            **tag.to_dict(),
            "asset_count": len(asset_ids),
            "asset_ids": asset_ids,
        })
    data = {
        "schema_version": 1,
        "source": "tenable_vm_tags",
        "run_id": run_id,
        "client_id": profile.client_id,
        "tenant_id": profile.tenant_id,
        "collected_at": utc_now_iso(),
        "match_operator": "OR_WITHIN_CATEGORY",
        "category_name": selected[0].category_name,
        "selected_asset_count": len(all_asset_ids),
        "selected_tags": selected_rows,
    }
    path = (
        Path(output_root)
        / "snapshots"
        / profile.client_id
        / run_id
        / "tenable_vm_tag_scope.snapshot.json"
    )
    _write_exclusive(
        path,
        (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    return TagScopeArtifact(path=path, tags=selected, asset_ids=frozenset(all_asset_ids))


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
