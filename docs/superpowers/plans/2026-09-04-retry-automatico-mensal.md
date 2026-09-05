# Retry automático mensal e configuração administrativa — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unificar **Gerar todos** e o automático mensal no coordenador durável
`STAGED_V1`, executar VM/WAS/Cloud remotamente em paralelo, aplicar duas janelas
automáticas de 10 horas e uma terceira condicional, e permitir configurar o
agendamento pelo painel Admin.

**Architecture:** `web_batch_jobs` continua representando um cliente e sua montagem
local, enquanto uma nova tabela filha representa cada componente remoto e suas
janelas recuperáveis. Coletores produzem checkpoints independentes, o coordenador
os consolida antes do único worker DOCX e a família de lote agrega tentativas sem
contar clientes duas vezes. A configuração mensal fica no JSON da carteira; um
adaptador isolado consulta e sincroniza o Agendador de Tarefas do Windows.

**Tech Stack:** Python 3.14, PostgreSQL/psycopg, pytest, Tenable VM/WAS REST,
Tenable Cloud GraphQL, PowerShell/`schtasks.exe`, HTTPServer local e JavaScript/CSS/
HTML sem framework.

**Spec:** `docs/superpowers/specs/2026-09-04-retry-automatico-mensal-design.md`

## Global Constraints

- Trabalhar somente em `codex/retry-automatico-mensal`; não criar outra branch ou
  worktree neste ciclo.
- TDD é obrigatório: cada comportamento começa com teste falhando pelo motivo
  esperado, seguido da implementação mínima e do teste verde.
- Nenhum teste automatizado acessa Tenable, PostgreSQL real, Agendador do Windows,
  servidor real ou credenciais.
- As janelas usam exatamente `36_000` segundos; existem duas janelas comuns e uma
  terceira somente se a Janela 2 criou uma operação substituta.
- Não existe Janela 4 e criar novo UUID/cursor na Janela 3 não reinicia seu relógio.
- `PROCESSING`, `QUEUED`, ausência momentânea de chunks, `429`, `5xx` e erro de rede
  não invalidam identificadores.
- UUID/cursor/checkpoint válido é retomado antes de criar operação substituta;
  nenhum export remoto é cancelado automaticamente.
- Coletores remotos podem executar para até
  `min(clientes_elegíveis, 64, capacidade_segura_do_PostgreSQL)` clientes; a
  montagem DOCX mantém exatamente um worker global.
- Conexões PostgreSQL não permanecem abertas durante espera HTTP ou subprocesso.
- Períodos continuam `[início, fim)` no fuso do cliente; o automático usa o mês
  anterior completo.
- TAG recorta somente relatórios por TAG/comparativos e nunca o relatório geral.
- Severidade informativa continua fora; textos, métricas e estrutura DOCX não
  mudam nesta entrega.
- WAS e Cloud só participam quando habilitados no perfil. Módulo não contratado é
  `NOT_APPLICABLE`; resultado válido vazio é `COMPLETE`.
- Conjunto parcial não vira `MAIN`; módulos concluídos não são coletados novamente.
- Salvar/validar agendamento não cria lote, não chama Tenable e não altera a tarefa
  do Windows.
- Sincronizar, ativar ou desativar a tarefa exige confirmação explícita e nunca
  solicita ou armazena senha administrativa.
- Commits são feitos na branch atual; merge/push e alteração da tarefa oficial
  somente após a verificação completa e autorização correspondente.

---

## File map

**Novos arquivos de domínio/aplicação**

- `src/tenable_reports/domain/remote_components.py`: estados persistidos de cada
  componente remoto e representação de uma janela.
- `src/tenable_reports/application/automatic_recovery.py`: decisão pura das
  Janelas 1, 2 e 3.
- `src/tenable_reports/application/component_collection.py`: checkpoint por
  componente e consolidação para `CollectionCheckpoint`.
- `src/tenable_reports/application/monthly_batch.py`: cálculo/idempotência e
  execução headless do lote mensal.
- `src/tenable_reports/config/monthly_schedule.py`: schema e validação da política
  mensal não secreta.

**Novos adaptadores**

- `src/tenable_reports/infrastructure/web_batch_components_postgresql.py`:
  persistência e claims curtos dos componentes.
- `src/tenable_reports/infrastructure/windows_task_scheduler.py`: consulta e
  sincronização controlada de `schtasks.exe`.
- `src/tenable_reports/infrastructure/postgresql_migrations/0014_remote_component_windows.sql`:
  família de lotes e componentes/janelas.

**Novos auxiliares de interface**

- `src/tenable_reports/webapp/static/batch_family_filters.js`: categorias e filtro
  puro da família.
- `src/tenable_reports/webapp/static/monthly_schedule.js`: view-model e validações
  puras da tela mensal.

Os arquivos existentes `cli.py`, `period_collection.py`, `staged_execution.py`,
`durable_dashboard_queue.py`, `web_batches_postgresql.py`, `server.py`, `app.js`,
`index.html`, `app.css` e os dois scripts mensais serão integrados sem duplicar um
segundo coordenador.

---

### Task 1: Domínio das janelas e política pura de recuperação

**Files:**
- Create: `src/tenable_reports/domain/remote_components.py`
- Create: `src/tenable_reports/application/automatic_recovery.py`
- Create: `tests/test_automatic_recovery_policy.py`

**Interfaces:**
- Produces: `RemoteComponentState`, `RemoteIdentifierKind`,
  `RemoteComponentWindow`, `RemoteObservation`, `RecoveryAction`,
  `RecoveryDecision`, `AutomaticRecoveryPolicy` e `decide_recovery()`.
- Consumes: somente valores imutáveis e `now` UTC; não conhece banco, CLI ou API.

- [ ] **Step 1: Escrever os testes RED das transições centrais**

