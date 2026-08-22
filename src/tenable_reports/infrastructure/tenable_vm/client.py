from __future__ import annotations

import json
import logging
import random
import ssl
import time
from dataclasses import dataclass
from http.client import HTTPResponse
from typing import Any, Callable, Iterator, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import HTTPSHandler, Request, build_opener

from .parser import parse_chunk_response


LOGGER = logging.getLogger(__name__)
TRANSIENT_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
SUCCESS_STATES = frozenset({"finished", "completed", "complete", "ready"})
FAILURE_STATES = frozenset({"error", "failed", "cancelled", "canceled"})


class ApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        endpoint: str | None = None,
        active_job_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.endpoint = endpoint
        self.active_job_id = active_job_id


class CredentialError(ApiError):
    pass


class ExportFailedError(ApiError):
    pass


class ExportTimeoutError(TimeoutError):
    def __init__(
        self,
        message: str,
        *,
        export_uuid: str | None = None,
        last_status: Mapping[str, Any] | None = None,
        progress_made: bool = False,
        origin: str | None = None,
        auto_cancelled: bool = False,
        cancellation_error: str | None = None,
    ) -> None:
        super().__init__(message)
        self.export_uuid = export_uuid
        self.last_status = dict(last_status or {})
        self.progress_made = bool(progress_made)
        self.origin = origin
        self.auto_cancelled = bool(auto_cancelled)
        self.cancellation_error = cancellation_error


@dataclass(frozen=True, slots=True)
class ExportJob:
    export_uuid: str
    origin: str

    @property
    def created_by_current_run(self) -> bool:
        return self.origin == "created"


@dataclass(frozen=True, slots=True)
class TenableVmConfig:
    access_key: str
    secret_key: str
    base_url: str = "https://cloud.tenable.com"
    user_agent: str = "Integration/1.0 (SuaEmpresa; RelatorioTenableMensal; Build/0.1.0)"
    timeout_seconds: float = 30.0
    poll_seconds: float = 5.0
    max_wait_seconds: float = 1800.0
    max_attempts: int = 5
    ca_bundle: str | None = None
    validate_tls: bool = True

    def __post_init__(self) -> None:
        if not self.access_key.strip() or not self.secret_key.strip():
            raise CredentialError("Credenciais Tenable VM nao configuradas.")
        parsed = urlsplit(self.base_url)
        if parsed.scheme.lower() != "https" or not parsed.netloc:
            raise ValueError("base_url deve ser uma URL HTTPS completa.")
        if self.timeout_seconds <= 0 or self.poll_seconds < 0 or self.max_wait_seconds <= 0:
            raise ValueError("Timeouts devem ser positivos e poll_seconds nao pode ser negativo.")
        if self.max_attempts < 1:
            raise ValueError("max_attempts deve ser maior que zero.")


