# Relatório Tenable Cloud Security Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrar a coleta GraphQL do Tenable Cloud Security à execução mensal de cada cliente e gerar, durante a homologação, os modelos DOCX Base e Ampliado a partir de uma única fotografia Cloud reproduzível.

**Architecture:** Um cliente GraphQL isolado coleta fontes obrigatórias e opcionais em staging, com paginação, checkpoint e contrato de capacidade. Modelos de domínio normalizam ativos, CVEs, findings, inventário e ciclo de vida; um dataset Cloud único alimenta os dois renderizadores e um snapshot compacto PostgreSQL. A execução principal trata Cloud como componente habilitado e tolerante a falha, publica documentos tipados e permite retentativa exclusiva do Cloud.

**Tech Stack:** Python 3.11+, biblioteca padrão `urllib`, `python-docx`, Pillow, PostgreSQL/`psycopg`, servidor HTTP e frontend JavaScript existentes, `pytest`, LibreOffice para QA visual.

**Spec:** `docs/superpowers/specs/2026-08-26-relatorio-cloud-security-design.md`

## Global Constraints

- Começar a execução em `main` limpo e criar exatamente uma branch ativa: `codex/relatorio-cloud-security`; não criar outra branch ou worktree durante esta entrega.
- O relatório Cloud é opcional por cliente e, quando habilitado, começa na mesma execução dos demais produtos.
- Falha Cloud não bloqueia VM, WAS, customizado ou relatórios por TAG.
- O token fica somente em `credentials/<client_id>.env` como `TCS_API_SECRET` e nunca retorna ao navegador, banco, perfil, log, manifesto ou argumento de subprocesso.
- A coleta Cloud é uma fotografia do instante de execução; somente snapshots `MAIN` compatíveis alimentam comparativos.
- No máximo uma coleta Cloud completa por cliente e ambiente ocorre em 24 horas. Snapshot exato é reutilizado; outra coleta recente exige confirmação explícita e nunca é disparada silenciosamente pela automação.
- Máquinas virtuais, imagens, findings de postura e inventário mantêm populações distintas e identidade por ID da API.
- VPR `0` é zero; VPR ausente é `N/D` e não participa do ranking como pontuação real.
- Descrição e remediação usam consultas separadas e limitadas; não ampliar silenciosamente a consulta obrigatória com campos pesados.
- Seção sem população recebe mensagem explícita. Fonte que falhou é `indisponível`, nunca zero.
- O primeiro piloto gera Modelo Base e Modelo Ampliado a partir do mesmo dataset e da mesma fotografia.
- Módulos condicionais só aparecem quando o contrato e a população do tenant forem comprovados.
- Intermediários pesados só são removidos depois de documentos validados e snapshot compacto confirmado.
- Não iniciar servidor, coleta real ou alteração no PostgreSQL real durante tarefas unitárias.
- Cada mudança de comportamento começa por um teste que falha pelo motivo esperado.
- Commits são pequenos e permanecem na mesma branch até revisão, merge e push.

Os factories usados nos exemplos de teste (`valid_profile_dict`, `cloud_config`, `cloud_request`, `cloud_dataset_fixture`, `cloud_profile` e equivalentes) são helpers locais definidos no próprio módulo de teste. Eles usam apenas valores fictícios, retornam o tipo indicado pelo nome e não fazem rede, banco real ou leitura de credenciais. `ScriptedTransport`, `FakeClientFactory`, `CloudExecutionCalls`, `RetryServices` e o cliente web de teste registram chamadas para que cada teste possa provar também o que **não** foi executado.

---

## File Structure

### Novos arquivos

- `src/tenable_reports/domain/cloud.py` — tipos normalizados, enums de fonte e contratos do dataset Cloud.
- `src/tenable_reports/infrastructure/tenable_cloud/__init__.py` — exportações públicas do adaptador Cloud.
- `src/tenable_reports/infrastructure/tenable_cloud/client.py` — transporte GraphQL, retries, paginação adaptativa e erros seguros.
- `src/tenable_reports/infrastructure/tenable_cloud/queries.py` — consultas GraphQL versionadas e candidatos de endpoint.
- `src/tenable_reports/application/cloud_contract.py` — teste mínimo de endpoint e capacidades.
- `src/tenable_reports/application/collect_cloud.py` — coleta, checkpoint, manifesto e estados por fonte.
- `src/tenable_reports/application/normalize_cloud.py` — conversão de respostas GraphQL em modelos de domínio.
- `src/tenable_reports/application/cloud_corrections.py` — classificação determinística e proveniência das correções.
- `src/tenable_reports/application/cloud_enrichment.py` — seleção e enriquecimento das CVEs que serão publicadas.
- `src/tenable_reports/application/cloud_report_dataset.py` — métricas, rankings, proveniência e JSON do relatório.
- `src/tenable_reports/application/cloud_snapshots.py` — snapshot compacto, replay e protocolo de repositório.
- `src/tenable_reports/application/cloud_execution.py` — caso de uso Cloud completo e isolado da execução VM.
- `src/tenable_reports/infrastructure/cloud_snapshots_postgresql.py` — persistência e leitura de snapshots/capacidades.
- `src/tenable_reports/infrastructure/postgresql_migrations/0007_cloud_reports.sql` — tabelas Cloud e metadados dos documentos.
- `src/tenable_reports/presentation/cloud_editorial_catalog.py` — textos exatos e sanitizados do modelo aprovado.
- `src/tenable_reports/presentation/cloud_report_docx.py` — orquestração dos modelos Base e Ampliado.
- `src/tenable_reports/presentation/cloud_report_sections.py` — seções e tabelas Cloud sem cálculo de métricas.
- `src/tenable_reports/presentation/cloud_visuals.py` — gráficos determinísticos do Modelo Ampliado.
- `templates/corporate/cloud-base-v1.docx` — template Cloud sanitizado e versionado.
- `scripts/distill_cloud_template.py` — sanitização reproduzível do DOCX externo de referência.
- `scripts/render_cloud_report_fixture.py` — prova Base/Ampliada com dados fictícios.
- `tests/fixtures/tenable_cloud/virtual-machines-page-1.json` — resposta mínima obrigatória sanitizada.
- `tests/fixtures/tenable_cloud/container-images-page-1.json` — resposta mínima obrigatória sanitizada.
- `tests/fixtures/tenable_cloud/findings-page-1.json` — postura e remediação sanitizadas.
- `tests/fixtures/tenable_cloud/inventory-page-1.json` — inventário sanitizado.
- `tests/fixtures/tenable_cloud/lifecycle-page-1.json` — ciclo de vida sanitizado.
- `tests/test_cloud_client.py` — transporte, retries e paginação.
- `tests/test_cloud_contract.py` — endpoint e capacidades.
- `tests/test_cloud_collection.py` — manifestos, fontes obrigatórias/opcionais e progresso.
- `tests/test_cloud_normalization.py` — identidade, ausência e datas.
- `tests/test_cloud_corrections.py` — classificação e proveniência.
- `tests/test_cloud_enrichment.py` — seleção e fallback de enriquecimento.
- `tests/test_cloud_report_dataset.py` — métricas, rankings e filtros.
- `tests/test_cloud_snapshots.py` — compactação, replay e compatibilidade.
- `tests/test_cloud_snapshots_postgresql.py` — SQL e seleção por `MAIN`.
- `tests/test_cloud_report_docx.py` — estrutura dos dois DOCX.
- 	ests/test_cloud_execution.py — sucesso, replay e falha isolada.
- 	ests/test_customizations_report_docx.py — supressão do módulo Cloud provisório que foi substituído pelo DOCX próprio.

### Arquivos modificados