```python
def test_window_two_reuses_a_valid_processing_uuid():
    window = component_window(
        state=RemoteComponentState.RUNNING_WINDOW_2,
        window_number=2,
        remote_identifier="00000000-0000-0000-0000-000000000111",
        replacement_created_in_window_2=False,
    )
    decision = decide_recovery(
        window,
        RemoteObservation.processing(completed=1, total=3),
        now=dt("2026-09-04T10:00:00Z"),
    )
    assert decision.action is RecoveryAction.CONTINUE_CURRENT


def test_window_two_invalid_uuid_creates_one_replacement_and_unlocks_window_three():
    decision = decide_recovery(
        component_window(state=RemoteComponentState.RUNNING_WINDOW_2, window_number=2),
        RemoteObservation.invalid_identifier(code="REMOTE_IDENTIFIER_NOT_FOUND"),
        now=dt("2026-09-04T10:00:00Z"),
    )
    assert decision.action is RecoveryAction.CREATE_REPLACEMENT
    assert decision.mark_replacement_in_window_two is True


def test_window_three_timeout_never_creates_window_four():
    decision = decide_recovery(
        component_window(
            state=RemoteComponentState.RUNNING_WINDOW_3,
            window_number=3,
            deadline_at=dt("2026-09-04T09:59:59Z"),
            replacement_created_in_window_2=True,
        ),
        RemoteObservation.processing(completed=2, total=4),
        now=dt("2026-09-04T10:00:00Z"),
    )
    assert decision.action is RecoveryAction.WAIT_MANUAL_RETRY
    assert decision.next_window is None
```

Cobrir também: sucesso, `NOT_APPLICABLE`, resultado vazio válido, falha retentável
antecipando Janela 2, timeout da Janela 2 sem substituição, `401/403`, perfil
inválido, `429/5xx` e reinício sem alterar `deadline_at`.

- [ ] **Step 2: Executar os testes e confirmar RED**

Run:

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
.\.venv\Scripts\python.exe -m pytest -q tests/test_automatic_recovery_policy.py
```

Expected: FAIL por ausência dos módulos/tipos, não por erro de fixture.

- [ ] **Step 3: Implementar os tipos e a decisão mínima**

```python
class RemoteComponentState(StrEnum):
    PENDING = "PENDING"
    RUNNING_WINDOW_1 = "RUNNING_WINDOW_1"
    RUNNING_WINDOW_2 = "RUNNING_WINDOW_2"
    RUNNING_WINDOW_3 = "RUNNING_WINDOW_3"
    COMPLETE = "COMPLETE"
    COMPLETE_WITH_WARNINGS = "COMPLETE_WITH_WARNINGS"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    WAITING_MANUAL_RETRY = "WAITING_MANUAL_RETRY"
    NON_RETRYABLE_FAILURE = "NON_RETRYABLE_FAILURE"
    INTERRUPTED = "INTERRUPTED"


class RecoveryAction(StrEnum):
    CONTINUE_CURRENT = "CONTINUE_CURRENT"
    COMPLETE = "COMPLETE"
    MARK_NOT_APPLICABLE = "MARK_NOT_APPLICABLE"
    START_NEXT_WINDOW = "START_NEXT_WINDOW"
    CREATE_REPLACEMENT = "CREATE_REPLACEMENT"
    WAIT_MANUAL_RETRY = "WAIT_MANUAL_RETRY"
    FAIL_NON_RETRYABLE = "FAIL_NON_RETRYABLE"


@dataclass(frozen=True, slots=True)
class AutomaticRecoveryPolicy:
    automatic_window_seconds: int = 36_000
    automatic_base_windows: int = 2
    automatic_replacement_window: bool = True
    manual_retry_window_seconds: int = 36_000
```

`RemoteComponentWindow` deve validar `window_number in {1, 2, 3}`, timestamps UTC,
identificador compatível com `RemoteIdentifierKind` e
`replacement_created_in_window_2` somente a partir da Janela 2. A política rejeita
qualquer valor diferente de `36_000`, `2`, `True`, `36_000` nesta versão para
evitar que a configuração local viole o contrato aprovado.

- [ ] **Step 4: Confirmar GREEN e invariantes**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_automatic_recovery_policy.py`

Expected: PASS; remover a guarda de Janela 4 ou tratar `PROCESSING` como inválido
precisa quebrar testes.

- [ ] **Step 5: Commit**

```powershell
git add src/tenable_reports/domain/remote_components.py src/tenable_reports/application/automatic_recovery.py tests/test_automatic_recovery_policy.py
git commit -m "feat: definir politica de recuperacao em tres janelas"
```

### Task 2: Persistência de família e componentes remotos

**Files:**
- Create: `src/tenable_reports/infrastructure/postgresql_migrations/0014_remote_component_windows.sql`
- Create: `src/tenable_reports/infrastructure/web_batch_components_postgresql.py`
- Create: `tests/test_web_batch_components_postgresql.py`
- Modify: `src/tenable_reports/domain/web_batches.py`
- Modify: `src/tenable_reports/application/web_batches.py`
- Modify: `src/tenable_reports/application/web_batches_memory.py`
- Modify: `src/tenable_reports/infrastructure/web_batches_postgresql.py`
- Modify: `tests/test_web_batches.py`
- Modify: `tests/test_web_batches_postgresql.py`
- Modify: `tests/test_postgresql.py`

**Interfaces:**
- Consumes: tipos da Task 1.
- Produces: `RemoteComponentRepository` e persistência de `root_batch_id`,
  `parent_batch_id`, `origin`, `competence`, claims, observações e transições.

- [ ] **Step 1: Escrever testes RED de schema, família e claims**

```python
def test_derived_batch_keeps_root_and_immediate_parent(repository):
    root = repository.create_batch(root_batch(), root_jobs())
    child = repository.create_batch(
        derived_batch(root_batch_id=root.id, parent_batch_id=root.id),
        derived_jobs(),
    )
    assert child.root_batch_id == root.id
    assert child.parent_batch_id == root.id


def test_component_claim_is_atomic_and_scoped_to_one_component(component_repository):
    vm, was, cloud = component_repository.create_for_job(
        batch_job_id=JOB_ID,
        components=(ReportComponent.VM_CORE, ReportComponent.WAS, ReportComponent.CLOUD),
        window_number=1,
        deadline_at=dt("2026-09-04T10:00:00Z"),
    )
    claimed = component_repository.claim_next(worker_id="remote-1")
    assert claimed.id == vm.id
    assert claimed.state is RemoteComponentState.RUNNING_WINDOW_1
    assert component_repository.claim_next(worker_id="remote-2").id == was.id
```

Adicionar casos para unicidade `(batch_job_id, component, attempt_number)`, lease
abandonado, update otimista, sanitização, deadline persistido e listagem de uma
família inteira.

