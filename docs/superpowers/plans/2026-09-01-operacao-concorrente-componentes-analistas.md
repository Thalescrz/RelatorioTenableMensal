# Operação concorrente, componentes e analistas — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permitir seleção de clientes e responsáveis, retentativas isoladas de VM/WAS/Cloud e coleta remota concorrente com montagem local serial e retomável.

**Architecture:** A entrega ocorre em três incrementos sobre a fila PostgreSQL existente. Configuração de analistas permanece em JSON atômico; tentativas por componente recebem domínio e persistência próprios; novos lotes `STAGED_V1` separam coleta remota e montagem local por checkpoints sanitizados. Compatibilidade `LEGACY` e `run-client` é preservada.

**Tech Stack:** Python 3.14, pytest, PostgreSQL/psycopg, HTTPServer local, JavaScript/CSS/HTML sem framework, DOCX/LibreOffice nos gates visuais.

**Spec:** `docs/superpowers/specs/2026-09-01-coleta-concorrente-renderizacao-serial-design.md`

## Global Constraints

- Trabalhar somente em `codex/coleta-concorrente-renderizacao`; não criar outro worktree ou branch.
- TDD obrigatório: cada comportamento novo precisa falhar pelo motivo esperado antes do código de produção.
- Nenhum teste acessa Tenable, PostgreSQL real, servidor real ou credencial.
- Períodos continuam `[início, fim)` no fuso do cliente.
- TAG não filtra o relatório geral; WAS permanece opcional; Cloud não depende de VM.
- `FINISHED` e chunks tratados continuam obrigatórios para concluir export VM/WAS.
- Um job fornecido/reutilizado/retomado nunca é cancelado automaticamente.
- Nenhum texto, cálculo, tabela ou ordem editorial dos DOCX muda nesta entrega.
- Component retry preserva documentos válidos e a identidade do conjunto/`MAIN`.
- Commits abaixo são locais; merge/push só ocorrem no fluxo Git final aprovado.

---

## Incremento 1 — Analistas e seleção de clientes

### Task 1: Catálogo atômico de analistas

**Files:**
- Create: `src/tenable_reports/config/analysts.py`
- Test: `tests/test_analyst_catalog.py`

**Interfaces:**
- Produces: `AnalystRecord`, `AnalystCatalog.list()`, `create()`, `update()`, `deactivate()`, `delete()` e `get()`.
- Consumes: somente `pathlib`, `json`, `uuid`, relógio injetável e escrita atômica local.

- [ ] **Step 1: Escrever testes RED do contrato público**

```python
def test_catalog_creates_stable_unique_analyst_and_reloads(tmp_path):
    catalog = AnalystCatalog(tmp_path / "analysts.json")
    created = catalog.create(display_name="Analista Um")
    assert created.analyst_id
    assert AnalystCatalog(tmp_path / "analysts.json").get(created.analyst_id) == created

def test_catalog_rejects_case_insensitive_duplicate(tmp_path):
    catalog = AnalystCatalog(tmp_path / "analysts.json")
    catalog.create(display_name="Analista Um")
    with pytest.raises(ValueError, match="já existe"):
        catalog.create(display_name="analista um")
```

Adicionar casos de atualização, desativação, arquivo inválido e recusa de exclusão quando `is_in_use(analyst_id)` retornar verdadeiro.

- [ ] **Step 2: Confirmar RED**

Run: `python -m pytest tests/test_analyst_catalog.py -q`

Expected: FAIL por módulo/API inexistente, não por erro de fixture.

- [ ] **Step 3: Implementar o mínimo**

Implementar `AnalystRecord` como dataclass imutável com os campos
`analyst_id`, `display_name`, `active`, `created_at` e `updated_at`. O contrato
público de `AnalystCatalog` é:

- `__init__(path: Path, *, now: Callable[[], datetime] = utc_now) -> None`;
- `list() -> Sequence[AnalystRecord]`, ordenado por nome e ID;
- `get(analyst_id: str) -> AnalystRecord | None`;
- `create(*, display_name: str) -> AnalystRecord`;
- `update(analyst_id: str, *, display_name: str, active: bool) -> AnalystRecord`;
- `deactivate(analyst_id: str) -> AnalystRecord`;
- `delete(analyst_id: str, *, is_in_use: Callable[[str], bool]) -> None`.

Usar `os.replace()` sobre arquivo temporário irmão e normalizar unicidade com `casefold()`.
A integração web deve instanciar este catálogo no caminho canônico
`orchestration/analysts.json`; testes posteriores não podem substituir esse
contrato por armazenamento em perfil ou credenciais.

