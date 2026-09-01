from __future__ import annotations

import json
import ssl
import time
from dataclasses import dataclass
from http.client import HTTPResponse
from typing import Any, Callable, Iterator, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, Request, build_opener

from tenable_reports.domain.execution_control import ExecutionInterruptedError


TRANSIENT_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
AUTH_GRAPHQL_CODES = frozenset({"UNAUTHENTICATED", "UNAUTHORIZED", "FORBIDDEN"})
RATE_LIMIT_GRAPHQL_CODES = frozenset({"RATE_LIMITED", "RATE_LIMIT", "TOO_MANY_REQUESTS"})
TEMPORARY_GRAPHQL_CODES = frozenset({"INTERNAL_SERVER_ERROR", "TIMEOUT", "SERVICE_UNAVAILABLE"})
COMPLEXITY_GRAPHQL_CODES = frozenset({"QUERY_TOO_COMPLEX", "COMPLEXITY_LIMIT_EXCEEDED", "MAX_COST_EXCEEDED"})


class CloudGraphQLError(RuntimeError):
    failure_code = "UNEXPECTED"
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        root_field: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.root_field = root_field


class CloudAuthError(CloudGraphQLError):
    failure_code = "TENABLE_AUTH_INVALID"


class CloudRateLimitError(CloudGraphQLError):
    failure_code = "TENABLE_RATE_LIMIT"
    retryable = True


class CloudTemporaryError(CloudGraphQLError):
    failure_code = "TENABLE_TEMPORARY"
    retryable = True


class CloudContractError(CloudGraphQLError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        root_field: str | None = None,
        reducible_page_size: bool = False,
    ) -> None:
        super().__init__(message, status_code=status_code, root_field=root_field)
        self.reducible_page_size = reducible_page_size