- [ ] **Step 2: Confirmar RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_web_batches.py tests/test_web_batches_postgresql.py tests/test_web_batch_components_postgresql.py tests/test_postgresql.py -k "family or component or migration_0014"
```

Expected: FAIL por migration/repositório/campos inexistentes.

- [ ] **Step 3: Criar a migration idempotente**

Adicionar a `web_batches`:

```sql
alter table tenable_reports.web_batches add column if not exists root_batch_id uuid;
alter table tenable_reports.web_batches add column if not exists parent_batch_id uuid;
alter table tenable_reports.web_batches add column if not exists origin text;
alter table tenable_reports.web_batches add column if not exists competence text;
```

Preencher `root_batch_id = id` para raízes e usar `source_batch_id` como pai dos
lotes antigos. Criar FKs após o backfill. Criar
`tenable_reports.web_batch_remote_components` com `id`, `batch_job_id`,
`component`, `state`, `window_number`, `attempt_number`, `parent_component_id`,
`origin`, `deadline_at`, `replacement_created_in_window_2`, `identifier_kind`,
`remote_identifier`, `identifier_origin`, `query_fingerprint`, `checkpoint_path`,
`completed_units`, `total_units`, `last_remote_status`, `last_contact_at`,
`last_progress_at`, `worker_id`, `lease_expires_at`, `failure_code`,
`failure_message`, `retryable`, `created_at`, `started_at` e `ended_at`.

Os checks SQL devem repetir os enums da Task 1, limitar janela a 1–3, exigir erro
sanitizado nos estados de falha e impedir `replacement_created_in_window_2=true`
na Janela 1.

- [ ] **Step 4: Implementar contrato e adaptadores com conexões curtas**

```python
class RemoteComponentRepository(Protocol):
    def create_for_job(self, *, batch_job_id: UUID,
                       components: Sequence[ReportComponent],
                       window_number: int,
                       deadline_at: datetime,
                       origin: str) -> tuple[RemoteComponentWindow, ...]: ...
    def claim_next(self, *, worker_id: str, lease_seconds: int = 60) -> RemoteComponentWindow | None: ...
    def record_observation(self, component_id: UUID, observation: RemoteObservation) -> RemoteComponentWindow: ...
    def transition(self, component_id: UUID, *, expected_state: RemoteComponentState,
                   requested_state: RemoteComponentState, **changes: Any) -> RemoteComponentWindow: ...
    def list_for_jobs(self, job_ids: Sequence[UUID]) -> Mapping[UUID, tuple[RemoteComponentWindow, ...]]: ...
    def reconcile_abandoned(self, *, now: datetime, active_worker_ids: set[str]) -> int: ...
```

`claim_next()` usa `FOR UPDATE SKIP LOCKED`, confirma o claim e fecha a transação
antes de qualquer subprocesso/API. O adaptador em memória precisa obedecer o mesmo
contrato para todos os testes de coordenador.

- [ ] **Step 5: Confirmar GREEN**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_web_batches.py tests/test_web_batches_postgresql.py tests/test_web_batch_components_postgresql.py tests/test_postgresql.py`

- [ ] **Step 6: Commit**

```powershell
git add src/tenable_reports/domain/web_batches.py src/tenable_reports/application/web_batches.py src/tenable_reports/application/web_batches_memory.py src/tenable_reports/infrastructure/web_batches_postgresql.py src/tenable_reports/infrastructure/web_batch_components_postgresql.py src/tenable_reports/infrastructure/postgresql_migrations/0014_remote_component_windows.sql tests/test_web_batches.py tests/test_web_batches_postgresql.py tests/test_web_batch_components_postgresql.py tests/test_postgresql.py
git commit -m "feat: persistir familias e janelas por componente"
```

### Task 3: Checkpoints independentes e CLI por componente

**Files:**
- Create: `src/tenable_reports/application/component_collection.py`
- Create: `tests/test_component_collection.py`
- Modify: `src/tenable_reports/application/period_collection.py`
- Modify: `src/tenable_reports/application/staged_execution.py`
- Modify: `src/tenable_reports/application/cloud_execution.py`
- Modify: `src/tenable_reports/cli.py`
- Modify: `tests/test_collection_execution.py`
- Modify: `tests/test_cloud_collection.py`
- Modify: `tests/test_cloud_execution.py`
- Modify: `tests/test_cli_collection_routing.py`

**Interfaces:**
- Produces: `ComponentCollectionCheckpoint`, `collect_vm_core_period()`,
  `collect_was_period()`, `collect_cloud_period()`,
  `merge_component_checkpoints()` e CLI `collect-component`.
- Consumes: hashes/validação de `staged_execution.py` e coletores existentes.

- [ ] **Step 1: Escrever testes RED de isolamento e consolidação**

```python
def test_component_checkpoints_write_to_disjoint_directories(tmp_path):
    vm = collect_component_with_fake("VM_CORE", root=tmp_path)
    was = collect_component_with_fake("WAS", root=tmp_path)
    cloud = collect_component_with_fake("CLOUD", root=tmp_path)
    assert vm.checkpoint_path.parent.name == "vm_core"
    assert was.checkpoint_path.parent.name == "was"
    assert cloud.checkpoint_path.parent.name == "cloud"
    assert not ({a.path for a in vm.artifacts} & {a.path for a in was.artifacts})


def test_merge_accepts_complete_vm_failed_was_and_complete_cloud(tmp_path):
    merged = merge_component_checkpoints(
        request=remote_request(tmp_path),
        checkpoints=(complete_vm(tmp_path), failed_was(tmp_path), complete_cloud(tmp_path)),
    )
    assert merged.component_metadata["VM_CORE"]["status"] == "COMPLETE"
    assert merged.component_metadata["WAS"]["status"] == "FAILED"
    assert merged.component_metadata["CLOUD"]["status"] == "COMPLETE"
```

Provar que Cloud remoto não renderiza DOCX, WAS não normaliza dentro do mesmo
diretório do VM, hashes adulterados falham e `SKIPPED` vira `NOT_APPLICABLE`.

- [ ] **Step 2: Confirmar RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_component_collection.py tests/test_collection_execution.py tests/test_cloud_collection.py tests/test_cloud_execution.py tests/test_cli_collection_routing.py
```

- [ ] **Step 3: Extrair coletores sem mudar filtros**

Em `period_collection.py`, separar o atual `collect_external_period()`:

```python
def collect_vm_core_period(*, args, profile, period, output_root, run_id,
                           client, inventory_client, selected_tags,
                           asset_filters, finding_filters, vm_strategy,
                           vm_num_assets, vm_selective_mode, route,
                           plugin_catalog=None, plugin_catalog_callback=None,
                           progress_callback=None) -> VmCorePeriodCollection: ...

def collect_was_period(*, args, profile, period, output_root, run_id,
                       was_client, progress_callback=None) -> WasPeriodCollection: ...
