from __future__ import annotations

import logging
from typing import Any, Mapping

from tenable_reports.infrastructure.tenable_vm.client import (
    ApiError,
    TRANSIENT_STATUS_CODES,
    TenableVmClient,
)
from tenable_reports.infrastructure.tenable_vm.parser import parse_chunk_response


LOGGER = logging.getLogger(__name__)


class TenableWasClient(TenableVmClient):
    """Adaptador do contrato de export dedicado do Tenable WAS."""

    def start_findings_export(
        self,
        *,
        filters: Mapping[str, Any],
        num_assets: int = 1000,
        include_unlicensed: bool = False,
    ) -> str:
        payload = {
            "num_assets": max(50, min(int(num_assets), 5000)),
            "include_unlicensed": bool(include_unlicensed),
            "filters": dict(filters),
        }
        try:
            response = self.request(
                "POST",
                "/was/v1/export/vulns",
                json_body=payload,
                retry_status_codes=TRANSIENT_STATUS_CODES,
            )
        except ApiError as exc:
            if exc.status_code == 409 and exc.active_job_id:
                LOGGER.info("Export WAS equivalente em andamento; reutilizando o job.")
                return exc.active_job_id
            raise
        data = response.json()
        export_uuid = data.get("export_uuid") or data.get("uuid") if isinstance(data, dict) else None
        if not isinstance(export_uuid, str) or not export_uuid.strip():
            raise ApiError("Resposta de inicio do export WAS nao contem export_uuid.")
        return export_uuid.strip()

    def get_findings_export_status(self, export_uuid: str) -> dict[str, Any]:
        data = self.request("GET", f"/was/v1/export/vulns/{export_uuid}/status").json()
        if not isinstance(data, dict):
            raise ApiError("Resposta de status do export WAS nao e um objeto JSON.")
        return data

    def wait_for_findings_completion(
        self, export_uuid: str
    ) -> tuple[dict[str, Any], list[int]]:
        return self._wait_for_completion(
            export_uuid,
            self.get_findings_export_status,
            label="WAS",
        )

    def download_findings_chunk_bytes(self, export_uuid: str, chunk_id: int) -> bytes:
        return self.request(
            "GET",
            f"/was/v1/export/vulns/{export_uuid}/chunks/{int(chunk_id)}",
            accept="application/octet-stream",
        ).content

    def download_findings_chunk(
        self, export_uuid: str, chunk_id: int
    ) -> list[dict[str, Any]]:
        return parse_chunk_response(
            self.download_findings_chunk_bytes(export_uuid, chunk_id)
        )

    def list_vulnerability_filters(self) -> list[dict[str, Any]]:
        data = self.request("GET", "/was/v2/vulnerabilities/filters").json()
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            for key in ("filters", "items", "data"):
                value = data.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        raise ApiError("Resposta dos filtros WAS possui formato inesperado.")