@dataclass(frozen=True, slots=True)
class CloudGraphQLConfig:
    endpoint: str
    api_secret: str
    user_agent: str = "RelatorioTenableMensal/0.1"
    timeout_seconds: float = 180.0
    retries: int = 4
    ca_bundle: str | None = None
    min_page_size: int = 5

    def __post_init__(self) -> None:
        parsed = urlsplit(self.endpoint)
        if (
            parsed.scheme.lower() != "https"
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("endpoint Cloud deve ser uma URL HTTPS permitida.")
        if not self.api_secret.strip():
            raise CloudAuthError("Credencial Tenable Cloud Security nao configurada.")
        if not self.user_agent.strip():
            raise ValueError("user_agent Cloud nao pode ser vazio.")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds Cloud deve ser positivo.")
        if not 0 <= self.retries <= 10:
            raise ValueError("retries Cloud deve estar entre 0 e 10.")
        if self.min_page_size < 1:
            raise ValueError("min_page_size Cloud deve ser positivo.")


@dataclass(frozen=True, slots=True)
class CloudTransportResponse:
    status_code: int
    headers: Mapping[str, str]
    content: bytes


@dataclass(frozen=True, slots=True)
class CloudGraphQLPage:
    """One validated page, yielded before the next request is issued."""

    nodes: tuple[dict[str, Any], ...]
    page: int
    records: int
    end_cursor: str | None
    has_next_page: bool
    page_size: int


class CloudTransport(Protocol):
    def send(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> CloudTransportResponse:
        raise NotImplementedError


class UrllibCloudTransport:
    def __init__(self, *, ca_bundle: str | None = None) -> None:
        context = ssl.create_default_context(cafile=ca_bundle)
        self._opener = build_opener(HTTPSHandler(context=context))

    def send(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> CloudTransportResponse:
        request = Request(url, data=body, headers=dict(headers), method=method.upper())
        try:
            response: HTTPResponse = self._opener.open(request, timeout=timeout)
            return CloudTransportResponse(
                status_code=response.status,
                headers={key.lower(): value for key, value in response.headers.items()},
                content=response.read(),
            )
        except HTTPError as exc:
            return CloudTransportResponse(
                status_code=exc.code,
                headers={key.lower(): value for key, value in exc.headers.items()},
                content=exc.read(),
            )
        except (URLError, TimeoutError, OSError) as exc:
            raise CloudTemporaryError(
                "Falha temporaria de transporte na API Tenable Cloud Security."
            ) from exc


def _json_body(query: str, variables: Mapping[str, Any]) -> bytes:
    return json.dumps(
        {"query": query, "variables": dict(variables)},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _header(response: CloudTransportResponse, name: str) -> str | None:
    expected = name.lower()
    for key, value in response.headers.items():
        if key.lower() == expected:
            return value
    return None


def _retry_delay(response: CloudTransportResponse, attempt: int) -> float:
    retry_after = _header(response, "retry-after")
    if retry_after:
        try:
            return max(0.0, min(float(retry_after.strip()), 300.0))
        except ValueError:
            pass
    return min(60.0, float(2 ** max(0, attempt - 1)))


def _graphql_error(
    errors: Any,
    *,
    root_field: str | None,
) -> CloudGraphQLError:
    if not isinstance(errors, list) or not errors:
        return CloudContractError(
            "A resposta GraphQL informou erro em formato invalido.",
            root_field=root_field,
        )
    codes: set[str] = set()
    messages: list[str] = []
    for item in errors:
        if not isinstance(item, Mapping):
            continue
        extensions = item.get("extensions")
        if isinstance(extensions, Mapping):
            code = str(extensions.get("code") or "").strip().upper()
            if code:
                codes.add(code)
        message = str(item.get("message") or "").strip()
        if message:
            messages.append(message.lower())
    joined = " ".join(messages)
    if codes & AUTH_GRAPHQL_CODES or any(
        token in joined for token in ("unauthorized", "forbidden", "not authorized")
    ):
        return CloudAuthError(
            "A Tenable Cloud Security rejeitou a credencial ou permissao.",
            root_field=root_field,
        )
    if codes & RATE_LIMIT_GRAPHQL_CODES or "rate limit" in joined:
        return CloudRateLimitError(
            "A Tenable Cloud Security limitou temporariamente as consultas.",
            root_field=root_field,
        )
    if codes & COMPLEXITY_GRAPHQL_CODES or "too complex" in joined or "complexity" in joined:
        return CloudContractError(
            "A consulta GraphQL excedeu a complexidade permitida.",
            root_field=root_field,
            reducible_page_size=True,
        )
    if codes & TEMPORARY_GRAPHQL_CODES:
        return CloudTemporaryError(
            "A Tenable Cloud Security retornou uma falha temporaria.",
            root_field=root_field,
        )
    return CloudContractError(
        "A resposta GraphQL contem erros incompativeis com o contrato.",
        root_field=root_field,
    )


class CloudGraphQLClient:
    def __init__(
        self,
        config: CloudGraphQLConfig,
        *,
        transport: CloudTransport | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.transport = transport or UrllibCloudTransport(ca_bundle=config.ca_bundle)
        self.sleeper = sleeper
        self._headers = {
            "Authorization": f"Bearer {config.api_secret}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": config.user_agent,
        }

    def _execute(
        self,
        query: str,
        variables: Mapping[str, Any],
        *,
        root_field: str | None,
    ) -> dict[str, Any]:
        last_response: CloudTransportResponse | None = None
        for attempt in range(1, self.config.retries + 2):
            try:
                response = self.transport.send(
                    "POST",
                    self.config.endpoint,
                    headers=self._headers,
                    body=_json_body(query, variables),
                    timeout=self.config.timeout_seconds,
                )
            except CloudTemporaryError:
                if attempt > self.config.retries:
                    raise
                self.sleeper(min(60.0, float(2 ** max(0, attempt - 1))))
                continue
            last_response = response
            if response.status_code in TRANSIENT_STATUS_CODES:
                if attempt <= self.config.retries:
                    self.sleeper(_retry_delay(response, attempt))
                    continue
                if response.status_code == 429:
                    raise CloudRateLimitError(
                        "A Tenable Cloud Security limitou temporariamente as consultas.",
                        status_code=429,
                        root_field=root_field,
                    )
                raise CloudTemporaryError(
                    "A Tenable Cloud Security retornou uma falha HTTP temporaria.",
                    status_code=response.status_code,
                    root_field=root_field,
                )
            if response.status_code in {401, 403}:
                raise CloudAuthError(
                    "A Tenable Cloud Security rejeitou a credencial ou permissao.",
                    status_code=response.status_code,
                    root_field=root_field,
                )
            if response.status_code >= 400:
                raise CloudContractError(
                    "A Tenable Cloud Security rejeitou a consulta GraphQL.",
                    status_code=response.status_code,
                    root_field=root_field,
                )
            try:
                payload = json.loads(response.content.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise CloudContractError(
                    "A API Tenable Cloud Security retornou JSON invalido.",
                    root_field=root_field,
                ) from exc
            if not isinstance(payload, Mapping):
                raise CloudContractError(
                    "A resposta GraphQL deve ser um objeto JSON.",
                    root_field=root_field,
                )
            if payload.get("errors"):
                error = _graphql_error(payload.get("errors"), root_field=root_field)
                if isinstance(error, (CloudRateLimitError, CloudTemporaryError)) and attempt <= self.config.retries:
                    self.sleeper(min(60.0, float(2 ** max(0, attempt - 1))))
                    continue
                raise error
            data = payload.get("data")
            if not isinstance(data, Mapping):
                raise CloudContractError(
                    "A resposta GraphQL nao contem o objeto data.",
                    root_field=root_field,
                )
            return dict(data)
        if last_response is None:
            raise CloudTemporaryError(
                "A Tenable Cloud Security nao retornou uma resposta valida.",
                root_field=root_field,
            )
        raise CloudTemporaryError(
            "A Tenable Cloud Security excedeu as tentativas configuradas.",
            status_code=last_response.status_code,
            root_field=root_field,
        )

    def execute(self, query: str, variables: Mapping[str, Any]) -> dict[str, Any]:
        return self._execute(query, variables, root_field=None)

    def paginate_pages(
        self,
        query: str,
        root_field: str,
        *,
        page_size: int,
        after: str | None = None,
        pages_completed: int = 0,
        records_completed: int = 0,
        max_pages: int = 0,
        variables: Mapping[str, Any] | None = None,
        progress: Callable[[Mapping[str, Any]], None] | None = None,
        cancellation_probe: Callable[[], bool] | None = None,
    ) -> Iterator[CloudGraphQLPage]:
        if page_size < 1:
            raise ValueError("page_size deve ser positivo.")
        if pages_completed < 0 or records_completed < 0:
            raise ValueError("Contadores de retomada nao podem ser negativos.")
        if max_pages < 0:
            raise ValueError("max_pages nao pode ser negativo.")
        current_size = max(self.config.min_page_size, page_size)
        cursor = after
        seen_cursors: set[str] = {after} if after else set()
        page = pages_completed
        records = records_completed
        base_variables = dict(variables or {})
        while True:
            if cancellation_probe is not None and cancellation_probe():
                raise ExecutionInterruptedError(
                    "Execucao Cloud interrompida com checkpoint preservado."
                )
            request_variables = dict(base_variables)
            request_variables.update({"first": current_size, "after": cursor})
            try:
                data = self._execute(
                    query,
                    request_variables,
                    root_field=root_field,
                )
            except CloudContractError as exc:
                reduced_size = max(self.config.min_page_size, current_size // 2)
                if exc.reducible_page_size and reduced_size < current_size:
                    current_size = reduced_size
                    continue
                raise
            connection = data.get(root_field)
            if not isinstance(connection, Mapping):
                raise CloudContractError(
                    "A resposta GraphQL nao contem a conexao solicitada.",
                    root_field=root_field,
                )
            nodes = connection.get("nodes")
            page_info = connection.get("pageInfo")
            if not isinstance(nodes, list) or any(
                not isinstance(item, Mapping) for item in nodes
            ):
                raise CloudContractError(
                    "A conexao GraphQL retornou nodes invalidos.",
                    root_field=root_field,
                )
            if not isinstance(page_info, Mapping) or not isinstance(
                page_info.get("hasNextPage"), bool
            ):
                raise CloudContractError(
                    "A conexao GraphQL retornou pageInfo invalido.",
                    root_field=root_field,
                )
            has_next_page = page_info["hasNextPage"]
            end_cursor = page_info.get("endCursor")
            if has_next_page:
                if not isinstance(end_cursor, str) or not end_cursor.strip():
                    raise CloudContractError(
                        "A paginacao GraphQL nao retornou cursor para a proxima pagina.",
                        root_field=root_field,
                    )
                if end_cursor in seen_cursors:
                    raise CloudContractError(
                        "A paginacao GraphQL repetiu o cursor e foi interrompida.",
                        root_field=root_field,
                    )
            page += 1
            records += len(nodes)
            page_result = CloudGraphQLPage(
                nodes=tuple(dict(item) for item in nodes),
                page=page,
                records=records,
                end_cursor=end_cursor if isinstance(end_cursor, str) else None,
                has_next_page=has_next_page,
                page_size=current_size,
            )
            if progress is not None:
                progress(
                    {
                        "root_field": root_field,
                        "page": page,
                        "records": records,
                        "page_size": current_size,
                        "has_next_page": has_next_page,
                    }
                )
            yield page_result
            if not has_next_page:
                return
            if max_pages and page >= max_pages:
                raise CloudContractError(
                    "A paginacao GraphQL excedeu o limite de paginas configurado.",
                    root_field=root_field,
                )
            seen_cursors.add(end_cursor)
            cursor = end_cursor

    def paginate(
        self,
        query: str,
        root_field: str,
        *,
        page_size: int,
        max_pages: int = 0,
        variables: Mapping[str, Any] | None = None,
        progress: Callable[[Mapping[str, Any]], None] | None = None,
        cancellation_probe: Callable[[], bool] | None = None,
    ) -> Iterator[dict[str, Any]]:
        for page in self.paginate_pages(
            query,
            root_field,
            page_size=page_size,
            max_pages=max_pages,
            variables=variables,
            progress=progress,
            cancellation_probe=cancellation_probe,
        ):
            yield from page.nodes