```

Preservar literalmente `since`, estados, severidades, `include_unlicensed`,
propriedades seletivas, recuperação de UUID, TAGs e regras históricas existentes.
Normalização que combina VM/WAS migra para a fase local; a fase remota grava raw,
manifestos e dataset Cloud em subdiretórios exclusivos.

- [ ] **Step 4: Implementar checkpoint e comando `collect-component`**

```python
@dataclass(frozen=True, slots=True)
class ComponentCollectionCheckpoint:
    schema_version: int
    component: ReportComponent
    client_id: str
    tenant_id: str
    run_id: str
    period: Mapping[str, Any]
    status: RemoteComponentState
    artifacts: tuple[CheckpointArtifact, ...]
    metadata: Mapping[str, Any]
    query_fingerprint: str


def merge_component_checkpoints(
    *, request: RemoteCollectionRequest,
    checkpoints: Sequence[ComponentCollectionCheckpoint],
) -> CollectionCheckpoint: ...
```

O CLI recebe `--component`, `--component-checkpoint`, `--window-number`,
`--deadline-at`, identificador/checkpoint anterior opcional e os argumentos de
coleta já existentes. Ele imprime eventos JSONL com `component`, janela,
identificador, origem, estado e unidades concluídas, sem credenciais.

- [ ] **Step 5: Confirmar GREEN e compatibilidade do comando antigo**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_component_collection.py tests/test_collection_execution.py tests/test_cloud_collection.py tests/test_cloud_execution.py tests/test_cli_collection_routing.py tests/test_cli.py -k "collect_component or collect_client or staged or cloud"
```

O `collect-client` permanece compatível para diagnóstico, mas novos lotes não o
usam como unidade remota.

- [ ] **Step 6: Commit**

```powershell
git add src/tenable_reports/application/component_collection.py src/tenable_reports/application/period_collection.py src/tenable_reports/application/staged_execution.py src/tenable_reports/application/cloud_execution.py src/tenable_reports/cli.py tests/test_component_collection.py tests/test_collection_execution.py tests/test_cloud_collection.py tests/test_cloud_execution.py tests/test_cli_collection_routing.py
git commit -m "feat: coletar componentes em checkpoints independentes"
```

### Task 4: Coordenador concorrente da Janela 1 e montagem serial

**Files:**
- Modify: `src/tenable_reports/webapp/durable_dashboard_queue.py`
- Modify: `src/tenable_reports/webapp/server.py`
- Modify: `src/tenable_reports/application/web_batches.py`
- Modify: `src/tenable_reports/application/orchestration.py`
- Modify: `tests/test_durable_job_queue.py`
- Modify: `tests/test_webapp.py`
- Modify: `tests/test_orchestration.py`

**Interfaces:**
- Consumes: repositório da Task 2 e CLI/checkpoints da Task 3.
- Produces: workers remotos por componente e promoção atômica do cliente para
  `READY_FOR_BUILD`.

- [ ] **Step 1: Escrever testes RED de paralelismo e build único**

```python
def test_staged_batch_claims_three_components_without_three_builds(app):
    batch = app.enqueue_jobs(["client-a"], automatic_request())[0]
    claimed = app.jobs.claim_remote_for_test(3)
    assert {item.component for item in claimed} == {
        ReportComponent.VM_CORE, ReportComponent.WAS, ReportComponent.CLOUD,
    }
    finish_components(app, claimed)
    assert app.jobs.batch_snapshot(batch["batch_id"])["build_queue_count"] == 1


def test_remote_capacity_uses_clients_bounded_by_64_and_build_capacity_is_one(app):
    add_clients(app, 25)
    capacities = app.jobs.capacity_snapshot()
    assert capacity(capacities, "REMOTE_COMPONENT") == 25
    assert capacity(capacities, "READY_FOR_BUILD") == 1
```

Adicionar teste que mantém uma conexão-fake marcada como fechada enquanto o runner
fica bloqueado e outro que impede dois coordenadores de reivindicar o mesmo
componente. Validar também que `automatic_window_seconds=36_000`,
`automatic_base_windows=2`, `automatic_replacement_window=true`,
`manual_retry_window_seconds=36_000`, `remote_collection_workers=0` e
`local_build_workers=1` são carregados, e que valores incompatíveis são recusados.

- [ ] **Step 2: Confirmar RED**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_durable_job_queue.py tests/test_webapp.py tests/test_orchestration.py -k "component or parallel or capacity or build or automatic_window"`

- [ ] **Step 3: Inicializar componentes e pools**

Ao criar `STAGED_V1`, determinar módulos pelo perfil e chamar
`create_for_job(..., window_number=1, deadline_at=started_at+36_000s,
origin=request.origin)`. O pool
remoto reivindica componentes, não o `web_batch_job` inteiro. O pool legado continua
isolado para lotes `LEGACY`; o pool de build continua com capacidade `1`.

O carregador de `clients.json` lê a política fixa pelo
`AutomaticRecoveryPolicy`; o painel e o comando headless recebem a mesma instância,
sem constantes concorrentes em camadas diferentes.

O número automático de workers é:

```python
def remote_worker_capacity(enabled_clients: int, safe_db_capacity: int) -> int:
    return max(1, min(enabled_clients, 64, safe_db_capacity))
```

- [ ] **Step 4: Executar componente e consolidar cliente**

Cada claim monta `collect-component`, fecha a transação, executa o processo e grava
observações curtas. Quando não existir componente `PENDING`/`RUNNING`:

```python
if remote_components_ready_for_build(components):
    checkpoint = merge_component_checkpoints(request=request, checkpoints=loaded)
    repository.advance_job_phase(
        job.id,
        expected_phase=BatchJobPhase.REMOTE_RUNNING,
        requested_phase=BatchJobPhase.READY_FOR_BUILD,
        collection_checkpoint_path=checkpoint_path,
    )
```

Se nenhum componente produzir artefato publicável, terminar o cliente como
`FAILED`; caso contrário, permitir build parcial.

- [ ] **Step 5: Confirmar GREEN e regressões de pausa/parada**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_durable_job_queue.py tests/test_durable_batch_process_control.py tests/test_web_batch_http_controls.py tests/test_webapp.py tests/test_orchestration.py`

- [ ] **Step 6: Commit**

```powershell
git add src/tenable_reports/webapp/durable_dashboard_queue.py src/tenable_reports/webapp/server.py src/tenable_reports/application/web_batches.py src/tenable_reports/application/orchestration.py tests/test_durable_job_queue.py tests/test_webapp.py tests/test_orchestration.py
git commit -m "feat: coordenar componentes remotos em paralelo"
```

### Task 5: Janelas automáticas 2 e 3, substituição e reinício