- `src/tenable_reports/config/profile.py` — ambiente e variante editorial Cloud.
- `src/tenable_reports/config/environment.py` — `CloudCredentialConfig`.
- `src/tenable_reports/config/__init__.py` — exportações dos contratos Cloud.
- `src/tenable_reports/application/failures.py` — códigos de falha Cloud sem secret.
- `src/tenable_reports/application/orchestration.py` — progresso e sucesso com alertas.
- `src/tenable_reports/application/publishing.py` — documento `cloud`, variante e datasets adicionais.
- `src/tenable_reports/application/report_set_purge.py` — inclusão segura dos artefatos Cloud.
- `src/tenable_reports/application/retention.py` — proteção de staging Cloud pendente.
- `src/tenable_reports/infrastructure/postgresql.py` — metadados de publicação e contexto de retentativa.
- `src/tenable_reports/infrastructure/report_set_purge_postgresql.py` — exclusão transacional do snapshot Cloud.
- `src/tenable_reports/presentation/__init__.py` — exports do renderer Cloud.
- `src/tenable_reports/presentation/report_filenames.py` — nomes Base e Ampliado.
- `src/tenable_reports/presentation/source_filters.py` — notas GraphQL discretas.
- `src/tenable_reports/presentation/customizations_report_docx.py` — compatibilidade do módulo legado sem duplicar conteúdo Cloud.
- `src/tenable_reports/cli.py` — coleta/renderização no `run-client`, teste e retentativa Cloud.
- `src/tenable_reports/webapp/server.py` — token, status, teste, progresso e retentativa.
- `src/tenable_reports/webapp/static/index.html` — controles Cloud.
- `src/tenable_reports/webapp/static/app.js` — estado, ações e agrupamento dos documentos.
- `src/tenable_reports/webapp/static/app.css` — layout compacto dos novos estados.
- `clients/examples/*.json` e `orchestration/clients.example.json` — exemplos sanitizados.
- `tests/test_profile_environment.py`, `tests/test_failures.py`, `tests/test_cli.py`, `tests/test_orchestration.py`, `tests/test_postgresql.py`, `tests/test_report_filenames.py`, `tests/test_source_filters.py`, `tests/test_report_set_purge.py`, `tests/test_report_set_purge_postgresql.py`, `tests/test_retention.py`, `tests/test_webapp.py` — regressões integradas.
- `README.md`, `docs/19-visao-geral-e-objetivos.md`, `docs/20-arquitetura-e-fluxo-de-dados.md`, `docs/21-catalogo-de-dados-e-metricas.md`, `docs/22-guia-operacional.md`, `docs/23-guia-de-desenvolvimento.md` — estado vigente e operação.

---

### Task 1: Configuração de perfil e credencial Cloud

**Files:**
- Modify: `src/tenable_reports/config/profile.py:88-170,235-455`
- Modify: `src/tenable_reports/config/environment.py:1-180`
- Modify: `src/tenable_reports/config/__init__.py:1-30`
- Modify: `clients/examples/client-profile.json`
- Modify: `tests/test_profile_environment.py`
- Modify: `src/tenable_reports/presentation/customizations_report_docx.py`
- Modify: `tests/test_customizations_report_docx.py`

**Interfaces:**
- Produces: `CloudSecurityScope(enabled: bool, environment: str, layout: str)`.
- Produces: `CloudCredentialConfig.from_environment(environ) -> CloudCredentialConfig`.
- Preserves: leitura de `TCS_API_KEY` somente como alias legado; escrita usa `TCS_API_SECRET`.
- Produces: `resolve_custom_intelligence_modules(profile)` com módulos ativos e motivos de supressão.
- Preserves: perfis legados com `cloud_container_images` continuam válidos, mas o módulo é omitido com `MOVED_TO_CLOUD_REPORT` quando o relatório Cloud próprio está habilitado.
- Consumes later: `scope.environment in {'global', 'us_gov'}` e `scope.layout in {'comparison', 'base', 'expanded'}`.

- [x] **Step 1: Escrever os testes que definem perfil, segredo e compatibilidade**

```python
def test_cloud_scope_accepts_environment_and_comparison_layout() -> None:
    payload = valid_profile_dict()
    payload["scope"]["cloud_security"] = {
        "enabled": True,
        "environment": "global",
        "layout": "comparison",
    }
    profile = ClientProfile.from_dict(payload)
    assert profile.cloud_security_scope.environment == "global"
    assert profile.cloud_security_scope.layout == "comparison"


def test_cloud_credentials_keep_zero_secrets_out_of_profile() -> None:
    config = CloudCredentialConfig.from_environment({"TCS_API_SECRET": "test-secret"})
    assert config.is_complete is True
    assert config.api_secret == "test-secret"


def test_legacy_tcs_api_key_remains_read_only_compatible() -> None:
    config = CloudCredentialConfig.from_environment({"TCS_API_KEY": "legacy-secret"})
    assert config.api_secret == "legacy-secret"
    assert config.used_legacy_alias is True


def test_standalone_cloud_report_supersedes_legacy_custom_module() -> None:
    payload = valid_profile_dict()
    payload["scope"]["cloud_security"] = {"enabled": True}
    payload["report"]["intelligence_modules"] = ["cloud_container_images"]
    active, suppressed = resolve_custom_intelligence_modules(
        ClientProfile.from_dict(payload)
    )
    assert "cloud_container_images" not in active
    assert suppressed["cloud_container_images"] == "MOVED_TO_CLOUD_REPORT"
```

- [x] **Step 2: Executar os testes e confirmar a falha inicial**

Run:

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
.\.venv\Scripts\python.exe -m pytest tests/test_profile_environment.py -q
```

Expected: FAIL porque `CloudSecurityScope` não possui `environment/layout` e `CloudCredentialConfig` não existe.

- [x] **Step 3: Implementar o contrato mínimo**

```python
@dataclass(frozen=True, slots=True)
class CloudSecurityScope:
    enabled: bool = False
    environment: str = "global"
    layout: str = "comparison"


@dataclass(frozen=True, slots=True)
class CloudCredentialConfig:
    api_secret: str
    ca_bundle: str | None = None
    timeout_seconds: float = 180.0
    retries: int = 4
    used_legacy_alias: bool = False

    @property
    def is_complete(self) -> bool:
        return bool(self.api_secret.strip())
```

Validar os enums textuais, CA bundle, timeout positivo e retries entre `0` e `10`. O perfil continua rejeitando qualquer chave cujo nome contenha token, secret ou password. O resolver mantém o JSON legado legível, mas impede que `cloud_container_images` crie título, página ou tabela duplicada no customizado quando `cloud_security.enabled=true`.

- [x] **Step 4: Executar testes de perfil e exemplos**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_profile_environment.py tests/test_project_guidance.py -q
```

Expected: PASS.

- [x] **Step 5: Commit**

```powershell
git add src/tenable_reports/config src/tenable_reports/presentation/customizations_report_docx.py clients/examples tests/test_profile_environment.py tests/test_customizations_report_docx.py
git commit -m "feat: define cloud report configuration"
```

---

### Task 2: Cliente GraphQL seguro e paginação adaptativa

**Files:**
- Create: `src/tenable_reports/infrastructure/tenable_cloud/__init__.py`
- Create: `src/tenable_reports/infrastructure/tenable_cloud/client.py`
- Create: `tests/test_cloud_client.py`
- Modify: `src/tenable_reports/application/failures.py:1-100`
- Modify: `tests/test_failures.py`

**Interfaces:**
- Consumes: `CloudCredentialConfig`.
- Produces: `CloudGraphQLConfig`, `CloudGraphQLClient.execute()` e `CloudGraphQLClient.paginate()`.
- Produces errors: `CloudAuthError`, `CloudRateLimitError`, `CloudTemporaryError`, `CloudContractError`.
- Produces progress: callback `Callable[[Mapping[str, Any]], None]` sem conteúdo sensível.

- [x] **Step 1: Escrever testes para headers, erros, cursor e redução de página**

```python
def test_paginate_uses_bearer_user_agent_and_all_cursors() -> None:
    transport = ScriptedTransport([
        response(200, connection("VirtualMachines", [{"Id": "asset-a"}], True, "c1")),
        response(200, connection("VirtualMachines", [{"Id": "asset-b"}], False, None)),
    ])
    client = CloudGraphQLClient(cloud_config("secret"), transport=transport, sleeper=lambda _: None)
    nodes = list(client.paginate("query", "VirtualMachines", page_size=20))
    assert [item["Id"] for item in nodes] == ["asset-a", "asset-b"]
    assert transport.requests[0].headers["Authorization"] == "Bearer secret"
    assert transport.requests[0].headers["User-Agent"].startswith("RelatorioTenableMensal/")


def test_repeated_cursor_is_a_contract_error() -> None:
    client = scripted_client_with_repeated_cursor()
    with pytest.raises(CloudContractError, match="cursor"):
        list(client.paginate("query", "Findings", page_size=20))
```

Cobrir também `401/403`, `429` com `Retry-After`, `502/503/504`, TLS, JSON inválido, GraphQL `errors` e redução `20 -> 10 -> 5` mantendo o mesmo cursor.