- [ ] **Step 4: Confirmar GREEN e mutações**

Run: `python -m pytest tests/test_analyst_catalog.py -q`

Mental mutation: remover `casefold`, gravar diretamente no destino ou aceitar nome vazio precisa quebrar ao menos um teste.

- [ ] **Step 5: Commit**

```powershell
git add src/tenable_reports/config/analysts.py tests/test_analyst_catalog.py
git commit -m "feat: adicionar catálogo local de analistas"
```

### Task 2: Responsável no perfil e APIs de administração

**Files:**
- Modify: `src/tenable_reports/config/profile.py`
- Modify: `src/tenable_reports/webapp/server.py`
- Modify: `tests/test_profile_environment.py`
- Modify: `tests/test_webapp.py`

**Interfaces:**
- Consumes: `AnalystCatalog` da Task 1.
- Produces: `ClientProfile.responsible_analyst_id`, estado `analysts`, CRUD `/api/analysts` e vínculo validado em `/api/clients`.

- [ ] **Step 1: Escrever testes RED de perfil e HTTP**

```python
def test_profile_accepts_optional_responsible_analyst_id():
    profile = ClientProfile.from_dict({**minimum_profile(), "responsible_analyst_id": "ana-1"})
    assert profile.responsible_analyst_id == "ana-1"

def test_client_update_rejects_unknown_responsible_analyst(dashboard):
    response = dashboard.post_json("/api/clients/cliente-a", {
        "responsible_analyst_id": "desconhecido",
    })
    assert response.status == 400
```

Cobrir list/create/update/deactivate/delete-em-uso e migração de cliente existente para `null`.

- [ ] **Step 2: Confirmar RED**

Run: `python -m pytest tests/test_profile_environment.py tests/test_webapp.py -q -k "analyst or responsible"`

Expected: FAIL porque campo e rotas ainda não existem.

- [ ] **Step 3: Implementar o mínimo**

Adicionar ao perfil:

```python
responsible_analyst_id: str | None = None
```

Instanciar o catálogo em `DashboardConfigStore`, incluir nome/responsável na visão sanitizada dos clientes e registrar rotas:

```text
GET    /api/analysts
POST   /api/analysts
PATCH  /api/analysts/<analyst_id>
DELETE /api/analysts/<analyst_id>
```

Validar identificador ativo para novas atribuições; preservar associação a analista inativo já existente.

- [ ] **Step 4: Confirmar GREEN**

Run: `python -m pytest tests/test_profile_environment.py tests/test_webapp.py -q -k "analyst or responsible"`

- [ ] **Step 5: Commit**

```powershell
git add src/tenable_reports/config/profile.py src/tenable_reports/webapp/server.py tests/test_profile_environment.py tests/test_webapp.py
git commit -m "feat: vincular analista responsável ao cliente"
```

### Task 3: Seleção explícita e auditoria do Gerar todos

**Files:**
- Modify: `src/tenable_reports/application/web_batches.py`
- Modify: `src/tenable_reports/webapp/server.py`
- Modify: `tests/test_web_batches.py`
- Modify: `tests/test_webapp.py`

**Interfaces:**
- Produces: opções sanitizadas `selected_client_ids`, `excluded_client_ids`, `analyst_snapshot_by_client` e `selection_filter_snapshot`.
- Consumes: lista explícita enviada pela interface; nunca recalcula a seleção depois da criação.

- [ ] **Step 1: Escrever testes RED**

```python
def test_create_manual_all_batch_persists_exact_selection_and_exclusions(app):
    request = manual_request(
        run_scope="all",
        selection_filter_snapshot={"analyst_id": "ana-1", "query": ""},
    )
    result = app.enqueue_jobs(["a", "c"], request)
    batch = app.batch_state(result["batch_id"])["batch"]
    assert batch.options["selected_client_ids"] == ["a", "c"]
    assert batch.options["excluded_client_ids"] == ["b"]
    assert batch.options["selection_filter_snapshot"] == {"analyst_id": "ana-1", "query": ""}
```

Adicionar testes separados que enviam `[]` e `['desconhecido']`, esperando
`ValueError` com códigos `EMPTY_CLIENT_SELECTION` e `UNKNOWN_CLIENT_SELECTION`.
Provar que mudança posterior no cadastro não altera `batch.options`.

- [ ] **Step 2: Confirmar RED**

Run: `python -m pytest tests/test_web_batches.py tests/test_webapp.py -q -k "selection or excluded_client"`