**Files:**
- Modify: `src/tenable_reports/webapp/durable_dashboard_queue.py`
- Modify: `src/tenable_reports/infrastructure/web_batch_components_postgresql.py`
- Modify: `src/tenable_reports/application/collect.py`
- Modify: `src/tenable_reports/application/collect_was.py`
- Modify: `src/tenable_reports/application/collect_cloud.py`
- Modify: `tests/test_durable_job_queue.py`
- Modify: `tests/test_inventory_collection_resume.py`
- Modify: `tests/test_collection.py`
- Modify: `tests/test_cloud_collection.py`

**Interfaces:**
- Consumes: `decide_recovery()`.
- Produces: continuidade do identificador, substituição controlada e avanço
  automático até o limite permitido.

- [ ] **Step 1: Escrever testes RED das três janelas**

```python
def test_timeout_window_one_enqueues_window_two_with_same_uuid(coordinator):
    first = running_vm(window=1, uuid=UUID_A, deadline_at=past())
    coordinator.observe(first, processing(chunks=(1,), total=2))
    second = coordinator.latest_component(first.batch_job_id, ReportComponent.VM_CORE)
    assert second.window_number == 2
    assert second.remote_identifier == UUID_A


def test_invalid_uuid_in_window_two_creates_replacement_then_allows_window_three(coordinator):
    second = running_vm(window=2, uuid=UUID_A)
    coordinator.observe(second, invalid_identifier())
    replacement = coordinator.latest_component(second.batch_job_id, ReportComponent.VM_CORE)
    assert replacement.remote_identifier == UUID_B
    assert replacement.replacement_created_in_window_2 is True
    coordinator.observe(replacement, timeout_processing())
    assert coordinator.latest_component(second.batch_job_id, ReportComponent.VM_CORE).window_number == 3
```

Cobrir VM/WAS `FINISHED`, `FAILED`, `CANCELLED`, 404, 401/403, 429/5xx; Cloud
cursor válido, cursor rejeitado, dataset completo, deduplicação de páginas e falha
de token. Testar reinício no segundo 35.999 e confirmar que não ganha 10 horas.
Disco insuficiente mantém o componente aguardando ação local sem abrir operação
remota. Cada transição deve emitir evento sanitizado com componente, janela,
origem, deadline, identificador permitido, progresso e decisão.

- [ ] **Step 2: Confirmar RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_durable_job_queue.py tests/test_inventory_collection_resume.py tests/test_collection.py tests/test_cloud_collection.py -k "window or deadline or replacement or resume"
```

- [ ] **Step 3: Aplicar decisões com criação idempotente de janela**

`START_NEXT_WINDOW` cria nova linha filha com `parent_component_id`, incrementa
`attempt_number`, preserva identificador/checkpoint e calcula um único novo
`deadline_at`; a origem passa a `AUTOMATIC_RETRY`. `CREATE_REPLACEMENT` chama o coletor sem identificador anterior,
grava antigo/novo e marca a Janela 2. Uma chave determinística impede duplicação:

```python
component_attempt_key = f"{batch_job_id}:{component.value}:window:{window_number}:attempt:{attempt_number}"
```

- [ ] **Step 4: Classificar VM/WAS/Cloud sem cancelar operações válidas**

O coletor transforma somente 404, `FAILED`, `CANCELLED`, `ERROR`, `ABORTED` e
checkpoint/cursor incompatível em `invalid_identifier=True`. `PROCESSING`,
`QUEUED`, chunks ainda ausentes, rede, 429 e 5xx preservam a operação. Cloud reinicia
somente a fonte que rejeitou cursor e mescla páginas pela identidade canônica.

- [ ] **Step 5: Confirmar GREEN e ausência de Janela 4**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_durable_job_queue.py tests/test_inventory_collection_resume.py tests/test_collection.py tests/test_cloud_collection.py`

- [ ] **Step 6: Commit**

```powershell
git add src/tenable_reports/webapp/durable_dashboard_queue.py src/tenable_reports/infrastructure/web_batch_components_postgresql.py src/tenable_reports/application/collect.py src/tenable_reports/application/collect_was.py src/tenable_reports/application/collect_cloud.py tests/test_durable_job_queue.py tests/test_inventory_collection_resume.py tests/test_collection.py tests/test_cloud_collection.py
git commit -m "feat: automatizar recuperacao em ate tres janelas"
```

### Task 6: Build/publicação parcial e retry manual seletivo

**Files:**
- Modify: `src/tenable_reports/application/staged_execution.py`
- Modify: `src/tenable_reports/application/component_status_recording.py`
- Modify: `src/tenable_reports/domain/report_components.py`
- Modify: `src/tenable_reports/cli.py`
- Modify: `src/tenable_reports/webapp/durable_dashboard_queue.py`
- Modify: `src/tenable_reports/webapp/server.py`
- Modify: `tests/test_staged_execution.py`
- Modify: `tests/test_component_status_recording.py`
- Modify: `tests/test_component_retry.py`
- Modify: `tests/test_compact_publication.py`
- Modify: `tests/test_report_registry_postgresql.py`

**Interfaces:**
- Consumes: checkpoint consolidado e estados finais da família.
- Produces: publicação do que estiver válido, `PARTIALLY_COMPLETE`, retry manual de
  `VM_CORE`/`WAS`/`CLOUD` e promoção `MAIN` apenas integral.

- [ ] **Step 1: Escrever testes RED de publicação e retry**

```python
def test_complete_cloud_is_published_when_vm_waits_manual_retry(app):
    result = build_from_components(vm=waiting_vm(), was=not_applicable(), cloud=complete_cloud())
    assert result.status == "PARTIALLY_COMPLETE"
    assert result.documents_by_component["CLOUD"]
    assert result.documents_by_component.get("VM_CORE") is None


def test_manual_retry_accepts_vm_and_never_recollects_complete_cloud(app):
    retry = app.retry_report_components(RUN_ID, components=["VM_CORE"])
    assert retry["selected_components"] == ["VM_CORE"]
    assert "CLOUD" not in retry["selected_components"]


def test_partial_set_is_not_promoted_main(registry):
    published = publish_partial_set(registry)
    assert registry.get_main(published.reference_key) is None
```