- [x] **Step 2: Executar os testes e confirmar a falha inicial**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cloud_client.py tests/test_failures.py -q
```

Expected: FAIL por ausência do adaptador Cloud e dos códigos de falha.

- [x] **Step 3: Implementar o cliente com transporte injetável**

```python
@dataclass(frozen=True, slots=True)
class CloudGraphQLConfig:
    endpoint: str
    api_secret: str
    user_agent: str = "RelatorioTenableMensal/0.1"
    timeout_seconds: float = 180.0
    retries: int = 4
    ca_bundle: str | None = None
    min_page_size: int = 5


class CloudGraphQLClient:
    def execute(self, query: str, variables: Mapping[str, Any]) -> dict[str, Any]:
        return self._validate_graphql_response(
            self._post_with_retry(query=query, variables=variables)
        )
    def paginate(
        self,
        query: str,
        root_field: str,
        *,
        page_size: int,
        max_pages: int = 0,
        progress: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> Iterator[dict[str, Any]]:
        yield from self._paginate_connections(
            query=query,
            root_field=root_field,
            page_size=page_size,
            max_pages=max_pages,
            progress=progress,
        )
```

`_post_with_retry` aplica timeout, TLS, `Retry-After` e backoff; `_validate_graphql_response` rejeita JSON inválido, resposta sem `data` e `errors` fatais; `_paginate_connections` valida `nodes`, `pageInfo`, cursor repetido e limite de páginas, reduzindo `first` até `min_page_size` quando a resposta indicar complexidade. O erro sanitizado inclui status, raiz GraphQL e tentativa, mas exclui query completa, token e corpo sensível. Mapear Cloud auth para `TENABLE_AUTH_INVALID`, rate limit para `TENABLE_RATE_LIMIT` e transporte/timeout para `TENABLE_TEMPORARY`.

- [x] **Step 4: Executar testes focados**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cloud_client.py tests/test_failures.py -q
```

Expected: PASS.

- [x] **Step 5: Commit**

```powershell
git add src/tenable_reports/infrastructure/tenable_cloud src/tenable_reports/application/failures.py tests/test_cloud_client.py tests/test_failures.py
git commit -m "feat: add resilient cloud graphql client"
```

---

### Task 3: Consultas versionadas e teste de contrato

**Files:**
- Create: `src/tenable_reports/infrastructure/tenable_cloud/queries.py`
- Create: `src/tenable_reports/application/cloud_contract.py`
- Create: `tests/test_cloud_contract.py`

**Interfaces:**
- Produces: `cloud_endpoint_candidates(environment: str) -> Sequence[str]`.
- Produces: `CloudCapabilityReport(endpoint, checked_at, connector_version, sources)`.
- Produces source names: `virtual_machines`, `container_images`, `compute_ips`, `inventory`, `findings`, `lifecycle`, `vulnerability_details`, `vulnerability_remediations`.
- Consumes later: `report.required_ready` e `report.source(name).available`.

- [x] **Step 1: Escrever testes do fallback de endpoint e da matriz de capacidades**

```python
def test_probe_uses_current_endpoint_then_legacy_endpoint() -> None:
    factory = FakeClientFactory({
        "https://app.tenable.com/graphql": CloudContractError("rota rejeitada"),
        "https://app.tenable.com/api/graph": required_sources_success(),
    })
    report = probe_cloud_contract("global", factory=factory, now=fixed_now)
    assert report.endpoint == "https://app.tenable.com/api/graph"
    assert report.required_ready is True


def test_optional_source_failure_does_not_hide_required_readiness() -> None:
    report = probe_with_optional_findings_denied()
    assert report.required_ready is True
    assert report.source("findings").status == "UNAVAILABLE"
```

- [x] **Step 2: Confirmar que os testes falham**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cloud_contract.py -q
```

Expected: FAIL porque as queries e o probe ainda não existem.

- [x] **Step 3: Implementar endpoints e probes mínimos**

```python
ENDPOINTS = {
    "global": (
        "https://app.tenable.com/graphql",
        "https://app.tenable.com/api/graph",
    ),
    "us_gov": (
        "https://app.tenable.us/graphql",
        "https://app.tenable.us/api/graph",
    ),
}

@dataclass(frozen=True, slots=True)
class CloudSourceCapability:
    name: str
    status: str
    message: str | None = None

@dataclass(frozen=True, slots=True)
class CloudCapabilityReport:
    endpoint: str
    checked_at: str
    connector_version: str
    sources: Sequence[CloudSourceCapability]
```

Cada probe executa `first: 1` e somente campos usados. `virtual_machines` e `container_images` são obrigatórios. As demais fontes são independentes. Não usar introspecção como fonte de dados; um filtro por CVE só é habilitado se uma consulta real mínima comprovar o contrato.

- [x] **Step 4: Executar testes**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cloud_contract.py tests/test_cloud_client.py -q
```

Expected: PASS.

- [x] **Step 5: Commit**

```powershell
git add src/tenable_reports/infrastructure/tenable_cloud/queries.py src/tenable_reports/application/cloud_contract.py tests/test_cloud_contract.py
git commit -m "feat: probe cloud graphql capabilities"
```

---

### Task 4: Coleta por fonte com checkpoint e progresso

**Files:**
- Create: `src/tenable_reports/application/collect_cloud.py`
- Create: `tests/test_cloud_collection.py`
- Create: `tests/fixtures/tenable_cloud/virtual-machines-page-1.json`
- Create: `tests/fixtures/tenable_cloud/container-images-page-1.json`
- Create: `tests/fixtures/tenable_cloud/findings-page-1.json`
- Create: `tests/fixtures/tenable_cloud/inventory-page-1.json`
- Create: `tests/fixtures/tenable_cloud/lifecycle-page-1.json`

**Interfaces:**
- Consumes: `CloudGraphQLClient` e `CloudCapabilityReport`.
- Produces: `CloudCollectionRequest` e `CloudCollectionArtifact`.
- Produces files: `raw/<client>/<run>/tenable_cloud/<source>.jsonl` e `manifest.json`.
- Produces events: `TENABLE_CLOUD_PROGRESS` com `stage`, `source`, `pages`, `records`, `status`.

- [x] **Step 1: Escrever testes de fonte obrigatória, opcional e retomada**

```python
def test_optional_failure_is_recorded_without_discarding_required_sources(tmp_path: Path) -> None:
    artifact = collect_cloud_snapshot(
        request=cloud_request(tmp_path),
        clients=fake_source_clients(findings=CloudContractError("sem permissão")),
        capabilities=capabilities_all(),
    )
    assert artifact.source_status["virtual_machines"].status == "COMPLETE"
    assert artifact.source_status["findings"].status == "UNAVAILABLE"
    assert artifact.manifest_path.is_file()


def test_required_source_failure_stops_cloud_only(tmp_path: Path) -> None:
    with pytest.raises(CloudRequiredSourceError, match="container_images"):
        collect_cloud_snapshot(
            request=cloud_request(tmp_path),
            clients=fake_source_clients(container_images=CloudTemporaryError("timeout")),
            capabilities=capabilities_all(),
        )
```

Cobrir retomada sem duplicar registros e manifesto sem `Authorization`, token ou conteúdo de descrição/remediação.

- [x] **Step 2: Executar e confirmar falha**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cloud_collection.py -q
```

Expected: FAIL pela ausência do caso de uso.

- [x] **Step 3: Implementar coleta e manifesto atômico**

```python
@dataclass(frozen=True, slots=True)
class CloudCollectionRequest:
    client_id: str
    tenant_id: str
    run_id: str
    execution_type: str
    output_root: Path
    collected_at: str

@dataclass(frozen=True, slots=True)
class CloudCollectionArtifact:
    manifest_path: Path
    source_paths: Mapping[str, Path]
    source_status: Mapping[str, CloudSourceStatus]
    warnings: Sequence[Mapping[str, Any]]
```

Persistir uma página validada antes de pedir a próxima. Um checkpoint guarda último cursor, número de páginas e hash do JSONL. Retomada aceita somente run, fonte, endpoint e versão de consulta compatíveis.

- [x] **Step 4: Executar testes**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cloud_collection.py tests/test_jsonl_io.py -q
```

Expected: PASS.

- [x] **Step 5: Commit**

```powershell
git add src/tenable_reports/application/collect_cloud.py tests/fixtures/tenable_cloud tests/test_cloud_collection.py
git commit -m "feat: collect cloud sources with checkpoints"
```

---

### Task 5: Modelos de domínio e normalização Cloud

**Files:**
- Create: `src/tenable_reports/domain/cloud.py`
- Create: `src/tenable_reports/application/normalize_cloud.py`
- Create: `tests/test_cloud_normalization.py`

**Interfaces:**
- Consumes: JSONL por fonte do `CloudCollectionArtifact`.
- Produces: `NormalizedCloudSnapshot` com assets, occurrences, findings, inventory, lifecycle e quality issues.
- Produces key: `CloudAssetKey(kind: CloudAssetKind, asset_id: str)`.

- [x] **Step 1: Escrever testes de identidade, VPR e populações**

```python
def test_vm_and_image_with_same_remote_id_do_not_collide() -> None:
    snapshot = normalize_cloud_sources(raw_vm("same-id"), raw_image("same-id"))
    assert {item.asset.kind for item in snapshot.occurrences} == {
        CloudAssetKind.VIRTUAL_MACHINE,
        CloudAssetKind.CONTAINER_IMAGE,
    }


def test_vpr_zero_and_missing_remain_distinct() -> None:
    snapshot = normalize_cloud_sources(raw_with_vpr_values(0, None))
    assert snapshot.occurrences[0].vpr == 0.0
    assert snapshot.occurrences[1].vpr is None


def test_posture_finding_is_not_a_cve_occurrence() -> None:
    snapshot = normalize_cloud_sources(raw_posture_finding())
    assert len(snapshot.findings) == 1
    assert snapshot.occurrences == ()
```

- [x] **Step 2: Executar e confirmar falha**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cloud_normalization.py -q
```

Expected: FAIL por ausência dos modelos.

- [x] **Step 3: Implementar tipos imutáveis e normalizador**

```python
class CloudAssetKind(StrEnum):
    VIRTUAL_MACHINE = "virtual_machine"
    CONTAINER_IMAGE = "container_image"

@dataclass(frozen=True, slots=True)
class CloudVulnerabilityOccurrence:
    asset: CloudAssetKey
    vulnerability_id: str
    severity: str
    vpr: float | None
    cvss: float | None
    software: str

@dataclass(frozen=True, slots=True)
class NormalizedCloudSnapshot:
    collected_at: str
    assets: Sequence[CloudAsset]
    occurrences: Sequence[CloudVulnerabilityOccurrence]
    findings: Sequence[CloudFinding]
    inventory: Sequence[CloudInventoryResource]
    lifecycle: Sequence[CloudLifecycleInstance]
    source_status: Mapping[str, CloudSourceStatus]
    quality_issues: Sequence[CloudQualityIssue]
```

Deduplicar ocorrência por `(kind, asset_id, vulnerability_id)`, preservando pior severidade e maiores VPR/CVSS informados. Registros órfãos entram em qualidade e não são associados por aproximação.

- [x] **Step 4: Executar testes**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cloud_normalization.py -q
```

Expected: PASS.

- [x] **Step 5: Commit**

```powershell
git add src/tenable_reports/domain/cloud.py src/tenable_reports/application/normalize_cloud.py tests/test_cloud_normalization.py
git commit -m "feat: normalize cloud security data"
```

---

### Task 6: Enriquecimento seletivo e classificação de correções

**Files:**
- Create: `src/tenable_reports/application/cloud_corrections.py`
- Create: `src/tenable_reports/application/cloud_enrichment.py`
- Create: `tests/test_cloud_corrections.py`
- Create: `tests/test_cloud_enrichment.py`

**Interfaces:**
- Consumes: `NormalizedCloudSnapshot` e `CloudCapabilityReport`.
- Produces: `CloudEnrichmentTargets`, `CloudVulnerabilityEnrichment` e `CloudCorrectionClassification`.
- Correction types: `patch_update`, `version_upgrade`, `configuration_change`, `remove_replace`, `mitigation`, `manual`, `undetermined`.

- [x] **Step 1: Escrever testes da classificação e da seleção**

```python
@pytest.mark.parametrize(("text", "expected"), [
    ("Apply the vendor security patch.", "patch_update"),
    ("Upgrade to version 4.8 or later.", "version_upgrade"),
    ("Disable anonymous access in the service configuration.", "configuration_change"),
    ("Remove the affected package.", "remove_replace"),
    ("Restrict network access as a temporary workaround.", "mitigation"),
])
def test_local_correction_rules_are_deterministic(text: str, expected: str) -> None:
    result = classify_cloud_correction(text)
    assert result.correction_type == expected
    assert result.origin == "local_rule"


def test_explicit_type_precedes_local_rules() -> None:
    result = classify_cloud_correction("Disable the service", explicit_type="Patch")
    assert result.correction_type == "patch_update"
    assert result.origin == "api_explicit"


def test_conflicting_or_negated_local_evidence_is_not_classified() -> None:
    assert classify_cloud_correction(
        "Upgrade the component or disable the affected service."
    ).correction_type == "undetermined"
    assert classify_cloud_correction(
        "No patch is currently available."
    ).correction_type == "undetermined"


def test_enrichment_targets_limit_description_to_top_five() -> None:
    targets = select_cloud_enrichment_targets(snapshot_with_six_critical_cves())
    assert len(targets.detail_cves) == 5
```

- [x] **Step 2: Executar e confirmar falha**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cloud_corrections.py tests/test_cloud_enrichment.py -q
```

Expected: FAIL porque classificadores e seletores não existem.

- [x] **Step 3: Implementar regras versionadas e fallback seguro**

```python
CORRECTION_RULES_VERSION = "cloud-correction-rules-v1"
CORRECTION_RULES = (
    ("patch_update", re.compile(r"\b(apply|install).{0,30}\b(patch|hotfix|security update)\b", re.I)),
    ("version_upgrade", re.compile(r"\b(upgrade|fixed version|version .{0,20} or later)\b", re.I)),
    ("remove_replace", re.compile(r"\b(remove|uninstall|replace)\b", re.I)),
    ("configuration_change", re.compile(r"\b(configure|configuration|disable|enable|set)\b", re.I)),
    ("mitigation", re.compile(r"\b(mitigat|workaround|restrict|block)\b", re.I)),
    ("manual", re.compile(r"\b(manual|contact the vendor|review)\b", re.I)),
)
```

Aplicar primeiro as negações conhecidas; aceitar regra local somente quando exatamente uma categoria positiva casar. Nenhuma ou mais de uma categoria resulta em `undetermined`. Se o probe comprovar filtro por ID, enriquecer somente os IDs escolhidos. Sem esse filtro, executar a consulta separada `VulnerabilityInstances` limitada a `Critical`, incluir `Description` e descartar localmente CVEs fora do Top 5. Remediação usa a consulta documentada de vulnerability findings e só é ligada quando o recurso e a CVE aparecem juntos. Falta de correlação não torna a CVE elegível ao Top 10.

- [x] **Step 4: Executar testes**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cloud_corrections.py tests/test_cloud_enrichment.py -q
```

Expected: PASS.

- [x] **Step 5: Commit**

```powershell
git add src/tenable_reports/application/cloud_corrections.py src/tenable_reports/application/cloud_enrichment.py tests/test_cloud_corrections.py tests/test_cloud_enrichment.py
git commit -m "feat: enrich and classify cloud remediations"
```

---

### Task 7: Dataset Cloud, métricas e proveniência

**Files:**
- Create: `src/tenable_reports/application/cloud_report_dataset.py`
- Create: `tests/test_cloud_report_dataset.py`
- Modify: `src/tenable_reports/presentation/source_filters.py:1-185`
- Modify: `tests/test_source_filters.py`

**Interfaces:**
- Consumes: `NormalizedCloudSnapshot`, enriquecimentos, `ReportingPeriod` e snapshots históricos.
- Produces: `CloudReportDatasetArtifact(directory, dataset_path, dataset)`.
- Dataset: `schema_version=1`, `document_kind='cloud'`, `metric_definition_version='cloud-metrics-v1'`.

- [x] **Step 1: Escrever testes dos rankings e métricas**

```python
def test_top_critical_keeps_missing_vpr_after_scored_cves() -> None:
    dataset = build_cloud_dataset(snapshot_with_scored_and_missing_vpr())
    rows = dataset["top_critical_cves"]
    assert rows[-1]["vpr"] is None
    assert rows[-1]["vpr_display"] == "N/D"


def test_top_correctable_requires_correlated_remediation() -> None:
    dataset = build_cloud_dataset(snapshot_with_correlated_and_generic_remediation())
    assert [row["cve"] for row in dataset["top_correctable_vulnerabilities"]] == [
        "CVE-2099-1000"
    ]


def test_resolved_metrics_use_exclusive_period_end() -> None:
    dataset = build_cloud_dataset(lifecycle_on_period_edges())
    assert dataset["remediation_performance"]["resolved"] == 1


def test_historical_period_without_exact_snapshot_discloses_current_state() -> None:
    dataset = build_cloud_dataset(current_snapshot_for_historical_period())
    assert dataset["snapshot_context"]["historical_reconstruction"] == "CURRENT_STATE_ONLY"
    assert dataset["snapshot_context"]["warning"]
```

Cobrir Top 10 hosts/imagens, componentes, postura, aging, inventário, fontes indisponíveis, primeira fotografia e proveniência GraphQL.

- [x] **Step 2: Executar e confirmar falha**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cloud_report_dataset.py tests/test_source_filters.py -q
```

Expected: FAIL pela ausência do builder Cloud.

- [x] **Step 3: Implementar dataset sem lógica no renderer**

```python
CLOUD_DATASET_SCHEMA_VERSION = 1
CLOUD_METRIC_DEFINITION_VERSION = "cloud-metrics-v1"
```
O builder recebe `profile`, `run_id`, `ReportingPeriod`, `NormalizedCloudSnapshot`, enriquecimentos, histórico e `output_root`. Ele valida as versões, monta o dicionário canônico, grava o JSON de forma atômica e retorna `CloudReportDatasetArtifact` contendo diretório, caminho, hash e dataset imutável.

Top 5: VPR informado decrescente, ativos decrescente, CVSS decrescente, CVE crescente; valores ausentes ficam depois. Top 10 com correção usa a mesma ordenação e exige remediação correlacionada. Aging usa faixas `0-30`, `31-60`, `61-90`, `91-180`, `>180` e `data_indisponivel`. O JSON registra `table_provenance` para todas as tabelas. A proveniência curta diferencia tabelas de fotografia (`collected_at`) das métricas de ciclo de vida calculadas em `[início, fim)`; uma execução histórica sem snapshot exato recebe `CURRENT_STATE_ONLY` e aviso editorial explícito.

- [x] **Step 4: Executar testes**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cloud_report_dataset.py tests/test_source_filters.py -q
```

Expected: PASS.

- [x] **Step 5: Commit**

```powershell
git add src/tenable_reports/application/cloud_report_dataset.py src/tenable_reports/presentation/source_filters.py tests/test_cloud_report_dataset.py tests/test_source_filters.py
git commit -m "feat: build auditable cloud report dataset"
```

---

### Task 8: Snapshot Cloud compacto e PostgreSQL

**Files:**
- Create: `src/tenable_reports/application/cloud_snapshots.py`
- Create: `src/tenable_reports/infrastructure/cloud_snapshots_postgresql.py`
- Create: `src/tenable_reports/infrastructure/postgresql_migrations/0007_cloud_reports.sql`
- Create: `tests/test_cloud_snapshots.py`
- Create: `tests/test_cloud_snapshots_postgresql.py`
- Modify: `tests/test_postgresql.py`

**Interfaces:**
- Produces: `CloudReportSnapshot`, `CloudSnapshotRepository`, `MemoryCloudSnapshotRepository`, `PostgresCloudSnapshotRepository`.
- Repository methods: `publish`, `find_exact`, `latest_compatible_since`, `list_main_before`, `save_contract_check`, `latest_contract_check`, `invalidate_contract_checks`.
- Consumes later: replay exato e série histórica do dataset.

- [x] **Step 1: Escrever testes de imutabilidade, replay e seleção MAIN**

```python
def test_compact_cloud_snapshot_round_trip_preserves_dataset() -> None:
    snapshot = build_cloud_snapshot(dataset=cloud_dataset(), **snapshot_identity())
    replay = replay_cloud_snapshot(snapshot)
    assert replay.dataset == cloud_dataset()
    assert snapshot.content_sha256 == replay.content_sha256


def test_history_returns_only_compatible_main_runs() -> None:
    repository = MemoryCloudSnapshotRepository()
    publish_cloud_runs(repository, main=("run-jun", "run-jul"), non_main=("run-test",))
    rows = repository.list_main_before(cloud_compatibility("2026-08-01T03:00:00Z"))
    assert [row.run_id for row in rows] == ["run-jun", "run-jul"]
```

- [x] **Step 2: Executar e confirmar falha**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cloud_snapshots.py tests/test_cloud_snapshots_postgresql.py tests/test_postgresql.py -q
```

Expected: FAIL pela ausência do snapshot e da migration `0007`.

- [x] **Step 3: Implementar snapshot e migration aditiva**

```python
@dataclass(frozen=True, slots=True)
class CloudReportSnapshot:
    snapshot_id: str
    schema_version: int
    connector_version: str
    normalizer_version: str
    client_id: str
    tenant_id: str
    run_id: str
    attempt_number: int
    execution_type: str
    period_mode: str
    timezone: str
    period_start_at: str
    period_end_at: str
    scope_hash: str
    metric_definition_version: str
    collected_at: str
    content_sha256: str
    payload_gzip: bytes
    capabilities: Mapping[str, Any]
    record_counts: Mapping[str, int]
```

A migration cria `cloud_report_snapshots` com FK `run_id` para `report_runs`, índices por cliente/tenant/período e unicidade por run. Cria `cloud_contract_checks` sem segredo. Também amplia `published_documents_kind_check` para `cloud` e adiciona `document_variant` restrito a `base` ou `expanded` em documentos Cloud. O cache de contrato é indexado por cliente, ambiente, versão do conector e revisão não sensível do arquivo de credencial; mudança do token pela interface invalida o registro, sem persistir hash ou conteúdo do segredo.

- [x] **Step 4: Executar testes**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cloud_snapshots.py tests/test_cloud_snapshots_postgresql.py tests/test_postgresql.py -q
```

Expected: PASS sem exigir PostgreSQL real nos testes unitários; integração real permanece marcada e opt-in.

- [x] **Step 5: Commit**

```powershell
git add src/tenable_reports/application/cloud_snapshots.py src/tenable_reports/infrastructure/cloud_snapshots_postgresql.py src/tenable_reports/infrastructure/postgresql_migrations/0007_cloud_reports.sql tests/test_cloud_snapshots.py tests/test_cloud_snapshots_postgresql.py tests/test_postgresql.py
git commit -m "feat: persist compact cloud snapshots"
```

---

### Task 9: Template sanitizado e Modelo Base

**Files:**
- Create: `scripts/distill_cloud_template.py`
- Create: `src/tenable_reports/presentation/cloud_editorial_catalog.py`
- Create: `src/tenable_reports/presentation/cloud_report_sections.py`
- Create: `src/tenable_reports/presentation/cloud_report_docx.py`
- Create: `templates/corporate/cloud-base-v1.docx`
- Create: `tests/test_cloud_report_docx.py`
- Modify: `src/tenable_reports/presentation/__init__.py`

**Interfaces:**
- Produces: `CloudReportVariant(BASE, EXPANDED)`.
- Produces: `generate_cloud_report(template_path, dataset_path, profile, output_path, variant) -> CloudReportRenderResult`.
- Consumes: dataset pronto; renderer não recalcula ranking ou métricas.

- [x] **Step 1: Escrever testes estruturais do Modelo Base**

```python
def test_base_cloud_report_keeps_approved_sections_and_detailed_top_five(tmp_path: Path) -> None:
    output = tmp_path / "cloud-base.docx"
    result = generate_cloud_report(
        template_path=CLOUD_TEMPLATE,
        dataset_path=cloud_dataset_fixture(tmp_path),
        profile=cloud_profile(layout="base"),
        output_path=output,
        variant=CloudReportVariant.BASE,
    )
    text = docx_text(output)
    assert "Principais Vulnerabilidades Críticas" in text
    assert "Principais Vulnerabilidades com Correção Disponível" in text
    assert "CVE-2099-1000" in text
    assert result.variant is CloudReportVariant.BASE
    assert tuple(result.rendered_sections) == (
        "cover", "table_of_contents", "document_control", "objective",
        "cloud_overview", "introduction", "top_hosts", "top_images",
        "top_critical", "critical_details", "top_correctable",
        "dashboard", "conclusion", "back_cover",
    )
    assert "Tipo de correção" in text
    assert "Ativos afetados" in text
    for paragraph in approved_cloud_editorial_paragraphs():
        assert paragraph in text


def test_empty_cloud_table_has_monthly_message_not_blank_page(tmp_path: Path) -> None:
    output = render_empty_cloud_base(tmp_path)
    assert "Neste mês não foram identificadas" in docx_text(output)
```

- [x] **Step 2: Executar e confirmar falha**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cloud_report_docx.py -q
```

Expected: FAIL porque template e renderer ainda não existem.

- [x] **Step 3: Implementar destilação e renderização Base**

```python
class CloudReportVariant(StrEnum):
    BASE = "base"
    EXPANDED = "expanded"

@dataclass(frozen=True, slots=True)
class CloudReportRenderResult:
    output_path: Path
    client_id: str
    period_id: str
    variant: CloudReportVariant
    rendered_sections: Sequence[str]
    omitted_sections: Sequence[str]
```

`distill_cloud_template.py` recebe `--source`, `--output` e `--forbidden-term-file`; remove propriedades, campos e textos sensíveis, preserva capa, logos, cabeçalhos, rodapés, seções e identidade visual. O catálogo editorial contém somente os parágrafos exatos aprovados e sanitizados. A descrição traduzida usa `translate_in_chunks`; tradução vazia preserva o original com aviso. O renderer percorre a sequência fixa testada de 14 IDs editoriais e cria até cinco detalhes críticos; quando não houver população, mantém somente a mensagem mensal aprovada, sem título órfão ou página vazia.

- [x] **Step 4: Gerar o template sanitizado fora de evidências reais e executar testes**

Run:

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
.\.venv\Scripts\python.exe scripts\distill_cloud_template.py --source $env:TENABLE_CLOUD_TEMPLATE_SOURCE --output templates\corporate\cloud-base-v1.docx
.\.venv\Scripts\python.exe -m pytest tests/test_cloud_report_docx.py tests/test_translation.py -q
.\.venv\Scripts\python.exe tools\audit_secret_leaks.py
```

Expected: template válido, testes PASS e nenhuma evidência sensível detectada.

- [x] **Step 5: Commit**

```powershell
git add scripts/distill_cloud_template.py src/tenable_reports/presentation templates/corporate/cloud-base-v1.docx tests/test_cloud_report_docx.py
git commit -m "feat: render base cloud security report"
```

---

### Task 10: Modelo Ampliado, gráficos e módulos condicionais

**Files:**
- Create: `src/tenable_reports/presentation/cloud_visuals.py`
- Modify: `src/tenable_reports/presentation/cloud_report_sections.py`
- Modify: `src/tenable_reports/presentation/cloud_report_docx.py`
- Modify: `tests/test_cloud_report_docx.py`

**Interfaces:**
- Consumes: `executive_summary`, `exposure_by_asset_type`, `components_at_risk`, `cloud_posture`, `aging`, `remediation_performance`, `inventory_coverage`, `monthly_evolution`, `conditional_modules`.
- Produces: gráficos PNG efêmeros e seções nativas do Word.
- Preserves: Modelo Base sem as seções ampliadas.

- [x] **Step 1: Escrever testes de diferença editorial e igualdade numérica**

```python
def test_expanded_adds_operational_sections_without_changing_shared_values(tmp_path: Path) -> None:
    base, expanded = render_both_variants(tmp_path)
    base_text, expanded_text = docx_text(base), docx_text(expanded)
    assert "Componentes e Produtos em Maior Risco" not in base_text
    assert "Componentes e Produtos em Maior Risco" in expanded_text
    assert extract_table_value(base, "CVE-2099-1000") == extract_table_value(
        expanded, "CVE-2099-1000"
    )


def test_unavailable_conditional_module_does_not_leave_heading_or_blank_page(tmp_path: Path) -> None:
    expanded = render_expanded_without_iam_capability(tmp_path)
    assert "IAM e Permissões Excessivas" not in docx_text(expanded)
```

Cobrir ausência de histórico, fontes parciais, gráfico com lacuna e largura das tabelas de imagens.

- [x] **Step 2: Executar e confirmar falha**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cloud_report_docx.py -q
```

Expected: FAIL porque as seções ampliadas ainda não são renderizadas.

- [x] **Step 3: Implementar seções e gráficos determinísticos**

`render_cloud_visuals(dataset, output_dir)` devolve um mapa estável `visual_id -> PNG`. `render_expanded_sections(document, dataset, visual_paths, show_source_filters)` devolve duas sequências: IDs efetivamente renderizados e IDs omitidos com motivo já registrado no manifesto.

Usar Pillow e a paleta corporativa existente. Gráficos não conectam meses ausentes como zero. Seção condicional só é chamada quando `capability.status == 'AVAILABLE'` e há população. O primeiro piloto pode registrar módulos como indisponíveis sem criar conteúdo vazio.

- [x] **Step 4: Executar testes**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cloud_report_docx.py tests/test_source_filters.py -q
```

Expected: PASS.

- [x] **Step 5: Commit**

```powershell
git add src/tenable_reports/presentation/cloud_visuals.py src/tenable_reports/presentation/cloud_report_sections.py src/tenable_reports/presentation/cloud_report_docx.py tests/test_cloud_report_docx.py
git commit -m "feat: render expanded cloud report"
```

---

### Task 11: Nomes, manifesto e documentos Cloud tipados

**Files:**
- Modify: `src/tenable_reports/presentation/report_filenames.py:1-90`
- Modify: `src/tenable_reports/application/publishing.py:1-180`
- Modify: `src/tenable_reports/infrastructure/postgresql.py:574-700`
- Modify: `tests/test_report_filenames.py`
- Modify: `tests/test_orchestration.py`
- Modify: `tests/test_postgresql.py`

**Interfaces:**
- Produces: `cloud_report_filename(display_name, period, variant)`.
- Extends: `PublicationDocument(document_kind='cloud', document_variant='base|expanded')`.
- Extends: `create_publication_manifest` com o argumento nomeado `additional_datasets: Mapping[str, Path]`.

- [x] **Step 1: Escrever testes dos nomes e metadados**

```python
def test_cloud_prototype_filenames_are_distinct_and_windows_safe() -> None:
    assert cloud_report_filename("CLIENTE", JULY, "base") == (
        "[CLIENTE] Relatório Tenable Cloud Security JUL26 - MODELO BASE.docx"
    )
    assert cloud_report_filename("CLIENTE", JULY, "expanded") == (
        "[CLIENTE] Relatório Tenable Cloud Security JUL26 - MODELO AMPLIADO.docx"
    )


def test_manifest_records_cloud_variant_and_common_dataset(tmp_path: Path) -> None:
    payload = create_manifest_with_two_cloud_documents(tmp_path)
    cloud = [item for item in payload["documents"] if item["document_kind"] == "cloud"]
    assert {item["document_variant"] for item in cloud} == {"base", "expanded"}
    assert payload["source_datasets"]["cloud"]["sha256"]
```

- [x] **Step 2: Executar e confirmar falha**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_report_filenames.py tests/test_orchestration.py tests/test_postgresql.py -q
```

Expected: FAIL porque `cloud` e `document_variant` ainda são rejeitados.

- [x] **Step 3: Implementar metadados compatíveis**

```python
@dataclass(frozen=True, slots=True)
class PublicationDocument:
    path: str | Path
    document_kind: str
    document_variant: str | None = None
    tag_uuid: str | None = None
    tag_category: str | None = None
    tag_value: str | None = None
```

Manifestos antigos continuam legíveis. Para documento `cloud`, a variante é sempre obrigatória; `comparison` publica as duas variantes e `base` ou `expanded` publica somente a escolhida. Para `base`, `custom` e `tag`, variante é nula. `source_dataset` VM permanece por compatibilidade e `source_datasets.cloud` registra o dataset Cloud comum.

- [x] **Step 4: Executar testes**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_report_filenames.py tests/test_orchestration.py tests/test_postgresql.py -q
```

Expected: PASS.

- [x] **Step 5: Commit**

```powershell
git add src/tenable_reports/presentation/report_filenames.py src/tenable_reports/application/publishing.py src/tenable_reports/infrastructure/postgresql.py tests/test_report_filenames.py tests/test_orchestration.py tests/test_postgresql.py
git commit -m "feat: publish typed cloud report variants"
```

---

### Task 12: Caso de uso Cloud e integração no `run-client`

**Files:**
- Create: `src/tenable_reports/application/cloud_execution.py`
- Create: `tests/test_cloud_execution.py`
- Modify: `src/tenable_reports/cli.py:1-130,1038-1225,1406-1640,1880-2290`
- Modify: `src/tenable_reports/application/orchestration.py:557-645,684-844,980-1140`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_orchestration.py`

**Interfaces:**
- Produces: `CloudComponentResult(status, documents, dataset_path, snapshot_id, warnings, cleanup_ready)`.
- Statuses: `DISABLED`, `COMPLETE`, `REPLAYED`, `BLOCKED_RECENT_COLLECTION`, `FAILED`.
- Produces events: `TENABLE_CLOUD_PROGRESS`.
- Extends client payload: `cloud_status`, `cloud_documents`, `cloud_snapshot_id`, `cloud_warnings`.

- [x] **Step 1: Escrever testes de geração conjunta e falha isolada**

```python
def test_enabled_cloud_generates_two_variants_from_one_dataset(tmp_path: Path) -> None:
    calls = CloudExecutionCalls()
    result = execute_cloud_component(cloud_execution_request(tmp_path), services=calls)
    assert result.status == "COMPLETE"
    assert {item.document_variant for item in result.documents} == {"base", "expanded"}
    assert calls.collection_count == 1
    assert calls.dataset_count == 1


def test_recent_non_exact_snapshot_requires_explicit_refresh(tmp_path: Path) -> None:
    calls = CloudExecutionCalls(recent_snapshot=recent_compatible_snapshot())
    result = execute_cloud_component(
        cloud_execution_request(tmp_path, force_cloud_refresh=False),
        services=calls,
    )
    assert result.status == "BLOCKED_RECENT_COLLECTION"
    assert calls.collection_count == 0


def test_cloud_failure_keeps_general_documents_and_returns_warning(tmp_path: Path) -> None:
    payload = run_client_with_cloud_failure(tmp_path)
    assert payload["status"] == "complete_with_warnings"
    assert Path(payload["base_document"]).is_file()
    assert payload["cloud_status"] == "FAILED"
```

Cobrir Cloud desabilitado sem chamadas, replay exato, progresso sem segredo e limpeza bloqueada quando Cloud falha com staging reaproveitável.

- [x] **Step 2: Executar e confirmar falha**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cloud_execution.py tests/test_cli.py tests/test_orchestration.py -q
```

Expected: FAIL porque o componente não está ligado ao `run-client`.

- [x] **Step 3: Implementar o caso de uso e integrar sem alterar o dataset VM**

```python
@dataclass(frozen=True, slots=True)
class CloudComponentResult:
    status: str
    documents: Sequence[PublicationDocument] = ()
    dataset_path: Path | None = None
    snapshot_id: str | None = None
    warnings: Sequence[Mapping[str, Any]] = ()
    cleanup_ready: bool = False
```

Fluxo: tentar replay exato; consultar a última coleta compatível; bloquear uma segunda coleta completa dentro de 24 horas, salvo `force_cloud_refresh=true` em execução manual confirmada; caso contrário testar/recuperar contrato, coletar, normalizar, enriquecer, carregar históricos `MAIN`, gerar dataset, renderizar as variantes configuradas, validar DOCX, publicar snapshot e devolver documentos. Automação nunca força refresh. `command_run_client` captura `OperationalFailure` Cloud, mantém os documentos gerais, publica os válidos e retorna código `0` com `complete_with_warnings`. A orquestração traduz esse payload para `COMPLETE_WITH_WARNINGS`, não `FAILED`.

- [x] **Step 4: Executar testes**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cloud_execution.py tests/test_cli.py tests/test_orchestration.py -q
```

Expected: PASS.

- [x] **Step 5: Commit**

```powershell
git add src/tenable_reports/application/cloud_execution.py src/tenable_reports/cli.py src/tenable_reports/application/orchestration.py tests/test_cloud_execution.py tests/test_cli.py tests/test_orchestration.py
git commit -m "feat: generate cloud reports with client runs"
```

---

### Task 13: Retentativa exclusiva do componente Cloud

**Files:**
- Modify: `src/tenable_reports/application/cloud_execution.py`
- Modify: `src/tenable_reports/application/publishing.py`
- Modify: `src/tenable_reports/infrastructure/postgresql.py`
- Modify: `src/tenable_reports/cli.py`
- Modify: `tests/test_cloud_execution.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_postgresql.py`

**Interfaces:**
- Produces CLI: `tenable-reports retry-cloud --run-id <id> --profile <path> --env-file <path> --database-env-file <path> --confirm-live-api`.
- Produces: `PostgresOperationsRepository.report_run_context(run_id)`.
- Produces: `upsert_publication_documents(manifest_path, documents, additional_datasets)`.

- [x] **Step 1: Escrever teste que prova que a retentativa não chama VM/WAS/TAG**

```python
def test_retry_cloud_reuses_run_context_without_general_collection(tmp_path: Path) -> None:
    services = RetryServices(vm_forbidden=True, was_forbidden=True, tags_forbidden=True)
    result = retry_cloud_component(run_id="run-a", services=services)
    assert result.status == "COMPLETE"
    assert services.cloud_calls == 1
    assert services.vm_calls == services.was_calls == services.tag_calls == 0


def test_retry_cloud_collects_only_missing_or_invalid_sources(tmp_path: Path) -> None:
    services = RetryServices(
        completed_sources=("virtual_machines", "container_images"),
        missing_sources=("findings",),
    )
    retry_cloud_component(run_id="run-a", services=services)
    assert services.requested_cloud_sources == ["findings"]
```

Cobrir substituição idempotente dos documentos Cloud no manifesto e preservação dos documentos gerais.

- [x] **Step 2: Executar e confirmar falha**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cloud_execution.py tests/test_cli.py tests/test_postgresql.py -q
```

Expected: FAIL porque contexto e subcomando não existem.

- [x] **Step 3: Implementar retentativa por run**

```python
@dataclass(frozen=True, slots=True)
class ReportRunContext:
    run_id: str
    client_id: str
    tenant_id: str
    execution_type: str
    period_start_at: str
    period_end_at: str
    period_mode: str
    timezone: str
    publication_manifest: Path
```

O comando exige run existente, não excluído, cliente compatível e confirmação de API. Ele valida hash e versão de cada checkpoint, reutiliza respostas e snapshot íntegros e chama externamente somente fontes ausentes ou inválidas; atualiza somente documentos `cloud` e persiste o resultado na mesma execução.

- [x] **Step 4: Executar testes**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cloud_execution.py tests/test_cli.py tests/test_postgresql.py -q
```

Expected: PASS.

- [x] **Step 5: Commit**

```powershell
git add src/tenable_reports/application/cloud_execution.py src/tenable_reports/application/publishing.py src/tenable_reports/infrastructure/postgresql.py src/tenable_reports/cli.py tests/test_cloud_execution.py tests/test_cli.py tests/test_postgresql.py
git commit -m "feat: retry cloud component independently"
```

---

### Task 14: Interface web, token, teste e progresso Cloud

**Files:**
- Modify: `src/tenable_reports/webapp/server.py:60-275,320-670,980-1280,1660-2010`
- Modify: `src/tenable_reports/webapp/static/index.html:80-150`
- Modify: `src/tenable_reports/webapp/static/app.js`
- Modify: `src/tenable_reports/webapp/static/app.css`
- Modify: `tests/test_webapp.py`
- Modify: `clients/examples/orchestration/client-a.json`
- Modify: `orchestration/clients.example.json`

**Interfaces:**
- Adds client fields: `cloud_enabled`, `cloud_environment`, `cloud_layout`, `cloud_credentials_ready`.
- Adds route: `POST /api/clients/{client_id}/cloud-test`.
- Adds route: `POST /api/reports/{run_id}/retry-cloud`.
- Extends global API check: enabled Cloud clients run their Cloud test independently.

- [x] **Step 1: Escrever testes de segurança e rotas**

```python
def test_client_edit_preserves_saved_cloud_token_when_field_is_blank(tmp_path: Path) -> None:
    store = configured_store(tmp_path, cloud_token="saved-secret")
    store.update_client("client-a", {"cloud_api_secret": ""})
    values = read_env(store.client_env_path("client-a"))
    assert values["TCS_API_SECRET"] == "saved-secret"
    assert "saved-secret" not in json.dumps(store.list_clients())


def test_cloud_test_route_returns_status_without_secret(webapp) -> None:
    response = webapp.post("/api/clients/client-a/cloud-test", {})
    assert response.status == 200
    assert response.json["ok"] is True
    assert "secret" not in json.dumps(response.json).lower()


def test_enabling_cloud_does_not_add_legacy_custom_module(configured_store) -> None:
    configured_store.update_client("client-a", {"cloud_enabled": True})
    profile = configured_store.load_profile("client-a")
    assert "cloud_container_images" not in profile["report"]["intelligence_modules"]
```

Cobrir cliente Cloud habilitado sem token, alteração de ambiente invalidando capability cache, retentativa com confirmação e progresso `TENABLE_CLOUD_PROGRESS`.

- [x] **Step 2: Executar e confirmar falha**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_webapp.py -q
```

Expected: FAIL porque token/status/rotas ainda não existem.

- [x] **Step 3: Implementar escrita de `.env` por merge e UI não técnica**

```python
def _merge_env_values(path: Path, replacements: Mapping[str, str | None]) -> None:
    values = _read_env_values(path) if path.is_file() else {}
    for key, value in replacements.items():
        if value is not None:
            values[key] = value
    _write_env_values_atomic(path, values)
```

`cloud_api_secret=""` vira `None` no merge e preserva o valor. A tela exibe `Token Cloud salvo` sem revelar conteúdo, ambiente, modo de protótipo e botão `Testar API Cloud`. O card mostra progresso Cloud separado e botão `Tentar Cloud novamente` somente após falha retentável. Salvar um token novo invalida o cache de contrato. Uma tentativa bloqueada pela janela de 24 horas mostra a fotografia recente e só envia `force_cloud_refresh=true` depois de confirmação explícita do analista.

- [x] **Step 4: Executar testes**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_webapp.py tests/test_profile_environment.py -q
```

Expected: PASS.

- [x] **Step 5: Commit**

```powershell
git add src/tenable_reports/webapp clients/examples/orchestration/client-a.json orchestration/clients.example.json tests/test_webapp.py tests/test_profile_environment.py
git commit -m "feat: manage cloud reports in web ui"
```

---

### Task 15: Retenção, exclusão de sets e reciclagem Cloud

**Files:**
- Modify: `src/tenable_reports/application/retention.py`
- Modify: `src/tenable_reports/application/report_set_purge.py`
- Modify: `src/tenable_reports/infrastructure/report_set_purge_postgresql.py`
- Modify: `tests/test_retention.py`
- Modify: `tests/test_report_set_purge.py`
- Modify: `tests/test_report_set_purge_postgresql.py`
- Modify: `tests/test_cloud_execution.py`

**Interfaces:**
- Consumes: `CloudComponentResult.cleanup_ready`.
- Preserves: exclusão física restrita à raiz `data` e transação PostgreSQL vigente.
- Produces: remoção em cascata de snapshots/capabilities ligados ao set excluído.

- [x] **Step 1: Escrever testes de proteção e remoção**

```python
def test_failed_cloud_staging_is_not_cleaned_before_retry_window(tmp_path: Path) -> None:
    plan = plan_published_run_cleanup(
        scoped_output_root=tmp_path,
        client_id="client-a",
        run_id="run-a",
        publication_confirmed=True,
        history_confirmed=True,
        compact_snapshot_confirmed=True,
        cloud_cleanup_ready=False,
    )
    assert all("tenable_cloud" not in str(path) for path in plan.candidates)


def test_hard_delete_removes_cloud_documents_and_snapshot_in_one_set(tmp_path: Path) -> None:
    result = purge_cloud_report_set(tmp_path)
    assert result.deleted_document_count == 4
    assert result.deleted_cloud_snapshot_count == 1
```

- [x] **Step 2: Executar e confirmar falha**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_retention.py tests/test_report_set_purge.py tests/test_report_set_purge_postgresql.py tests/test_cloud_execution.py -q
```

Expected: FAIL porque a limpeza não conhece o estado Cloud.

- [x] **Step 3: Implementar proteção e deleção transacional**

Adicionar `cloud_cleanup_ready` ao preflight de limpeza. Em hard delete, remover registros Cloud pelo `run_id` dentro da mesma transação antes do `report_runs`; arquivos são movidos para estágio recuperável e só então a transação é finalizada, preservando o rollback vigente.

- [x] **Step 4: Executar testes**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_retention.py tests/test_report_set_purge.py tests/test_report_set_purge_postgresql.py tests/test_cloud_execution.py -q
```

Expected: PASS.

- [x] **Step 5: Commit**

```powershell
git add src/tenable_reports/application/retention.py src/tenable_reports/application/report_set_purge.py src/tenable_reports/infrastructure/report_set_purge_postgresql.py tests/test_retention.py tests/test_report_set_purge.py tests/test_report_set_purge_postgresql.py tests/test_cloud_execution.py
git commit -m "feat: recycle cloud report artifacts safely"
```

---

### Task 16: Prova sanitizada, documentação e verificação final

**Files:**
- Create: `scripts/render_cloud_report_fixture.py`
- Modify: `README.md`
- Modify: `docs/19-visao-geral-e-objetivos.md`
- Modify: `docs/20-arquitetura-e-fluxo-de-dados.md`
- Modify: `docs/21-catalogo-de-dados-e-metricas.md`
- Modify: `docs/22-guia-operacional.md`
- Modify: `docs/23-guia-de-desenvolvimento.md`
- Modify: `templates/corporate/README.md`
- Modify: `tests/test_project_guidance.py`

**Interfaces:**
- Produces fixture documents: `cloud-modelo-base.docx`, `cloud-modelo-ampliado.docx` e `cloud-prototype-manifest.json`.
- Documents commands: configurar token, testar contrato, gerar junto, retentar Cloud e escolher variante final.

- [x] **Step 1: Escrever teste do script de prova e dos guias**

```python
def test_cloud_fixture_renderer_builds_two_documents_from_one_hash(tmp_path: Path) -> None:
    manifest = render_cloud_fixture(tmp_path)
    assert {item["variant"] for item in manifest["documents"]} == {"base", "expanded"}
    assert len({item["dataset_sha256"] for item in manifest["documents"]}) == 1
```

- [x] **Step 2: Executar e confirmar falha**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_project_guidance.py tests/test_cloud_report_docx.py -q
```

Expected: FAIL até o script e os guias vigentes serem atualizados.

- [x] **Step 3: Implementar prova e atualizar a documentação vigente**

O manifesto lista variante, páginas, seções, omissões, hash do dataset, hash do DOCX e alertas de cobertura. Os guias distinguem fotografia atual de período, documentam o legado `RelatorioCloudTenable` como fonte técnica e registram que módulos condicionais dependem do contrato do tenant.

- [x] **Step 4: Gerar os dois DOCX sanitizados**

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
.\.venv\Scripts\python.exe scripts\render_cloud_report_fixture.py --output-root artifacts\cloud-prototype
```

Expected: dois DOCX e um manifesto, sem dados reais.

- [x] **Step 5: Renderizar com LibreOffice e inspecionar todas as páginas**

```powershell
.\.venv\Scripts\python.exe scripts\render_cloud_report_fixture.py --output-root artifacts\cloud-prototype --qa
```

Expected: nenhuma tabela cortada, página intermediária vazia, texto fora da margem ou dado sensível. Registrar a inspeção no manifesto local fora do Git.

- [x] **Step 6: Executar a verificação completa**

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe tools\validate_project_guidance.py --root .
.\.venv\Scripts\python.exe tools\audit_secret_leaks.py
git diff --check
git status --short --branch
```

Expected: suíte completa PASS, guias válidos, nenhum secret e somente mudanças da feature na branch.

- [x] **Step 7: Commit**

```powershell
git add scripts/render_cloud_report_fixture.py README.md docs templates/corporate/README.md tests/test_project_guidance.py
git commit -m "docs: document cloud report workflow"
```

---

## Piloto real após todos os testes

O piloto não faz parte da suíte automatizada. Só executá-lo com autorização explícita e cliente selecionado:

1. cadastrar `TCS_API_SECRET` pela interface;
2. executar `Testar API Cloud`, que realiza somente o probe mínimo;
3. revisar endpoint, fontes obrigatórias e módulos opcionais detectados;
4. iniciar uma execução manual de um único cliente;
5. acompanhar o progresso Cloud e os demais produtos;
6. conferir que uma única fotografia gerou os dois modelos;
7. reconciliar amostras de hosts, imagens, Top 5, Top 10 com correção, aging e inventário;
8. apresentar os dois DOCX para decisão editorial;
9. alterar `scope.cloud_security.layout` de `comparison` para `base` ou `expanded` após a escolha;
10. executar novamente a suíte antes do fluxo final de Git.

## Final Git Flow

Depois do aceite do piloto:

```powershell
git status --short --branch
git log --oneline --decorate -12
git diff main HEAD --check
git switch main
git merge --no-ff codex/relatorio-cloud-security
git push origin main
git branch -d codex/relatorio-cloud-security
```

Antes do merge, confirmar que não há segunda branch `codex/*` ativa ou worktree paralelo desta entrega. A remoção da branch ocorre somente depois do push bem-sucedido da `main`.