- [ ] **Step 3: Implementar validação e fotografia**

Normalizar IDs, rejeitar duplicatas/desconhecidos/inativos, calcular exclusões
somente no servidor e persistir, em `batch.options`, `selected_client_ids`,
`excluded_client_ids`, `analyst_snapshot_by_client` e o
`selection_filter_snapshot` recebido na solicitação. A fotografia é imutável após
a criação do lote.

- [ ] **Step 4: Confirmar GREEN e regressão de conflitos**

Run: `python -m pytest tests/test_web_batches.py tests/test_webapp.py -q`

- [ ] **Step 5: Commit**

```powershell
git add src/tenable_reports/application/web_batches.py src/tenable_reports/webapp/server.py tests/test_web_batches.py tests/test_webapp.py
git commit -m "feat: persistir seleção explícita do lote"
```

### Task 4: Modal, filtros e administração de analistas

**Files:**
- Modify: `src/tenable_reports/webapp/static/index.html`
- Modify: `src/tenable_reports/webapp/static/app.js`
- Modify: `src/tenable_reports/webapp/static/app.css`
- Modify: `tests/test_web_batch_ui.py`
- Modify: `tests/test_webapp.py`

**Interfaces:**
- Consumes: `state.clients`, `state.analysts` e `/api/jobs` da Task 2/3.
- Produces: filtro do painel, modal de seleção e botão `Gerar N clientes`.

- [ ] **Step 1: Escrever testes RED de comportamento observável**

Testar funções puras exportáveis quando possível:

```javascript
filterClients(clients, { query: "trt", analystId: "ana-1" })
selectionForVisibleClients(currentSelection, visibleIds, false)
```

No teste HTTP/HTML, confirmar controles, rótulos e payload explícito; não testar apenas texto-fonte sem fluxo.

- [ ] **Step 2: Confirmar RED**

Run: `python -m pytest tests/test_web_batch_ui.py tests/test_webapp.py -q -k "analyst or run_all_selection"`

- [ ] **Step 3: Implementar UI mínima**

`Gerar todos` passa a abrir modal com todos elegíveis marcados. Busca e filtro de analista combinam; marcar/desmarcar atua somente nos visíveis; seleção vazia desabilita confirmação. O dashboard oferece `Todos`, cada analista e `Sem responsável`.

- [ ] **Step 4: Testar e validar JavaScript**

Run:

```powershell
python -m pytest tests/test_web_batch_ui.py tests/test_webapp.py -q
node --check src/tenable_reports/webapp/static/app.js
```

- [ ] **Step 5: Commit**

```powershell
git add src/tenable_reports/webapp/static tests/test_web_batch_ui.py tests/test_webapp.py
git commit -m "feat: selecionar clientes e filtrar por analista"
```

---

## Incremento 2 — Retentativa por componente

### Task 5: Domínio e persistência de tentativas por componente

**Files:**
- Create: `src/tenable_reports/domain/report_components.py`
- Create: `src/tenable_reports/application/report_components.py`
- Create: `src/tenable_reports/infrastructure/report_components_postgresql.py`
- Create: `src/tenable_reports/infrastructure/postgresql_migrations/0010_report_component_attempts.sql`
- Create: `tests/test_report_components.py`
- Create: `tests/test_report_components_postgresql.py`

**Interfaces:**
- Produces: `ReportComponent`, `ComponentStatus`, `ComponentStage`, `ComponentAttempt`, `ReportComponentRepository`.

- [ ] **Step 1: Escrever testes RED do domínio**

```python
def test_retryable_components_select_only_failed_or_interrupted():
    attempts = (
        attempt("VM_CORE", "COMPLETE", retryable=False),
        attempt("WAS", "FAILED", retryable=True),
        attempt("CLOUD", "FAILED", retryable=True),
    )
    assert retryable_components(attempts) == (ReportComponent.WAS, ReportComponent.CLOUD)

```

Adicionar um segundo teste com WAS `FAILED/retryable`, VM sem checkpoint e
assertar que `validate_component_selection()` retorna
`MISSING_VM_CHECKPOINT_FOR_WAS`.

Testar no adapter PostgreSQL unicidade por `(source_run_id, component, attempt_number)`, payload sanitizado e listagem do último estado.
Adicionar teste de composição global: `VM_CORE=FAILED`, `WAS=FAILED` e
`CLOUD=COMPLETE` deve produzir estado parcial, preservar o artefato Cloud como
disponível e oferecer retry somente de `VM_CORE`/`WAS`.