@dataclass(frozen=True, slots=True)
class TransportResponse:
    status_code: int
    headers: Mapping[str, str]
    content: bytes

    def json(self) -> Any:
        try:
            return json.loads(self.content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError("A API retornou JSON invalido.") from exc


class Transport(Protocol):
    def send(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> TransportResponse: ...


class UrllibTransport:
    def __init__(self, *, ca_bundle: str | None = None, validate_tls: bool = True) -> None:
        if validate_tls:
            context = ssl.create_default_context(cafile=ca_bundle)
        else:
            context = ssl._create_unverified_context()  # noqa: SLF001 - opt-in explicito
        self._opener = build_opener(HTTPSHandler(context=context))

    def send(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> TransportResponse:
        request = Request(url, data=body, headers=dict(headers), method=method.upper())
        try:
            response: HTTPResponse = self._opener.open(request, timeout=timeout)
            return TransportResponse(
                status_code=response.status,
                headers={key.lower(): value for key, value in response.headers.items()},
                content=response.read(),
            )
        except HTTPError as exc:
            return TransportResponse(
                status_code=exc.code,
                headers={key.lower(): value for key, value in exc.headers.items()},
                content=exc.read(),
            )
        except (URLError, TimeoutError, OSError) as exc:
            raise ApiError("Falha de transporte ao acessar a Tenable.") from exc


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _safe_error_message(status_code: int, endpoint: str) -> str:
    messages = {
        400: "A Tenable rejeitou os parametros da requisicao.",
        401: "A Tenable rejeitou as credenciais configuradas.",
        403: "A credencial nao possui permissao para este endpoint.",
        404: "O recurso solicitado nao existe ou expirou.",
        409: "Ja existe um export equivalente em andamento.",
        429: "A Tenable limitou temporariamente as requisicoes.",
    }
    return f"{messages.get(status_code, 'A Tenable retornou um erro HTTP.')} endpoint={endpoint} status={status_code}"


def _extract_active_job_id(content: bytes) -> str | None:
    try:
        data = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    for key in ("active_job_id", "activeJobId", "export_uuid", "uuid"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    error = data.get("error")
    if isinstance(error, dict):
        for key in ("active_job_id", "activeJobId"):
            value = error.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _retry_delay(response: TransportResponse, attempt: int) -> float:
    raw = response.headers.get("retry-after") or response.headers.get("Retry-After")
    if raw:
        try:
            return max(0.0, min(float(raw.strip()), 300.0))
        except ValueError:
            pass
    base = min(60.0, float(2 ** max(0, attempt - 1)))
    return base + random.uniform(0, min(1.0, base / 4.0))


class TenableVmClient:
    def __init__(
        self,
        config: TenableVmConfig,
        *,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self.transport = transport or UrllibTransport(
            ca_bundle=config.ca_bundle,
            validate_tls=config.validate_tls,
        )
        self.sleep = sleep
        self.monotonic = monotonic
        self._headers = {
            "X-ApiKeys": f"accessKey={config.access_key};secretKey={config.secret_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": config.user_agent,
        }

    def _url(self, path: str) -> str:
        return urljoin(self.config.base_url.rstrip("/") + "/", path.lstrip("/"))

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, Any] | None = None,
        accept: str = "application/json",
        retry_status_codes: frozenset[int] = TRANSIENT_STATUS_CODES,
    ) -> TransportResponse:
        endpoint = path.split("?", 1)[0]
        headers = dict(self._headers)
        headers["Accept"] = accept
        body = _json_bytes(json_body) if json_body is not None else None
        last_response: TransportResponse | None = None
        for attempt in range(1, self.config.max_attempts + 1):
            try:
                response = self.transport.send(
                    method,
                    self._url(path),
                    headers=headers,
                    body=body,
                    timeout=self.config.timeout_seconds,
                )
            except ApiError:
                if attempt >= self.config.max_attempts:
                    raise
                delay = min(60.0, float(2 ** max(0, attempt - 1)))
                LOGGER.warning(
                    "Falha transitoria de transporte; nova tentativa.",
                    extra={"endpoint": endpoint, "attempt": attempt, "wait_seconds": delay},
                )
                self.sleep(delay)
                continue
            last_response = response
            if response.status_code in retry_status_codes:
                if attempt >= self.config.max_attempts:
                    break
                delay = _retry_delay(response, attempt)
                LOGGER.warning(
                    "Resposta transitoria da Tenable; nova tentativa.",
                    extra={
                        "endpoint": endpoint,
                        "status_code": response.status_code,
                        "attempt": attempt,
                        "wait_seconds": delay,
                    },
                )
                self.sleep(delay)
                continue
            if response.status_code >= 400:
                error_type = CredentialError if response.status_code == 401 else ApiError
                raise error_type(
                    _safe_error_message(response.status_code, endpoint),
                    status_code=response.status_code,
                    endpoint=endpoint,
                    active_job_id=_extract_active_job_id(response.content),
                )
            return response
        assert last_response is not None
        raise ApiError(
            _safe_error_message(last_response.status_code, endpoint),
            status_code=last_response.status_code,
            endpoint=endpoint,
        )

    def start_vulnerability_export_job(
        self,
        *,
        filters: Mapping[str, Any],
        num_assets: int = 1000,
        include_unlicensed: bool = False,
        include_software_vulns: bool = False,
        include_plugin_output: bool = False,
        properties: list[str] | None = None,
    ) -> ExportJob:
        payload: dict[str, Any] = {
            "num_assets": max(50, min(int(num_assets), 5000)),
            "include_unlicensed": bool(include_unlicensed),
            "include_software_vulns": bool(include_software_vulns),
            "include_plugin_output": bool(include_plugin_output),
            "filters": dict(filters),
        }
        if properties:
            payload["properties"] = list(dict.fromkeys(properties))
        try:
            response = self.request(
                "POST",
                "/vulns/export",
                json_body=payload,
                # 409 e conflito de concorrencia, nao falha transitoria opaca:
                # precisamos ler active_job_id e reutiliza-lo com seguranca.
                retry_status_codes=frozenset({429, 500, 502, 503, 504}),
            )
        except ApiError as exc:
            if exc.status_code == 409 and exc.active_job_id:
                LOGGER.info("Export equivalente ja estava em andamento; reutilizando o job.")
                return ExportJob(exc.active_job_id, "reused")
            raise
        data = response.json()
        export_uuid = data.get("export_uuid") or data.get("uuid") if isinstance(data, dict) else None
        if not isinstance(export_uuid, str) or not export_uuid.strip():
            raise ApiError("Resposta de inicio do export nao contem export_uuid.")
        return ExportJob(export_uuid.strip(), "created")

    def start_vulnerability_export(
        self,
        *,
        filters: Mapping[str, Any],
        num_assets: int = 1000,
        include_unlicensed: bool = False,
        include_software_vulns: bool = False,
        include_plugin_output: bool = False,
        properties: list[str] | None = None,
    ) -> str:
        return self.start_vulnerability_export_job(
            filters=filters,
            num_assets=num_assets,
            include_unlicensed=include_unlicensed,
            include_software_vulns=include_software_vulns,
            include_plugin_output=include_plugin_output,
            properties=properties,
        ).export_uuid

    def cancel_vulnerability_export(self, export_uuid: str) -> dict[str, Any]:
        data = self.request(
            "POST", f"/vulns/export/{export_uuid}/cancel"
        ).json()
        return dict(data) if isinstance(data, dict) else {"status": "CANCELLED"}

    def get_export_status(self, export_uuid: str) -> dict[str, Any]:
        data = self.request("GET", f"/vulns/export/{export_uuid}/status").json()
        if not isinstance(data, dict):
            raise ApiError("Resposta de status do export nao e um objeto JSON.")
        return data

    def list_export_jobs(self) -> list[dict[str, Any]]:
        data = self.request("GET", "/vulns/export/status").json()
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            for key in ("exports", "jobs", "data", "items"):
                value = data.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        raise ApiError("Resposta da lista de exports possui formato inesperado.")

    def list_tag_values(self, *, page_size: int = 5000) -> list[dict[str, Any]]:
        bounded_page_size = max(1, min(int(page_size), 5000))
        offset = 0
        records: list[dict[str, Any]] = []
        while True:
            query = urlencode({
                "limit": bounded_page_size,
                "offset": offset,
                "sort": "category_name:asc,value:asc",
            })
            data = self.request("GET", f"/tags/values?{query}").json()
            if isinstance(data, list):
                page = [item for item in data if isinstance(item, dict)]
                total = None
            elif isinstance(data, dict):
                raw = next((
                    data.get(key) for key in ("values", "tags", "items", "data")
                    if isinstance(data.get(key), list)
                ), None)
                if raw is None:
                    raise ApiError("Resposta da lista de tags possui formato inesperado.")
                page = [item for item in raw if isinstance(item, dict)]
                pagination = data.get("pagination")
                total_value = (
                    pagination.get("total") if isinstance(pagination, dict)
                    else data.get("total")
                )
                try:
                    total = int(total_value) if total_value is not None else None
                except (TypeError, ValueError):
                    total = None
            else:
                raise ApiError("Resposta da lista de tags possui formato inesperado.")
            records.extend(page)
            offset += len(page)
            if not page or len(page) < bounded_page_size or (
                total is not None and offset >= total
            ):
                return records

    def list_assets_for_tag(
        self,
        category_name: str,
        value: str,
        *,
        page_size: int = 5000,
    ) -> list[dict[str, Any]]:
        category = category_name.strip()
        tag_value = value.strip()
        if not category or not tag_value:
            raise ValueError("Categoria e valor da tag sao obrigatorios.")
        bounded_page_size = max(1, min(int(page_size), 5000))
        offset = 0
        records: list[dict[str, Any]] = []
        while True:
            query = urlencode({
                "filter.0.filter": f"tag.{category}",
                "filter.0.quality": "eq",
                "filter.0.value": tag_value,
                "filter.search_type": "and",
                "limit": bounded_page_size,
                "offset": offset,
            })
            data = self.request("GET", f"/workbenches/assets?{query}").json()
            if not isinstance(data, dict):
                raise ApiError("Resposta de ativos por tag possui formato inesperado.")
            raw = next((
                data.get(key) for key in ("assets", "items", "data")
                if isinstance(data.get(key), list)
            ), None)
            if raw is None:
                raise ApiError("Resposta de ativos por tag nao contem lista de ativos.")
            page = [item for item in raw if isinstance(item, dict)]
            records.extend(page)
            pagination = data.get("pagination")
            total_value = (
                pagination.get("total") if isinstance(pagination, dict)
                else data.get("total")
            )
            try:
                total = int(total_value) if total_value is not None else None
            except (TypeError, ValueError):
                total = None
            offset += len(page)
            if total is not None and total > 5000:
                raise ApiError(
                    "A tag possui mais de 5000 ativos; o endpoint Workbench nao garante "
                    "uma enumeracao completa desse escopo."
                )
            if total is None and len(page) >= 5000:
                raise ApiError(
                    "A tag retornou o limite de 5000 ativos sem total; nao e seguro "
                    "assumir que o escopo esta completo."
                )
            if not page or len(page) < bounded_page_size or (
                total is not None and offset >= total
            ):
                return records

    def start_asset_export_v2(
        self,
        *,
        filters: Mapping[str, Any] | None = None,
        chunk_size: int = 1000,
        include_open_ports: bool = False,
        include_resource_tags: bool = False,
    ) -> str:
        bounded_chunk_size = int(chunk_size)
        if not 100 <= bounded_chunk_size <= 10000:
            raise ValueError("chunk_size de ativos deve estar entre 100 e 10000.")
        if include_resource_tags and bounded_chunk_size > 1000:
            raise ValueError("chunk_size nao pode exceder 1000 com resource tags.")
        if include_open_ports and include_resource_tags:
            raise ValueError("Nao combine open ports e resource tags no mesmo export.")
        payload: dict[str, Any] = {
            "chunk_size": bounded_chunk_size,
            "include_open_ports": bool(include_open_ports),
            "include_resource_tags": bool(include_resource_tags),
        }
        if filters:
            payload["filters"] = dict(filters)
        try:
            response = self.request(
                "POST",
                "/assets/v2/export",
                json_body=payload,
                retry_status_codes=frozenset({429, 500, 502, 503, 504}),
            )
        except ApiError as exc:
            if exc.status_code == 409 and exc.active_job_id:
                LOGGER.info("Export de ativos equivalente ja estava em andamento; reutilizando o job.")
                return exc.active_job_id
            raise
        data = response.json()
        export_uuid = data.get("export_uuid") or data.get("uuid") if isinstance(data, dict) else None
        if not isinstance(export_uuid, str) or not export_uuid.strip():
            raise ApiError("Resposta de inicio do export de ativos nao contem export_uuid.")
        return export_uuid.strip()

    def get_asset_export_status(self, export_uuid: str) -> dict[str, Any]:
        data = self.request("GET", f"/assets/export/{export_uuid}/status").json()
        if not isinstance(data, dict):
            raise ApiError("Resposta de status do export de ativos nao e um objeto JSON.")
        return data

    def download_asset_chunk_bytes(self, export_uuid: str, chunk_id: int) -> bytes:
        return self.request(
            "GET",
            f"/assets/export/{export_uuid}/chunks/{int(chunk_id)}",
            accept="application/json",
        ).content

    def iter_asset_chunk_bytes(
        self,
        export_uuid: str,
        chunk_id: int,
        *,
        block_size: int = 1024 * 1024,
    ) -> Iterator[bytes]:
        content = self.download_asset_chunk_bytes(export_uuid, chunk_id)
        size = max(1, int(block_size))
        for offset in range(0, len(content), size):
            yield content[offset:offset + size]

    def download_asset_chunk(self, export_uuid: str, chunk_id: int) -> list[dict[str, Any]]:
        return parse_chunk_response(self.download_asset_chunk_bytes(export_uuid, chunk_id))

    def download_chunk_bytes(self, export_uuid: str, chunk_id: int) -> bytes:
        return self.request(
            "GET",
            f"/vulns/export/{export_uuid}/chunks/{int(chunk_id)}",
            accept="application/octet-stream",
        ).content

    def iter_chunk_bytes(
        self,
        export_uuid: str,
        chunk_id: int,
        *,
        block_size: int = 1024 * 1024,
    ) -> Iterator[bytes]:
        content = self.download_chunk_bytes(export_uuid, chunk_id)
        size = max(1, int(block_size))
        for offset in range(0, len(content), size):
            yield content[offset:offset + size]

    def download_chunk(self, export_uuid: str, chunk_id: int) -> list[dict[str, Any]]:
        return parse_chunk_response(self.download_chunk_bytes(export_uuid, chunk_id))

    @staticmethod
    def completed_chunk_ids(status: Mapping[str, Any]) -> list[int]:
        raw = status.get("chunks_available")
        if raw is None:
            raw = status.get("chunks")
        if raw is None:
            raw = []
        if isinstance(raw, dict):
            raw = list(raw.keys())
        if not isinstance(raw, list):
            raise ApiError("chunks_available possui formato inesperado.")
        chunk_ids: list[int] = []
        for value in raw:
            try:
                chunk_ids.append(int(value))
            except (TypeError, ValueError) as exc:
                raise ApiError("A resposta de status contem um chunk_id invalido.") from exc
        return sorted(set(chunk_ids))

    @staticmethod
    def chunk_count(status: Mapping[str, Any], key: str) -> int:
        value = status.get(key)
        if value is None:
            return 0
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return max(0, int(value))
        if isinstance(value, (list, dict, tuple, set)):
            return len(value)
        return 0

    def _wait_for_completion(
        self,
        export_uuid: str,
        status_loader: Callable[[str], dict[str, Any]],
        *,
        label: str,
        progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> tuple[dict[str, Any], list[int]]:
        started = self.monotonic()
        seen: list[int] = []
        progress_made = False
        while True:
            status = status_loader(export_uuid)
            state = str(status.get("status") or status.get("state") or "").strip().lower()
            current = self.completed_chunk_ids(status)
            if current:
                seen = current
                progress_made = True
            elapsed = max(0.0, self.monotonic() - started)
            progress = {
                **status,
                "export_uuid": export_uuid,
                "status": state.upper() or "UNKNOWN",
                "completed_chunks": len(seen),
                "total_chunks": self.chunk_count(status, "total_chunks"),
                "failed_chunks": self.chunk_count(status, "chunks_failed"),
                "cancelled_chunks": self.chunk_count(status, "chunks_cancelled"),
                "elapsed_seconds": round(elapsed, 3),
                "progress_made": progress_made,
            }
            if progress_callback is not None:
                try:
                    progress_callback(progress)
                except Exception:
                    LOGGER.exception("Falha ignorada no callback de progresso do export.")
            if state in SUCCESS_STATES:
                failed = self.chunk_count(status, "chunks_failed")
                cancelled = self.chunk_count(status, "chunks_cancelled")
                total = self.chunk_count(status, "total_chunks")
                if failed or cancelled:
                    raise ExportFailedError(
                        f"Export {label} terminou com chunks falhos ou cancelados. "
                        f"failed={failed} cancelled={cancelled} total={total}."
                    )
                return status, seen
            if state in FAILURE_STATES:
                raise ExportFailedError(f"Export {label} terminou com estado {state}.")
            if elapsed >= self.config.max_wait_seconds:
                raise ExportTimeoutError(
                    f"Tempo maximo excedido aguardando o export {label}.",
                    export_uuid=export_uuid,
                    last_status=progress,
                    progress_made=progress_made,
                )
            self.sleep(self.config.poll_seconds)

    def wait_for_completion(
        self,
        export_uuid: str,
        *,
        progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> tuple[dict[str, Any], list[int]]:
        return self._wait_for_completion(
            export_uuid,
            self.get_export_status,
            label="VM",
            progress_callback=progress_callback,
        )

    def wait_for_asset_completion(self, export_uuid: str) -> tuple[dict[str, Any], list[int]]:
        return self._wait_for_completion(
            export_uuid,
            self.get_asset_export_status,
            label="de ativos",
        )
