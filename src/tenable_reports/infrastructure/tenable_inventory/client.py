from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Mapping, Sequence
from urllib.parse import urlencode

from tenable_reports.infrastructure.tenable_vm.client import (
    ApiError,
    TenableVmClient,
    TenableVmConfig,
    Transport,
)


PROPERTIES_PATH = "/api/v1/t1/inventory/findings/properties"
SEARCH_PATH = "/api/v1/t1/inventory/findings/search"
PROPERTY_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")
SORT_PATTERN = re.compile(r"^[A-Za-z0-9_]+:(?:asc|desc)$")


@dataclass(frozen=True, slots=True)
class InventoryPage:
    findings: tuple[dict[str, Any], ...]
    offset: int
    limit: int
    total: int | None


def _integer(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class InventoryFindingsClient:
    """Cliente fino da Inventory Findings API beta.

    Autenticacao, TLS, timeouts e retry permanecem centralizados no cliente VM.
    """

    def __init__(
        self,
        config: TenableVmConfig,
        *,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = TenableVmClient(
            config,
            transport=transport,
            sleep=sleep,
        )

    def list_properties(self) -> list[dict[str, Any]]:
        data = self._http.request("GET", PROPERTIES_PATH).json()
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            for key in ("properties", "data", "items"):
                value = data.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        raise ApiError("Resposta de properties da Inventory API possui formato inesperado.")

    def search_page(
        self,
        *,
        filters: Sequence[Mapping[str, Any]],
        extra_properties: Sequence[str] = (),
        offset: int = 0,
        limit: int = 1000,
        sort: str = "severity:desc",
    ) -> InventoryPage:
        bounded_offset = int(offset)
        bounded_limit = int(limit)
        if bounded_offset < 0:
            raise ValueError("offset nao pode ser negativo.")
        if not 1 <= bounded_limit <= 10000:
            raise ValueError("limit deve estar entre 1 e 10000.")
        if not SORT_PATTERN.fullmatch(sort):
            raise ValueError("sort deve usar o formato propriedade:asc|desc.")

        properties = tuple(dict.fromkeys(
            str(item).strip() for item in extra_properties if str(item).strip()
        ))
        invalid = [item for item in properties if not PROPERTY_PATTERN.fullmatch(item)]
        if invalid:
            raise ValueError("extra_properties contem nome de propriedade invalido.")

        query: dict[str, str | int] = {
            "offset": bounded_offset,
            "limit": bounded_limit,
            "sort": sort,
        }
        if properties:
            query["extra_properties"] = ",".join(properties)
        data = self._http.request(
            "POST",
            f"{SEARCH_PATH}?{urlencode(query)}",
            json_body={"filters": [dict(item) for item in filters]},
        ).json()
        if not isinstance(data, dict):
            raise ApiError("Resposta da busca de findings nao e um objeto JSON.")
        raw_findings = next((
            data.get(key)
            for key in ("findings", "data", "items")
            if isinstance(data.get(key), list)
        ), None)
        if raw_findings is None:
            raise ApiError("Resposta da busca nao contem uma colecao de findings.")
        findings = tuple(item for item in raw_findings if isinstance(item, dict))
        pagination = data.get("pagination")
        page_data = pagination if isinstance(pagination, dict) else {}
        page_offset = _integer(page_data.get("offset"), bounded_offset)
        page_limit = _integer(page_data.get("limit"), bounded_limit)
        total_value = page_data.get("total", data.get("total"))
        total = _integer(total_value, -1) if total_value is not None else None
        if total is not None and total < 0:
            raise ApiError("Inventory API retornou pagination.total invalido.")
        return InventoryPage(
            findings=findings,
            offset=page_offset,
            limit=page_limit,
            total=total,
        )

    def iter_findings(
        self,
        *,
        filters: Sequence[Mapping[str, Any]],
        extra_properties: Sequence[str] = (),
        offset: int = 0,
        limit: int = 1000,
        sort: str = "severity:desc",
    ) -> Iterator[dict[str, Any]]:
        current_offset = int(offset)
        while True:
            page = self.search_page(
                filters=filters,
                extra_properties=extra_properties,
                offset=current_offset,
                limit=limit,
                sort=sort,
            )
            yield from page.findings
            next_offset = current_offset + len(page.findings)
            if not page.findings:
                return
            if page.total is not None and next_offset >= page.total:
                return
            if len(page.findings) < int(limit):
                return
            if next_offset <= current_offset:
                raise ApiError("Paginacao da Inventory API nao avancou.")
            current_offset = next_offset