- [ ] **Step 2: Confirmar RED**

Run: `python -m pytest tests/test_report_components.py tests/test_report_components_postgresql.py -q`

- [ ] **Step 3: Implementar domínio, protocolo, adapter e migration**

```python
class ReportComponent(StrEnum):
    VM_CORE = "VM_CORE"
    WAS = "WAS"
    CLOUD = "CLOUD"

class ComponentStage(StrEnum):
    COLLECTION = "COLLECTION"
    DATASET = "DATASET"
    RENDER = "RENDER"
    DOCUMENT_VALIDATION = "DOCUMENT_VALIDATION"
    SNAPSHOT_PUBLICATION = "SNAPSHOT_PUBLICATION"
    REPORT_PUBLICATION = "REPORT_PUBLICATION"
```

- [ ] **Step 4: Confirmar GREEN**

Run: `python -m pytest tests/test_report_components.py tests/test_report_components_postgresql.py -q`

- [ ] **Step 5: Commit**

```powershell
git add src/tenable_reports/domain/report_components.py src/tenable_reports/application/report_components.py src/tenable_reports/infrastructure/report_components_postgresql.py src/tenable_reports/infrastructure/postgresql_migrations/0010_report_component_attempts.sql tests/test_report_components.py tests/test_report_components_postgresql.py
git commit -m "feat: persistir tentativas por componente"
```

### Task 6: Diagnóstico Cloud por etapa e retomada sem API

**Files:**
- Modify: `src/tenable_reports/application/cloud_execution.py`
- Modify: `src/tenable_reports/cli.py`
- Modify: `tests/test_cloud_execution.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Produces: eventos Cloud com `stage`/`failure_code`; `retry_cloud_component` retoma do dataset íntegro.

- [ ] **Step 1: Escrever RED para o caso TRT8 sanitizado**

```python
def test_retry_cloud_with_valid_dataset_starts_at_render_without_api(tmp_path):
    deps, calls = dependencies_with_existing_dataset(tmp_path)
    result = retry_cloud_component(request(), dependencies=deps, resume=cloud_resume("RENDER"))
    assert calls["collect"] == 0
    assert calls["render"] == ["expanded"]
    assert result.status is CloudExecutionStatus.COMPLETE
```

Adicionar teste que lança em `render_report` e prova evento `FAILED`, `stage="RENDER"`, `failure_code` sanitizado e ausência de mensagem bruta.

- [ ] **Step 2: Confirmar RED**

Run: `python -m pytest tests/test_cloud_execution.py tests/test_cli.py -q -k "retry_cloud or cloud_failure_stage"`

- [ ] **Step 3: Implementar fronteiras explícitas**

Separar `write_dataset`, `render_report`, `validate_document` e `repository.publish` em blocos que decoram a exceção com `ComponentStage`. Aceitar um `CloudResumeContext` validado por hash/raiz e não chamar `collect_live` quando o dataset estiver íntegro.

- [ ] **Step 4: Confirmar GREEN e proteção de secrets**

Run: `python -m pytest tests/test_cloud_execution.py tests/test_cli.py -q`

- [ ] **Step 5: Commit**

```powershell
git add src/tenable_reports/application/cloud_execution.py src/tenable_reports/cli.py tests/test_cloud_execution.py tests/test_cli.py
git commit -m "fix: retomar cloud pela etapa falha"
```

### Task 7: Caso de uso genérico de retentativa seletiva

**Files:**
- Create: `src/tenable_reports/application/component_retry.py`
- Modify: `src/tenable_reports/cli.py`
- Modify: `src/tenable_reports/application/collect_was.py`
- Create: `tests/test_component_retry.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Produces: `ComponentRetryRequest`, `retry_failed_components()` e CLI `retry-components`.
- Consumes: repositório da Task 5, Cloud da Task 6 e recuperação WAS existente.

- [ ] **Step 1: Escrever testes RED de seleção/dependências**

Criar casos table-driven com expectativas literais:

| Caso | Chamadas externas esperadas | Documentos substituídos | Resultado |
|---|---:|---|---|
| Cloud falho com dataset íntegro | 0 | `cloud` | `COMPLETE` |
| Cloud com paginação incompleta | somente páginas Cloud restantes | `cloud` | `COMPLETE` |
| WAS falho com VM íntegro | somente WAS | documentos com seção WEB | `COMPLETE` |
| VM com UUID ativo e chunks locais | somente status/chunks VM restantes | geral/custom/TAG | `COMPLETE` |
| VM raw completo | 0 | geral/custom/TAG | `COMPLETE` |
| VM e WAS falhos, Cloud completo | nenhuma chamada Cloud | somente geral/custom/TAG após recuperação VM/WAS | estado parcial antes do retry; Cloud preservado e não consultado novamente |
| componente já completo em `failed_only` | 0 | nenhum | `COMPONENT_NOT_RETRYABLE` |
| qualquer retry falha | conforme etapa | nenhum | manifesto original byte a byte |