- [ ] **Step 2: Confirmar RED**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_staged_execution.py tests/test_component_status_recording.py tests/test_component_retry.py tests/test_compact_publication.py tests/test_report_registry_postgresql.py`

- [ ] **Step 3: Tornar o build orientado a componentes disponíveis**

`build-client` recebe o checkpoint consolidado, normaliza/renderiza apenas módulos
`COMPLETE`/`COMPLETE_WITH_WARNINGS` e grava tentativas para todos os módulos
planejados. `NOT_APPLICABLE` equivale ao atual `SKIPPED`. Falha de um render afeta
somente o componente dono do documento.

- [ ] **Step 4: Generalizar retry manual e reparo atômico**

Remover a rejeição atual de `VM_CORE` em `command_retry_components`. A retentativa
cria componentes remotos somente para a seleção, usa os documentos/checkpoints do
mesmo `source_run_id` e troca arquivos afetados apenas após hash e validação DOCX.
Ao completar o último componente, recalcular o conjunto e promover `MAIN` conforme
as regras existentes.

- [ ] **Step 5: Confirmar GREEN**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_staged_execution.py tests/test_component_status_recording.py tests/test_component_retry.py tests/test_compact_publication.py tests/test_report_registry_postgresql.py tests/test_cli.py -k "component or partial or main"`

- [ ] **Step 6: Commit**

```powershell
git add src/tenable_reports/application/staged_execution.py src/tenable_reports/application/component_status_recording.py src/tenable_reports/domain/report_components.py src/tenable_reports/cli.py src/tenable_reports/webapp/durable_dashboard_queue.py src/tenable_reports/webapp/server.py tests/test_staged_execution.py tests/test_component_status_recording.py tests/test_component_retry.py tests/test_compact_publication.py tests/test_report_registry_postgresql.py
git commit -m "feat: publicar e reparar conjuntos por componente"
```

### Task 7: Família de lotes, contadores e filtros clicáveis

**Files:**
- Create: `src/tenable_reports/webapp/static/batch_family_filters.js`
- Modify: `src/tenable_reports/webapp/durable_dashboard_queue.py`
- Modify: `src/tenable_reports/webapp/server.py`
- Modify: `src/tenable_reports/webapp/static/index.html`
- Modify: `src/tenable_reports/webapp/static/app.js`
- Modify: `src/tenable_reports/webapp/static/app.css`
- Modify: `tests/test_durable_job_queue.py`
- Modify: `tests/test_web_batch_ui.py`
- Modify: `tests/test_webapp.py`

**Interfaces:**
- Produces: `batch_family_snapshot(root_batch_id)`, categoria efetiva por cliente e
  helper JS `filterFamilyClients()`.
- Consumes: raiz/pai da Task 2 e estados de componentes.

- [ ] **Step 1: Escrever testes RED de agregação sem dupla contagem**

```python
def test_family_counts_latest_effective_client_state_once(queue):
    root, retry = family_with_old_failure_and_active_retry(queue, client_id="client-a")
    snapshot = queue.batch_family_snapshot(root.id)
    assert snapshot["total_count"] == 1
    assert snapshot["counts"]["automatic_retry"] == 1
    assert snapshot["counts"]["failed"] == 0
```

```javascript
const clients = [
  {client_id: "a", effective_status: "AUTOMATIC_RETRY"},
  {client_id: "b", effective_status: "COMPLETE"},
];
return helpers.filterFamilyClients(clients, {
  status: "AUTOMATIC_RETRY", query: "a", analystId: "all"
}).map(item => item.client_id);
```

Cobrir a prioridade: falha definitiva, retry automático, execução inicial,
aguardando manual, semiconcluído, concluído, pendente.

- [ ] **Step 2: Confirmar RED**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_durable_job_queue.py tests/test_web_batch_ui.py tests/test_webapp.py -k "family or clickable or effective"`

- [ ] **Step 3: Implementar snapshot da família**

`GET /api/batches/<id>` passa a resolver `root_batch_id`, retornar raiz,
descendentes, linha do tempo de componentes e uma lista única por `client_id`. As
contagens são calculadas dessa lista, nunca de jobs brutos.

- [ ] **Step 4: Implementar indicadores como filtros combináveis**

Renderizar **Todos**, **Pendentes**, **Em execução**, **Em retry automático**,
**Aguardando retry manual**, **Semiconcluídos**, **Falha definitiva** e
**Concluídos** como botões com `aria-pressed`. Busca e analista continuam sendo
aplicados junto do estado; clicar novamente ou **Limpar filtro** retorna a Todos.

- [ ] **Step 5: Confirmar GREEN e acessibilidade básica**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_durable_job_queue.py tests/test_web_batch_ui.py tests/test_webapp.py`

- [ ] **Step 6: Commit**

```powershell
git add src/tenable_reports/webapp/static/batch_family_filters.js src/tenable_reports/webapp/durable_dashboard_queue.py src/tenable_reports/webapp/server.py src/tenable_reports/webapp/static/index.html src/tenable_reports/webapp/static/app.js src/tenable_reports/webapp/static/app.css tests/test_durable_job_queue.py tests/test_web_batch_ui.py tests/test_webapp.py
git commit -m "feat: agregar e filtrar familias de lotes"
```

### Task 8: Comando headless e tarefa mensal idempotente

**Files:**
- Create: `src/tenable_reports/application/monthly_batch.py`
- Create: `tests/test_monthly_batch.py`
- Modify: `src/tenable_reports/cli.py`
- Modify: `scripts/run_monthly_orchestration.ps1`
- Modify: `scripts/install_monthly_task.ps1`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_orchestration.py`

**Interfaces:**
- Produces: `MonthlyBatchRequest`, `monthly_idempotency_key()`,
  `run_monthly_batch()` e comando `run-monthly-batch`.
- Consumes: a mesma fábrica/coordenador usados pelo servidor.

- [ ] **Step 1: Escrever testes RED de competência e idempotência**

```python
def test_monthly_request_uses_previous_calendar_month_per_client_timezone():
    request = MonthlyBatchRequest(reference_at="2026-09-01T00:05:00-03:00")
    period = request.period_for(profile(timezone="America/Fortaleza"))
    assert period.start_at == "2026-08-01T00:00:00-03:00"
    assert period.end_at == "2026-09-01T00:00:00-03:00"


def test_duplicate_monthly_invocation_returns_same_root_family(repository):
    first = run_monthly_batch(config, repository=repository, start_workers=False)
    second = run_monthly_batch(config, repository=repository, start_workers=False)
    assert first.root_batch_id == second.root_batch_id
    assert first.idempotency_key == "automatic-monthly:carteira-tenable:2026-08"
```

Testar família ativa (assumir/acompanhá-la), família terminal (retornar resultado),
interface fechada, código de saída integral/parcial/falha e clientes ativos apenas.

- [ ] **Step 2: Confirmar RED**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_monthly_batch.py tests/test_cli.py tests/test_orchestration.py -k "monthly or automatic"`