- [ ] **Step 2: Confirmar RED**

Run: `python -m pytest tests/test_component_retry.py tests/test_cli.py -q -k "component"`

- [ ] **Step 3: Implementar o caso de uso**

```python
@dataclass(frozen=True, slots=True)
class ComponentRetryRequest:
    source_run_id: str
    selected_components: Sequence[ReportComponent]
    failed_only: bool = True
```

A função pública tem assinatura
`retry_failed_components(request: ComponentRetryRequest, *, dependencies: ComponentRetryDependencies) -> ComponentRetryResult`.

Usar staging irmão; validar documentos; atualizar manifesto/repositório em uma única confirmação; remover somente staging novo em falha.

- [ ] **Step 4: Confirmar GREEN**

Run: `python -m pytest tests/test_component_retry.py tests/test_cli.py -q`

- [ ] **Step 5: Commit**

```powershell
git add src/tenable_reports/application/component_retry.py src/tenable_reports/application/collect_was.py src/tenable_reports/cli.py tests/test_component_retry.py tests/test_cli.py
git commit -m "feat: retentar componentes falhos seletivamente"
```

### Task 8: API e interface da retentativa por componente

**Files:**
- Modify: `src/tenable_reports/webapp/server.py`
- Modify: `src/tenable_reports/webapp/static/app.js`
- Modify: `src/tenable_reports/webapp/static/app.css`
- Modify: `tests/test_webapp.py`
- Modify: `tests/test_web_batch_ui.py`

**Interfaces:**
- Produces: `GET /api/reports/<run_id>/components` e `POST /api/reports/<run_id>/retry-components`.
- Mantém: `/retry-cloud` como wrapper de compatibilidade.

- [ ] **Step 1: Escrever testes RED HTTP/UI**

Cobrir três contratos HTTP com payloads literais:

- POST sem `components` seleciona `['CLOUD']` quando somente Cloud está
  `FAILED/retryable` e responde `202`;
- POST com `['VM_CORE']` já completo ou `['UNKNOWN']` responde `400`, sem criar
  tentativa;
- GET de conjunto parcial retorna VM/WAS `COMPLETE`, Cloud `FAILED`,
  `retryable_components: ['CLOUD']` e preserva os três documentos válidos.

- [ ] **Step 2: Confirmar RED**

Run: `python -m pytest tests/test_webapp.py tests/test_web_batch_ui.py -q -k "component"`

- [ ] **Step 3: Implementar endpoints e controles**

Mostrar chips VM/WAS/Cloud e botões `Tentar componentes com falha`/`Selecionar componentes`. Confirmar nomes dos componentes e conjunto afetado antes do POST.

- [ ] **Step 4: Confirmar GREEN/JS**

Run:

```powershell
python -m pytest tests/test_webapp.py tests/test_web_batch_ui.py -q
node --check src/tenable_reports/webapp/static/app.js
```

- [ ] **Step 5: Commit**

```powershell
git add src/tenable_reports/webapp tests/test_webapp.py tests/test_web_batch_ui.py
git commit -m "feat: expor retentativa por componente na interface"
```

---

## Incremento 3 — Coleta remota concorrente e montagem serial

### Task 9: Checkpoint sanitizado e separação coleta/montagem

**Files:**
- Create: `src/tenable_reports/application/staged_execution.py`
- Create: `src/tenable_reports/application/remote_collection.py`
- Create: `src/tenable_reports/application/local_build.py`
- Modify: `src/tenable_reports/application/period_collection.py`
- Modify: `src/tenable_reports/cli.py`
- Create: `tests/test_staged_execution.py`
- Modify: `tests/test_cli_collection_routing.py`

**Interfaces:**
- Produces: `CollectionCheckpoint`, `collect_client_remote()` e `build_client_local()`; CLI `collect-client` e `build-client`.

- [ ] **Step 1: Escrever testes RED do checkpoint e ausência de API local**

Criar três fixtures literais:

1. checkpoint completo com arquivos `assets-0.gz`, `vm-0.gz`, `was-0.gz` e
   `cloud.jsonl`, hashes SHA-256 conhecidos e nenhuma chave sensível; round-trip
   precisa preservar todos os campos;
2. caminho `..\\fora\\vm.gz` precisa resultar em `CHECKPOINT_PATH_OUTSIDE_ROOT`;
3. build recebe um transporte que falha se chamado, produz dataset/documentos
   sintéticos e termina com zero chamadas HTTP.

- [ ] **Step 2: Confirmar RED**

Run: `python -m pytest tests/test_staged_execution.py tests/test_cli_collection_routing.py -q`

- [ ] **Step 3: Implementar o mínimo**

```python
@dataclass(frozen=True, slots=True)
class CollectionCheckpoint:
    schema_version: int
    client_id: str
    tenant_id: str
    run_id: str
    logical_job_id: str
    execution_type: str
    mode: str
    origin: str
    attempt_number: int
    period: Mapping[str, str]
    vm_exports: Mapping[str, Mapping[str, Any]]
    selected_tags: Sequence[Mapping[str, Any]]
    was: Mapping[str, Any]
    cloud: Mapping[str, Any]
    component_artifacts: Mapping[str, Mapping[str, Any]]
    hashes: Mapping[str, str]
```

Adicionar testes literais para UUID/origem/status/chunks VM, estratégia seletiva,
TAGs, estado WAS, checkpoint Cloud, modo/origem/tentativa e rejeição de qualquer
hash obrigatório divergente. Extrair de `collect_external_period` a fase raw;
normalização/dataset/TAG/render ficam em `local_build`. Cloud coleta raw,
enrichments e checkpoint remoto; dataset Cloud, snapshot compacto e DOCX ficam na
fase local.

- [ ] **Step 4: Confirmar GREEN e compatibilidade run-client**

Run: `python -m pytest tests/test_staged_execution.py tests/test_cli_collection_routing.py tests/test_cli.py -q`

- [ ] **Step 5: Commit**

```powershell
git add src/tenable_reports/application src/tenable_reports/cli.py tests/test_staged_execution.py tests/test_cli_collection_routing.py tests/test_cli.py
git commit -m "feat: separar coleta remota da montagem local"
```

### Task 10: Fases duráveis no PostgreSQL

**Files:**
- Modify: `src/tenable_reports/domain/web_batches.py`
- Modify: `src/tenable_reports/application/web_batches.py`
- Modify: `src/tenable_reports/infrastructure/web_batches_postgresql.py`
- Create: `src/tenable_reports/infrastructure/postgresql_migrations/0011_web_batch_job_phases.sql`
- Modify: `tests/test_web_batches.py`
- Modify: `tests/test_web_batches_postgresql.py`

**Interfaces:**
- Produces: `BatchJobPhase`, claim filtrado por fase e transação `advance_job_phase`.

- [ ] **Step 1: Escrever RED de transições/claim**

Usar a mesma sequência literal de jobs em memória e PostgreSQL:

| Estado inicial | Operação | Estado esperado |
|---|---|---|
| `REMOTE_RUNNING` + checkpoint válido | `advance_job_phase` | `QUEUED/READY_FOR_BUILD` no mesmo ID |
| `READY_FOR_BUILD` | claim remoto | não reivindicado |
| `REMOTE_QUEUED` | claim build | não reivindicado |
| `LEGACY/QUEUED` | worker legado | comando `run-client` |
| `REMOTE_RUNNING` abandonado + parcial | reconcile | `QUEUED/REMOTE_QUEUED` |
| `BUILD_RUNNING` abandonado + checkpoint | reconcile | `QUEUED/READY_FOR_BUILD` |

- [ ] **Step 2: Confirmar RED**

Run: `python -m pytest tests/test_web_batches.py tests/test_web_batches_postgresql.py -q -k "phase or staged or legacy"`

- [ ] **Step 3: Implementar domínio/migration/adapter**

```python
class BatchJobPhase(StrEnum):
    LEGACY = "LEGACY"
    REMOTE_QUEUED = "REMOTE_QUEUED"
    REMOTE_RUNNING = "REMOTE_RUNNING"
    REMOTE_WAITING_DECISION = "REMOTE_WAITING_DECISION"
    READY_FOR_BUILD = "READY_FOR_BUILD"
    BUILD_RUNNING = "BUILD_RUNNING"
    TERMINAL = "TERMINAL"
```

`claim_next_job(worker_id, phases)` usa `FOR UPDATE SKIP LOCKED`; avanço valida checkpoint antes de `READY_FOR_BUILD`.

- [ ] **Step 4: Confirmar GREEN**

Run: `python -m pytest tests/test_web_batches.py tests/test_web_batches_postgresql.py -q`

- [ ] **Step 5: Commit**

```powershell
git add src/tenable_reports/domain/web_batches.py src/tenable_reports/application/web_batches.py src/tenable_reports/infrastructure/web_batches_postgresql.py src/tenable_reports/infrastructure/postgresql_migrations/0011_web_batch_job_phases.sql tests/test_web_batches.py tests/test_web_batches_postgresql.py
git commit -m "feat: persistir fases da fila durável"
```

### Task 11: Pools remotos e worker local único

**Files:**
- Modify: `src/tenable_reports/webapp/job_queue.py`
- Modify: `tests/test_durable_job_queue.py`
- Modify: `tests/test_durable_batch_process_control.py`

**Interfaces:**
- Produces: `DurableWorkerPool` remoto e local, fechamento determinístico e snapshot de capacidade.

- [ ] **Step 1: Escrever RED de concorrência real controlada**

Usar barreiras/eventos controlados e afirmar:

- 20 IDs de cliente distintos entram no runner remoto antes da liberação da
  barreira;
- dois jobs do mesmo cliente nunca ficam simultaneamente no runner;
- o contador máximo observado no runner build é exatamente `1`;
- `close()` encerra todos os nomes `tenable-remote-*`/`tenable-build-*` e nenhum
  job permanece reclamado sem worker;
- pausa mantém o job em execução/checkpoint e impede novo claim;
- parada sinaliza cada control file, preserva chunks e termina como
  `INTERRUPTED`, sem apagar manifests.

Usar `threading.Event`/barreiras, não `sleep` arbitrário.

- [ ] **Step 2: Confirmar RED**

Run: `python -m pytest tests/test_durable_job_queue.py tests/test_durable_batch_process_control.py -q -k "pool or concurrent or build"`

- [ ] **Step 3: Implementar pools**

```python
DurableWorkerPool(repository, runner, worker_prefix, phases, workers)
```

Modo automático usa `min(selected_clients, 64)` workers remotos; build usa exatamente um. Falta de disco não reivindica novo job e emite alerta sem abrir export.

- [ ] **Step 4: Confirmar GREEN sem threads órfãs**

Run: `python -m pytest tests/test_durable_job_queue.py tests/test_durable_batch_process_control.py -q`

- [ ] **Step 5: Commit**

```powershell
git add src/tenable_reports/webapp/job_queue.py tests/test_durable_job_queue.py tests/test_durable_batch_process_control.py
git commit -m "feat: executar coleta concorrente e montagem serial"
```

### Task 12: Orquestração, automação mensal e timeout de duas horas

**Files:**
- Modify: `src/tenable_reports/webapp/server.py`
- Modify: `src/tenable_reports/application/orchestration.py`
- Modify: `src/tenable_reports/config/environment.py`
- Modify: `tests/test_orchestration.py`
- Modify: `tests/test_profile_environment.py`
- Modify: `tests/test_web_batches.py`

**Interfaces:**
- Novos lotes manuais/automáticos usam `execution_model=STAGED_V1`.
- `remote_collection_workers=0`, `remote_processing_timeout_seconds=7200`, aviso em 900 e `local_build_workers=1`.

- [ ] **Step 1: Escrever RED de políticas**

Usar relógio injetável e expectativas literais:

- seleção de 20 clientes com workers `0` resulta em capacidade remota `20` e
  capacidade build `1`;
- mensal inclui todos os clientes automáticos ativos e persiste
  `execution_model='STAGED_V1'`;
- em 900 segundos emite `TENABLE_EXPORT_NO_PROGRESS_WARNING`, sem cancelamento;
- UUID reutilizado chega a 7.200 segundos com `cancel_calls == []`;
- UUID criado, zero progresso, chega a 7.200 segundos com uma chamada de
  cancelamento e erro retryable;
- UUID criado com um chunk persistido chega a 7.200 segundos sem cancelamento e
  conserva o manifesto;
- reinício reenvia coleta parcial ao pool remoto e build parcial ao pool local,
  comprovando zero repetição das etapas já validadas.

- [ ] **Step 2: Confirmar RED**

Run: `python -m pytest tests/test_orchestration.py tests/test_profile_environment.py tests/test_web_batches.py -q -k "staged or remote_worker or 7200"`

- [ ] **Step 3: Implementar configuração/integração**