- [ ] **Step 3: Implementar serviço e CLI sem duplicar bootstrap**

```python
def monthly_idempotency_key(orchestration_id: str, competence: str) -> str:
    return f"automatic-monthly:{orchestration_id}:{competence}"


def run_monthly_batch(request: MonthlyBatchRequest, *, coordinator_factory,
                      wait: bool = True) -> MonthlyBatchResult: ...
```

Extrair a criação do repositório/coordenador usada por `serve-web` para função
compartilhada. `run-monthly-batch` cria/localiza a família `STAGED_V1`, inicia
workers, aguarda terminal e encerra apenas seus workers, sem depender do navegador.

- [ ] **Step 4: Atualizar scripts**

`run_monthly_orchestration.ps1` chama `run-monthly-batch --config ...`. O instalador
usa `00:05` por padrão, dia `1`, caminho absoluto resolvido, `-NoProfile` e nenhuma
credencial em argumentos.

- [ ] **Step 5: Confirmar GREEN**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_monthly_batch.py tests/test_cli.py tests/test_orchestration.py`

- [ ] **Step 6: Commit**

```powershell
git add src/tenable_reports/application/monthly_batch.py src/tenable_reports/cli.py scripts/run_monthly_orchestration.ps1 scripts/install_monthly_task.ps1 tests/test_monthly_batch.py tests/test_cli.py tests/test_orchestration.py
git commit -m "feat: executar lote mensal duravel sem interface"
```

### Task 9: Configuração mensal e adaptador do Agendador do Windows

**Files:**
- Create: `src/tenable_reports/config/monthly_schedule.py`
- Create: `src/tenable_reports/application/monthly_schedule.py`
- Create: `src/tenable_reports/infrastructure/windows_task_scheduler.py`
- Create: `tests/test_monthly_schedule.py`
- Create: `tests/test_windows_task_scheduler.py`
- Modify: `src/tenable_reports/webapp/server.py`
- Modify: `tests/test_webapp.py`

**Interfaces:**
- Produces: `MonthlyScheduleConfig`, `WindowsTaskStatus`, `WindowsTaskState`,
  `MonthlyScheduleService.status()/validate()/save()/apply()/set_enabled()` e rotas
  Admin.
- Consumes: `DashboardConfigStore`, cálculo da Task 8 e runner injetável.

- [ ] **Step 1: Escrever testes RED do schema e do adaptador**

```python
def test_monthly_schedule_defaults_are_safe_and_inactive():
    config = MonthlyScheduleConfig.from_mapping({})
    assert config.enabled is False
    assert config.day_of_month == 1
    assert config.local_start_time == time(0, 5)
    assert config.task_name == "Relatorios Tenable - Mensal"


def test_save_does_not_invoke_windows_or_create_batch(service, runner):
    service.save({"enabled": True, "day_of_month": 1, "local_start_time": "00:05"})
    assert runner.calls == []
    assert service.batch_repository.list_batches() == ()


def test_existing_different_command_is_reported_divergent(scheduler):
    scheduler.runner.result = schtasks_query(command="powershell.exe -File C:\\Outro\\x.ps1")
    assert scheduler.query(EXPECTED_CONFIG).status is WindowsTaskStatus.DIVERGENT
```

Cobrir horário inválido, dia diferente de `1`, tarefa ausente, desabilitada,
sincronizada, erro de consulta, confirmação incorreta, permissão negada e duas
sincronizações concorrentes.

- [ ] **Step 2: Confirmar RED**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_monthly_schedule.py tests/test_windows_task_scheduler.py tests/test_webapp.py -k "monthly_schedule or scheduler"`

- [ ] **Step 3: Implementar configuração atômica no arquivo da carteira**

```python
@dataclass(frozen=True, slots=True)
class MonthlyScheduleConfig:
    enabled: bool = False
    day_of_month: int = 1
    local_start_time: time = time(0, 5)
    task_name: str = "Relatorios Tenable - Mensal"
```

`DashboardConfigStore.monthly_schedule()` lê o bloco ausente como padrão inativo;
`save_monthly_schedule()` mantém `clients`/`defaults`, escreve via
`write_json_atomic()` e nunca toca credenciais.

- [ ] **Step 4: Implementar adaptador sem shell**

```python
class WindowsTaskScheduler:
    def query(self, config: MonthlyScheduleConfig) -> WindowsTaskState: ...
    def apply(self, config: MonthlyScheduleConfig) -> WindowsTaskState: ...
    def set_enabled(self, config: MonthlyScheduleConfig, enabled: bool) -> WindowsTaskState: ...
```

Usar `subprocess.run([...], shell=False, timeout=30, capture_output=True)`. Aceitar
somente o nome canônico, resolver `scripts/run_monthly_orchestration.ps1` dentro da
raiz e comparar comando/horário antes de declarar `SYNCHRONIZED`.

- [ ] **Step 5: Expor rotas com confirmação**

```text
GET   /api/admin/monthly-schedule
PATCH /api/admin/monthly-schedule
POST  /api/admin/monthly-schedule/validate
POST  /api/admin/monthly-schedule/apply
POST  /api/admin/monthly-schedule/enable
POST  /api/admin/monthly-schedule/disable
```

`validate` retorna competência, chave idempotente, próxima execução e IDs dos
clientes elegíveis. `apply`/`enable`/`disable` exigem `confirmation` igual ao token
retornado pelo GET e registram evento administrativo sanitizado no PostgreSQL.