Preservar `run-client` para `LEGACY`; novos jobs constroem `collect-client`, avançam pelo checkpoint e depois constroem `build-client`. Pausa/parada/retry mantêm a fase mais avançada validada.

- [ ] **Step 4: Confirmar GREEN**

Run: `python -m pytest tests/test_orchestration.py tests/test_profile_environment.py tests/test_web_batches.py -q`

- [ ] **Step 5: Commit**

```powershell
git add src/tenable_reports/webapp/server.py src/tenable_reports/application/orchestration.py src/tenable_reports/config/environment.py tests/test_orchestration.py tests/test_profile_environment.py tests/test_web_batches.py
git commit -m "feat: integrar execução faseada aos lotes"
```

### Task 13: Progresso faseado na interface

**Files:**
- Modify: `src/tenable_reports/webapp/static/app.js`
- Modify: `src/tenable_reports/webapp/static/app.css`
- Modify: `tests/test_web_batch_ui.py`
- Modify: `tests/test_webapp.py`

**Interfaces:**
- Produces: contadores remoto/aguardando/pronto/build e rótulo explicativo para `0/0`.

- [ ] **Step 1: Escrever RED de apresentação**

Testar o estado serializado e funções de cópia/contagem, incluindo vinte remotos e um build.

- [ ] **Step 2: Confirmar RED**

Run: `python -m pytest tests/test_web_batch_ui.py tests/test_webapp.py -q -k "phase or remote or waiting_tenable"`

- [ ] **Step 3: Implementar UI**

Mostrar `Coletando`, `Aguardando Tenable`, `Prontos para montar`, `Montando`, concorrência efetiva e `0/0 · aguardando a Tenable informar chunks`.

- [ ] **Step 4: Confirmar GREEN/JS**

Run:

```powershell
python -m pytest tests/test_web_batch_ui.py tests/test_webapp.py -q
node --check src/tenable_reports/webapp/static/app.js
```

- [ ] **Step 5: Commit**

```powershell
git add src/tenable_reports/webapp/static tests/test_web_batch_ui.py tests/test_webapp.py
git commit -m "feat: exibir fases da coleta concorrente"
```

### Task 14: Guias, regressão integral e gates visuais

**Files:**
- Modify: `docs/20-arquitetura-e-fluxo-de-dados.md`
- Modify: `docs/22-guia-operacional.md`
- Modify: `docs/23-guia-de-desenvolvimento.md`
- Modify: `.agents/skills/operating-tenable-reports/references/runbook.md`
- Test: suíte completa e inspeção visual local isolada.

**Interfaces:**
- Documenta analistas, seleção, componentes, duas fases, timeout, retry e rollback.

- [ ] **Step 1: Atualizar guias vigentes**

Preservar specs históricas; registrar que `STAGED_V1` substitui a sequência estrita apenas para novos lotes.

- [ ] **Step 2: Executar testes focados conjuntos**

Run: `python -m pytest tests/test_analyst_catalog.py tests/test_report_components.py tests/test_component_retry.py tests/test_staged_execution.py tests/test_durable_job_queue.py tests/test_webapp.py -q`

- [ ] **Step 3: Executar verificação mínima integral**

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe tools\validate_project_guidance.py --root .
.\.venv\Scripts\python.exe tools\audit_secret_leaks.py
node --check src\tenable_reports\webapp\static\app.js
git diff --check
```

- [ ] **Step 4: Validar interface e DOCX afetados**

Usar servidor fake/DB em memória e navegador local autorizado para desktop/mobile. Não chamar API. Mudança não editorial não exige novo conteúdo DOCX, mas retentativa deve validar atomicidade com documentos sintéticos; qualquer DOCX real alterado exige LibreOffice e inspeção visual.

- [ ] **Step 5: Commit dos guias**

```powershell
git add docs/20-arquitetura-e-fluxo-de-dados.md docs/22-guia-operacional.md docs/23-guia-de-desenvolvimento.md .agents/skills/operating-tenable-reports/references/runbook.md
git commit -m "docs: operar coleta concorrente e retries seletivos"
```

## Rollback

- Configurar novos lotes como `LEGACY` desativa a execução faseada sem remover dados.
- Migrações 0010/0011 são aditivas; código anterior ignora as novas tabelas/colunas.
- Catálogo de analistas é configuração opcional; clientes sem vínculo continuam válidos.
- Falha em retry seletivo conserva manifesto e documentos originais.
- Nenhum rollback apaga checkpoints, chunks ou conjuntos publicados.