- [ ] **Step 6: Confirmar GREEN**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_monthly_schedule.py tests/test_windows_task_scheduler.py tests/test_webapp.py`

- [ ] **Step 7: Commit**

```powershell
git add src/tenable_reports/config/monthly_schedule.py src/tenable_reports/application/monthly_schedule.py src/tenable_reports/infrastructure/windows_task_scheduler.py src/tenable_reports/webapp/server.py tests/test_monthly_schedule.py tests/test_windows_task_scheduler.py tests/test_webapp.py
git commit -m "feat: configurar agendamento mensal no backend"
```

### Task 10: Tela Admin de Automação mensal

**Files:**
- Create: `src/tenable_reports/webapp/static/monthly_schedule.js`
- Modify: `src/tenable_reports/webapp/static/index.html`
- Modify: `src/tenable_reports/webapp/static/app.js`
- Modify: `src/tenable_reports/webapp/static/app.css`
- Modify: `tests/test_web_batch_ui.py`
- Modify: `tests/test_webapp.py`

**Interfaces:**
- Consumes: rotas da Task 9.
- Produces: abas **Automação mensal**/**Referências históricas**, status, formulário,
  validação e ações confirmadas.

- [ ] **Step 1: Escrever testes RED do view-model e do markup**

```javascript
return helpers.scheduleView({
  config: {enabled: true, day_of_month: 1, local_start_time: "00:05"},
  windows_task: {status: "DIVERGENT"},
  next_run_at: "2026-10-01T00:05:00-03:00",
  eligible_client_count: 24,
});
```

O teste espera `policyLabel="Ativa"`, `taskLabel="Divergente"`, próxima execução e
`eligibleClientCopy="24 clientes elegíveis"`. O teste estático exige IDs
`admin-monthly-tab`, `monthly-enabled`, `monthly-start-time`,
`monthly-validate-button`, `monthly-apply-button`, `monthly-toggle-button`,
`monthly-next-run` e carregamento de `monthly_schedule.js` antes de `app.js`.

- [ ] **Step 2: Confirmar RED**

Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_web_batch_ui.py tests/test_webapp.py -k "monthly or admin"`

- [ ] **Step 3: Implementar abas, formulário e estados**

A aba mostra política salva, tarefa Windows, dia fixo, hora editável, fuso local,
próxima/última execução, competência, clientes elegíveis e resumo 10h + 10h + 10h
condicional. O backfill existente permanece na segunda aba sem mudança funcional.

- [ ] **Step 4: Implementar ações sem bloqueio silencioso**

**Salvar** faz PATCH e atualiza o estado local; **Validar sem executar** exibe o
preview; **Aplicar**, **Ativar** e **Desativar** usam `window.confirm`, desabilitam
o botão enquanto aguardam e mostram erro de permissão com o comando administrativo
sanitizado. Nenhuma ação usa o endpoint de gerar lote.

- [ ] **Step 5: Confirmar GREEN e inspeção responsiva**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_web_batch_ui.py tests/test_webapp.py
```

Depois, iniciar apenas a interface necessária para QA, abrir o Admin e inspecionar
desktop e largura móvel. Não clicar em **Aplicar/Ativar/Desativar** durante QA sem
confirmação explícita para alterar a tarefa do Windows.

- [ ] **Step 6: Commit**

```powershell
git add src/tenable_reports/webapp/static/monthly_schedule.js src/tenable_reports/webapp/static/index.html src/tenable_reports/webapp/static/app.js src/tenable_reports/webapp/static/app.css tests/test_web_batch_ui.py tests/test_webapp.py
git commit -m "feat: adicionar automacao mensal ao painel admin"
```

### Task 11: Documentação vigente, verificação final e rollout controlado

**Files:**
- Modify: `README.md`
- Modify: `DESIGN.md`
- Modify: `docs/19-visao-geral-e-objetivos.md`
- Modify: `docs/20-arquitetura-e-fluxo-de-dados.md`
- Modify: `docs/22-guia-operacional.md`
- Modify: `docs/23-guia-de-desenvolvimento.md`
- Modify: `orchestration/clients.example.json`
- Modify: `.agents/skills/operating-tenable-reports/references/runbook.md`
- Modify only if routing changes: `.agents/skills/operating-tenable-reports/SKILL.md`

**Interfaces:**
- Consumes: comportamento efetivamente entregue nas Tasks 1–10.
- Produces: documentação operacional e de desenvolvimento coerente com a versão.

- [ ] **Step 1: Atualizar documentação sem antecipar recurso não validado**

Registrar:

- fluxo único `STAGED_V1` para manual e mensal;
- três componentes, duas janelas comuns e terceira condicional;
- recuperação por UUID/cursor/checkpoint e ausência de cancelamento remoto;
- concorrência remota automática e build serial;
- família de lotes, filtros e retry manual;
- configuração/validação/sincronização pelo Admin;
- diferença entre política salva, tarefa Windows e resultado no PostgreSQL;
- comandos headless e tratamento de falta de privilégio.

Preservar documentos históricos e adicionar nota de substituição nos contratos
antigos incompatíveis.

- [ ] **Step 2: Atualizar exemplo e runbook**

Adicionar ao exemplo:

```json
"monthly_schedule": {
  "enabled": false,
  "day_of_month": 1,
  "local_start_time": "00:05",
  "task_name": "Relatorios Tenable - Mensal"
}
```

O runbook deve conter preparação, preview, sincronização confirmada, observação das
Janelas 1–3, interpretação dos filtros e retry manual após esgotamento.

- [ ] **Step 3: Executar testes focados completos da entrega**

Run:

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
.\.venv\Scripts\python.exe -m pytest -q tests/test_automatic_recovery_policy.py tests/test_web_batch_components_postgresql.py tests/test_component_collection.py tests/test_durable_job_queue.py tests/test_component_retry.py tests/test_monthly_batch.py tests/test_monthly_schedule.py tests/test_windows_task_scheduler.py tests/test_web_batch_ui.py tests/test_webapp.py
```

Expected: PASS sem rede, banco real ou Agendador real.

- [ ] **Step 4: Executar o gate completo do projeto**

Run:

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe tools\validate_project_guidance.py --root .
.\.venv\Scripts\python.exe tools\audit_secret_leaks.py
git diff --check
```

Expected: suíte integral verde, orientação válida, `leaks=0` e diff limpo.

- [ ] **Step 5: Fazer validação operacional sem coleta**

Executar o novo preview/dry-run mensal com a configuração local, confirmar
competência, clientes e idempotência e consultar a tarefa do Windows somente em
modo leitura. Nenhum lote e nenhuma chamada Tenable devem aparecer.

Antes de iniciar a aplicação com a migration `0014` contra o PostgreSQL local,
registrar o status atual do schema e obter autorização explícita para aplicar a
migration. Sincronizar a tarefa oficial e executar homologação real permanecem
gates separados.

- [ ] **Step 6: Commit documental**

```powershell
git add README.md DESIGN.md docs/19-visao-geral-e-objetivos.md docs/20-arquitetura-e-fluxo-de-dados.md docs/22-guia-operacional.md docs/23-guia-de-desenvolvimento.md orchestration/clients.example.json .agents/skills/operating-tenable-reports/references/runbook.md .agents/skills/operating-tenable-reports/SKILL.md
git commit -m "docs: atualizar operacao mensal e recuperacao duravel"
```

- [ ] **Step 7: Parar no gate de operação real**

Apresentar resultados, commits e estado da branch. A tarefa oficial do Windows,
um lote de homologação, merge e push só são executados após autorização explícita.
