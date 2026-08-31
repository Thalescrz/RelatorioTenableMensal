# Controle Durável de Lotes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tornar a fila de geração de relatórios durável e controlável, permitindo pausar, parar, retomar, repetir somente falhas/interrompidos e iniciar uma nova geração completa sem perder checkpoints de exportação.

**Architecture:** PostgreSQL passa a ser a fonte de verdade dos lotes, itens e eventos; um único despachante local reivindica no máximo um item por vez e executa o comando existente de cliente. Solicitações de pausa e parada são persistidas, enquanto a interrupção da execução atual é cooperativa por arquivo de controle lido pelo processo filho. Reinícios reconciliam itens abandonados como interrompidos e nunca iniciam ou cancelam exportações remotas automaticamente.

**Tech Stack:** Python 3.14, biblioteca padrão HTTP/subprocess/threading, PostgreSQL, HTML/CSS/JavaScript sem framework, pytest.

**Spec:** [2026-08-31-controle-duravel-de-lotes-design.md](../specs/2026-08-31-controle-duravel-de-lotes-design.md)

## Global Constraints

- Trabalhar somente em `codex/controle-lotes-duravel` até a verificação e o fluxo Git final.
- Usar TDD: cada mudança de comportamento começa por um teste que falha pelo motivo esperado.
- Preservar a execução sequencial; não introduzir paralelismo entre clientes.
- Não alterar conteúdo, ordem ou formatação dos DOCX.
- Não cancelar automaticamente jobs remotos Tenable quando o usuário parar um lote.
- Não repetir coleta VM, assets, TAG ou Cloud ao retomar um checkpoint válido.
- Não ler, imprimir ou versionar credenciais nem identificadores reais de clientes/exports.
- Não aplicar migração, reiniciar servidor ou importar o snapshot real enquanto o lote ativo atual não tiver terminado e a janela operacional não estiver autorizada.
- Nos comandos de teste executados dentro do worktree, usar `C:\Codex\RelatorioTenableMensalv2\.venv\Scripts\python.exe` e definir `PYTHONPATH` para o `src` do worktree.

---

## Task 1: Modelar estados e transições do lote sem infraestrutura

**Files:**

- Create: `src/tenable_reports/domain/web_batches.py`
- Create: `src/tenable_reports/application/web_batches.py`
- Create: `tests/test_web_batches.py`

**Interfaces:**

```python
class BatchStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PAUSE_REQUESTED = "PAUSE_REQUESTED"
    PAUSED = "PAUSED"
    STOP_REQUESTED = "STOP_REQUESTED"
    STOPPED = "STOPPED"
    COMPLETED = "COMPLETED"
    COMPLETE_WITH_WARNINGS = "COMPLETE_WITH_WARNINGS"
    PARTIAL_FAILURE = "PARTIAL_FAILURE"


class BatchJobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    COMPLETE_WITH_WARNINGS = "COMPLETE_WITH_WARNINGS"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"
    CANCELLED_BY_USER = "CANCELLED_BY_USER"


RETRYABLE_BATCH_JOB_STATUSES = frozenset({
    BatchJobStatus.FAILED,
    BatchJobStatus.INTERRUPTED,
    BatchJobStatus.CANCELLED_BY_USER,
})
```

- [ ] **Step 1: Escrever testes das transições permitidas e proibidas**

Cobrir: `RUNNING -> PAUSE_REQUESTED -> PAUSED`, `RUNNING -> STOP_REQUESTED -> STOPPED`, retomada de `PAUSED`, terminalidade, e o conjunto exato de itens elegíveis ao retry geral. Confirmar que `SUCCEEDED` e `COMPLETE_WITH_WARNINGS` não entram no retry.

- [ ] **Step 2: Executar o teste e confirmar a falha por módulo ausente**

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
C:\Codex\RelatorioTenableMensalv2\.venv\Scripts\python.exe -m pytest tests/test_web_batches.py -q
```

Esperado: falha de importação para `tenable_reports.domain.web_batches`.

- [ ] **Step 3: Implementar enums, dataclasses imutáveis e funções puras de transição**

```python
def transition_batch(current: BatchStatus, requested: BatchStatus) -> BatchStatus:
    if requested not in ALLOWED_BATCH_TRANSITIONS[current]:
        raise InvalidBatchTransitionError(current=current, requested=requested)
    return requested


def retryable_job_ids(jobs: Iterable[WebBatchJob]) -> tuple[UUID, ...]:
    return tuple(job.id for job in jobs if job.status in RETRYABLE_BATCH_JOB_STATUSES)
```

Incluir `WebBatch`, `WebBatchJob`, `WebBatchEvent`, `InvalidBatchTransitionError` e um protocolo `WebBatchRepository` em `application/web_batches.py`, sem importar PostgreSQL.

- [ ] **Step 4: Executar testes focados e confirmar sucesso**

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
C:\Codex\RelatorioTenableMensalv2\.venv\Scripts\python.exe -m pytest tests/test_web_batches.py -q
```

- [ ] **Step 5: Commit**

```powershell
git add src/tenable_reports/domain/web_batches.py src/tenable_reports/application/web_batches.py tests/test_web_batches.py
git commit -m "feat: modelar estados dos lotes web"
```

---

## Task 2: Persistir lotes, itens e eventos no PostgreSQL

**Files:**

- Create: `src/tenable_reports/infrastructure/postgresql_migrations/0009_web_batches.sql`
- Create: `src/tenable_reports/infrastructure/web_batches_postgresql.py`
- Create: `tests/test_web_batches_postgresql.py`
- Modify: `src/tenable_reports/infrastructure/postgresql.py`

**Interfaces:**

```python
class PostgreSQLWebBatchRepository(WebBatchRepository):
    def create_batch(self, batch: WebBatch, jobs: Sequence[WebBatchJob]) -> None: ...
    def get_batch(self, batch_id: UUID) -> WebBatch | None: ...
    def list_batch_jobs(self, batch_id: UUID) -> list[WebBatchJob]: ...
    def request_action(self, batch_id: UUID, action: BatchAction) -> WebBatch: ...
    def claim_next_job(self, *, worker_id: str) -> WebBatchJob | None: ...
    def complete_job(self, job_id: UUID, result: BatchJobResult) -> None: ...
    def append_event(self, event: WebBatchEvent) -> None: ...
    def reconcile_abandoned_jobs(self, *, active_worker_ids: set[str]) -> int: ...
```

- [ ] **Step 1: Escrever testes do contrato SQL e do repositório**

Cobrir criação atômica do lote e seus itens, ordem estável por `position`, idempotência por `idempotency_key`, unicidade de um cliente dentro do lote, evento append-only, atualização com versão otimista e reivindicação exclusiva. Simular duas conexões e exigir que apenas uma reivindique o mesmo item.

- [ ] **Step 2: Executar os testes e confirmar falha por migração/repositório ausentes**

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
C:\Codex\RelatorioTenableMensalv2\.venv\Scripts\python.exe -m pytest tests/test_web_batches_postgresql.py -q
```

- [ ] **Step 3: Criar a migração aditiva `0009_web_batches.sql`**

```sql
CREATE TABLE web_batches (
    id UUID PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_action TEXT,
    source_batch_id UUID REFERENCES web_batches(id),
    options JSONB NOT NULL DEFAULT '{}'::jsonb,
    version BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL,
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ
);

CREATE TABLE web_batch_jobs (
    id UUID PRIMARY KEY,
    batch_id UUID NOT NULL REFERENCES web_batches(id) ON DELETE CASCADE,
    client_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    status TEXT NOT NULL,
    worker_id TEXT,
    process_id INTEGER,
    control_file TEXT,
    orchestration_run_id TEXT,
    attempt_number INTEGER NOT NULL DEFAULT 1,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_code TEXT,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    UNIQUE (batch_id, client_id),
    UNIQUE (batch_id, position)
);

CREATE TABLE web_batch_events (
    id BIGSERIAL PRIMARY KEY,
    batch_id UUID NOT NULL REFERENCES web_batches(id) ON DELETE CASCADE,
    job_id UUID REFERENCES web_batch_jobs(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL
);
```

Adicionar índices por `(batch_id, position)`, `(status, created_at)` e `(batch_id, created_at)`.

- [ ] **Step 4: Implementar o repositório com transações e claim exclusivo**

Usar `SELECT ... FOR UPDATE SKIP LOCKED` dentro de transação e somente selecionar itens `QUEUED` cujo lote esteja `QUEUED` ou `RUNNING`. Ao reivindicar o primeiro item, marcar lote e item como `RUNNING`, registrar `worker_id` e emitir evento na mesma transação.

- [ ] **Step 5: Executar testes focados**

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
C:\Codex\RelatorioTenableMensalv2\.venv\Scripts\python.exe -m pytest tests/test_web_batches_postgresql.py tests/test_postgresql.py -q
```

- [ ] **Step 6: Commit**

```powershell
git add src/tenable_reports/infrastructure/postgresql_migrations/0009_web_batches.sql src/tenable_reports/infrastructure/web_batches_postgresql.py src/tenable_reports/infrastructure/postgresql.py tests/test_web_batches_postgresql.py
git commit -m "feat: persistir lotes web no postgresql"
```

---

## Task 3: Substituir a fila em memória por um despachante durável e sequencial

**Files:**

- Create: `src/tenable_reports/webapp/job_queue.py`
- Modify: `src/tenable_reports/webapp/server.py`
- Modify: `tests/test_webapp.py`
- Create: `tests/test_durable_job_queue.py`

**Interfaces:**

```python
class DurableJobQueue:
    def enqueue_batch(self, request: CreateBatchRequest) -> WebBatch: ...
    def snapshot(self, batch_id: UUID | None = None) -> dict[str, object]: ...
    def wake(self) -> None: ...
    def close(self) -> None: ...


class ClientProcessRunner:
    def run(self, job: WebBatchJob, command: Sequence[str]) -> BatchJobResult: ...
    def request_interrupt(self, job_id: UUID) -> bool: ...
```

- [ ] **Step 1: Escrever testes de recuperação e serialização**

Cobrir: dados permanecem após recriar `DurableJobQueue`; apenas um cliente fica `RUNNING`; a ordem é `position`; `PAUSE_REQUESTED` impede o próximo claim; lote sem itens pendentes deriva o status final; `close()` não apaga o estado.

- [ ] **Step 2: Executar e confirmar falha pela ausência do despachante**

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
C:\Codex\RelatorioTenableMensalv2\.venv\Scripts\python.exe -m pytest tests/test_durable_job_queue.py tests/test_webapp.py -q
```

- [ ] **Step 3: Extrair o runner existente e implementar o laço durável**

```python
def _work(self) -> None:
    while not self._stopping.is_set():
        job = self._repository.claim_next_job(worker_id=self._worker_id)
        if job is None:
            self._wake_event.wait(self._poll_interval)
            self._wake_event.clear()
            continue
        result = self._runner.run(job, self._command_factory(job))
        self._repository.complete_job(job.id, result)
```

Manter um único thread trabalhador. A memória pode conter apenas referências transitórias a `Popen`; status, ordem e resultado vêm sempre do repositório.

- [ ] **Step 4: Reconciliar inicialização sem repetir trabalho**

Ao subir a aplicação, marcar item `RUNNING` sem processo local pertencente ao worker atual como `INTERRUPTED`, marcar o lote como `PAUSED` e registrar um evento. Não reivindicar outro item desse lote até ação explícita de retomada.

- [ ] **Step 5: Preservar compatibilidade temporária de `/api/jobs`**

Fazer o endpoint existente criar um lote de um cliente e devolver também `batch_id`; adaptar `snapshot()` para manter os campos consumidos pela interface durante a migração.

- [ ] **Step 6: Executar testes focados**

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
C:\Codex\RelatorioTenableMensalv2\.venv\Scripts\python.exe -m pytest tests/test_durable_job_queue.py tests/test_webapp.py -q
```

- [ ] **Step 7: Commit**

```powershell
git add src/tenable_reports/webapp/job_queue.py src/tenable_reports/webapp/server.py tests/test_durable_job_queue.py tests/test_webapp.py
git commit -m "feat: tornar fila web duravel e sequencial"
```

---

## Task 4: Introduzir interrupção cooperativa com checkpoint preservado

**Files:**

- Create: `src/tenable_reports/domain/execution_control.py`
- Create: `src/tenable_reports/application/execution_control.py`
- Modify: `src/tenable_reports/cli.py`
- Modify: `src/tenable_reports/infrastructure/tenable_vm/client.py`
- Modify: `src/tenable_reports/infrastructure/tenable_was/client.py`
- Modify: `src/tenable_reports/infrastructure/tenable_cloud/client.py`
- Modify: `src/tenable_reports/application/collect_cloud.py`
- Create: `tests/test_execution_control.py`
- Modify: `tests/test_vm_client.py`
- Modify: `tests/test_was_client.py`
- Modify: `tests/test_cloud_client.py`
- Modify: `tests/test_cloud_collection.py`
- Modify: `tests/test_cli.py`

**Interfaces:**

```python
class ExecutionInterruptedError(RuntimeError):
    """Raised when a persisted local stop request interrupts a run."""


class FileExecutionControl:
    def __init__(self, path: Path) -> None: ...
    def request_stop(self, *, reason: str) -> None: ...
    def is_stop_requested(self) -> bool: ...
    def raise_if_stop_requested(self) -> None: ...
```

```python
def wait_for_completion(
    self,
    export_uuid: str,
    *,
    cancellation_probe: Callable[[], bool] | None = None,
) -> ExportStatus: ...
```

- [ ] **Step 1: Escrever testes do arquivo de controle e interrupção**

Exigir escrita atômica, leitura tolerante a arquivo ausente, exceção específica e preservação do conteúdo após reinício. No cliente VM, simular `PROCESSING`, solicitar parada e confirmar que nenhuma chamada de cancelamento remoto ocorre.

- [ ] **Step 2: Executar testes e confirmar falhas esperadas**

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
C:\Codex\RelatorioTenableMensalv2\.venv\Scripts\python.exe -m pytest tests/test_execution_control.py tests/test_vm_client.py tests/test_was_client.py tests/test_cloud_client.py tests/test_cloud_collection.py tests/test_cli.py -q
```

- [ ] **Step 3: Implementar o controle local e integrá-lo aos loops de espera**

Verificar o probe antes de cada nova consulta de status, antes de baixar cada chunk e entre páginas GraphQL/Cloud. WAS deve reutilizar o comportamento base do VM sem duplicar cancelamento.

```python
if cancellation_probe is not None and cancellation_probe():
    raise ExecutionInterruptedError(
        f"Execucao interrompida com export {export_uuid} preservado para retomada."
    )
```

- [ ] **Step 4: Adicionar `--job-control-file` ao `run-client`**

Construir uma única instância de `FileExecutionControl`, verificar nos limites entre assets, VM, TAG, WAS, Cloud, renderização e publicação, e passá-la às esperas internas. Mapear `ExecutionInterruptedError` para saída 130 e payload JSON com `status="INTERRUPTED"`, mantendo os manifests parciais.

- [ ] **Step 5: Garantir que a parada não publique documento parcial**

Adicionar teste que interrompe após coleta VM parcial e prova que registro/publicação final não são executados, enquanto `manifest.partial.json` permanece referenciado no resultado.

- [ ] **Step 6: Executar testes focados**

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
C:\Codex\RelatorioTenableMensalv2\.venv\Scripts\python.exe -m pytest tests/test_execution_control.py tests/test_vm_client.py tests/test_was_client.py tests/test_cloud_client.py tests/test_cloud_collection.py tests/test_cli.py -q
```

- [ ] **Step 7: Commit**

```powershell
git add src/tenable_reports/domain/execution_control.py src/tenable_reports/application/execution_control.py src/tenable_reports/cli.py src/tenable_reports/infrastructure/tenable_vm/client.py src/tenable_reports/infrastructure/tenable_was/client.py src/tenable_reports/infrastructure/tenable_cloud/client.py src/tenable_reports/application/collect_cloud.py tests/test_execution_control.py tests/test_vm_client.py tests/test_was_client.py tests/test_cloud_client.py tests/test_cloud_collection.py tests/test_cli.py
git commit -m "feat: interromper coleta preservando checkpoints"
```

---

## Task 5: Implementar pausa, parada e retomada no serviço e na API

**Files:**

- Modify: `src/tenable_reports/application/web_batches.py`
- Modify: `src/tenable_reports/webapp/job_queue.py`
- Modify: `src/tenable_reports/webapp/server.py`
- Modify: `tests/test_web_batches.py`
- Modify: `tests/test_durable_job_queue.py`
- Modify: `tests/test_webapp.py`

**Interfaces:**

```text
POST /api/batches/{batch_id}/pause
POST /api/batches/{batch_id}/resume
POST /api/batches/{batch_id}/stop
GET  /api/batches/{batch_id}
GET  /api/batches
```

- [ ] **Step 1: Escrever testes HTTP e de corrida**

Cobrir confirmação de estado, chamadas idempotentes, `pause` durante item ativo, `pause` sem item ativo, `stop` durante item ativo, `stop` antes do primeiro item e `resume` após reinício. Simular a conclusão do processo exatamente ao mesmo tempo que a parada e exigir apenas um estado terminal.

- [ ] **Step 2: Executar e confirmar respostas 404/405 antes da implementação**

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
C:\Codex\RelatorioTenableMensalv2\.venv\Scripts\python.exe -m pytest tests/test_web_batches.py tests/test_durable_job_queue.py tests/test_webapp.py -q
```

- [ ] **Step 3: Implementar `Pausar após o atual`**

Persistir `PAUSE_REQUESTED`; deixar o item atual terminar; converter para `PAUSED` antes de qualquer novo claim. Se não houver item ativo, pausar imediatamente.

- [ ] **Step 4: Implementar `Parar lote`**

Persistir `STOP_REQUESTED`, gravar solicitação no arquivo de controle do item atual e marcar itens ainda `QUEUED` como `CANCELLED_BY_USER`. Após saída 130, marcar o atual `INTERRUPTED` e o lote `STOPPED`. Se o filho não sair após o prazo configurado, encerrar apenas o processo local e registrar `local_process_terminated=true`; nunca chamar cancelamento Tenable nesse fluxo.

- [ ] **Step 5: Implementar `Retomar lote`**

Converter itens `INTERRUPTED` ou `CANCELLED_BY_USER` escolhidos para `QUEUED`, incrementar `attempt_number`, limpar somente campos transitórios do processo e preservar payload/checkpoint. Reativar o lote e acordar o worker.

- [ ] **Step 6: Executar testes focados**

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
C:\Codex\RelatorioTenableMensalv2\.venv\Scripts\python.exe -m pytest tests/test_web_batches.py tests/test_durable_job_queue.py tests/test_webapp.py -q
```

- [ ] **Step 7: Commit**

```powershell
git add src/tenable_reports/application/web_batches.py src/tenable_reports/webapp/job_queue.py src/tenable_reports/webapp/server.py tests/test_web_batches.py tests/test_durable_job_queue.py tests/test_webapp.py
git commit -m "feat: controlar pausa parada e retomada de lotes"
```

---

## Task 6: Implementar retry seletivo e nova geração completa

**Files:**

- Modify: `src/tenable_reports/application/web_batches.py`
- Modify: `src/tenable_reports/infrastructure/web_batches_postgresql.py`
- Modify: `src/tenable_reports/webapp/server.py`
- Modify: `tests/test_web_batches.py`
- Modify: `tests/test_web_batches_postgresql.py`
- Modify: `tests/test_webapp.py`

**Interfaces:**

```text
POST /api/batches/{batch_id}/retry-incomplete
POST /api/batches/{batch_id}/rerun-all
```

```python
@dataclass(frozen=True)
class DerivedBatchRequest:
    source_batch_id: UUID
    kind: Literal["RETRY_INCOMPLETE", "RERUN_ALL"]
    confirmation_token: str | None
```

- [ ] **Step 1: Escrever testes da seleção exata**

Para retry, incluir apenas `FAILED`, `INTERRUPTED` e `CANCELLED_BY_USER`; excluir `SUCCEEDED` e `COMPLETE_WITH_WARNINGS`. Para rerun-all, copiar todos os clientes e opções do lote de origem, exigir confirmação e criar novos IDs. Repetir a mesma requisição com a mesma chave deve devolver o mesmo lote.

- [ ] **Step 2: Escrever teste de conflito por cliente ocupado**

Se qualquer cliente selecionado já estiver `QUEUED` ou `RUNNING` em outro lote, responder HTTP 409 com lista sanitizada de conflitos e não criar lote parcial.

- [ ] **Step 3: Executar e confirmar falha antes da implementação**

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
C:\Codex\RelatorioTenableMensalv2\.venv\Scripts\python.exe -m pytest tests/test_web_batches.py tests/test_web_batches_postgresql.py tests/test_webapp.py -q
```

- [ ] **Step 4: Implementar derivação transacional de lote**

```python
def derive_batch(self, request: DerivedBatchRequest) -> WebBatch:
    selected = (
        self._repository.list_retryable_jobs(request.source_batch_id)
        if request.kind == "RETRY_INCOMPLETE"
        else self._repository.list_batch_jobs(request.source_batch_id)
    )
    if not selected:
        raise NoEligibleBatchJobsError(request.source_batch_id)
    return self._repository.create_derived_batch(request, selected)
```

Copiar período, modo, template, flags WEB/Cloud/TAG e demais opções do lote de origem; não copiar resultados terminais nem caminhos de processo.

- [ ] **Step 5: Executar testes focados**

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
C:\Codex\RelatorioTenableMensalv2\.venv\Scripts\python.exe -m pytest tests/test_web_batches.py tests/test_web_batches_postgresql.py tests/test_webapp.py -q
```

- [ ] **Step 6: Commit**

```powershell
git add src/tenable_reports/application/web_batches.py src/tenable_reports/infrastructure/web_batches_postgresql.py src/tenable_reports/webapp/server.py tests/test_web_batches.py tests/test_web_batches_postgresql.py tests/test_webapp.py
git commit -m "feat: repetir falhas ou lote completo"
```

---

## Task 7: Expor o controle de lotes na interface web

**Files:**

- Modify: `src/tenable_reports/webapp/static/index.html`
- Modify: `src/tenable_reports/webapp/static/app.js`
- Modify: `src/tenable_reports/webapp/static/app.css`
- Modify: `tests/test_webapp.py`
- Modify: `tests/test_webapp_historical_ui.py`

**UI contract:**

- Card do lote com progresso `concluídos / total`, cliente atual, quantidade de falhas/interrompidos e estado persistido.
- Ações contextuais: `Pausar após o atual`, `Parar lote`, `Retomar lote`, `Tentar somente falhas e interrompidos`, `Gerar novamente para todos`.
- Modal de confirmação para parada e nova geração completa; a parada explica que o export remoto será preservado.
- Estado `COMPLETE_WITH_WARNINGS` aparece como aviso, não como falha elegível ao retry geral.

- [ ] **Step 1: Escrever testes de contrato da página e JavaScript**

Verificar presença dos controles, URLs corretas, confirmação, desabilitação durante requisição e mensagens para 409/404. Incluir teste de renderização para lote pausado, parado e concluído com avisos.

- [ ] **Step 2: Executar e confirmar falha antes da marcação existir**

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
C:\Codex\RelatorioTenableMensalv2\.venv\Scripts\python.exe -m pytest tests/test_webapp.py tests/test_webapp_historical_ui.py -q
```

- [ ] **Step 3: Implementar renderização e atualização incremental**

Usar o polling atual de estado, mas renderizar a coleção `batches` proveniente do PostgreSQL. Manter os cards individuais existentes e vinculá-los ao `batch_id`/`job_id` para alertas por cliente.

- [ ] **Step 4: Implementar confirmações e feedback acessível**

Preservar foco do teclado no modal, usar `aria-live` para retorno e bloquear clique duplo enquanto a ação está pendente. Não mostrar stack trace, comando ou segredo.

- [ ] **Step 5: Executar testes e validação visual local sem coleta**

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
C:\Codex\RelatorioTenableMensalv2\.venv\Scripts\python.exe -m pytest tests/test_webapp.py tests/test_webapp_historical_ui.py -q
```

Subir a interface somente com runner fake/fixtures, inspecionar desktop e largura reduzida, e confirmar que nenhuma coleta real é enfileirada.

- [ ] **Step 6: Commit**

```powershell
git add src/tenable_reports/webapp/static/index.html src/tenable_reports/webapp/static/app.js src/tenable_reports/webapp/static/app.css tests/test_webapp.py tests/test_webapp_historical_ui.py
git commit -m "feat: adicionar controle visual de lotes"
```

---

## Task 8: Importar uma vez o snapshot de recuperação do lote atual

**Files:**

- Create: `src/tenable_reports/application/web_batch_recovery_import.py`
- Modify: `src/tenable_reports/infrastructure/web_batches_postgresql.py`
- Modify: `src/tenable_reports/cli.py`
- Create: `tests/test_web_batch_recovery_import.py`
- Modify: `tests/test_cli.py`

**Interfaces:**

```text
python -m tenable_reports import-web-batch-recovery \
  --snapshot C:\Codex\RelatorioTenableMensalv2\data\manual\orchestration\recovery-gerar-todos-20260831T160328Z.json \
  --database-env-file C:\Codex\RelatorioTenableMensalv2\credentials\database.env \
  --dry-run

python -m tenable_reports import-web-batch-recovery \
  --snapshot C:\Codex\RelatorioTenableMensalv2\data\manual\orchestration\recovery-gerar-todos-20260831T160328Z.json \
  --database-env-file C:\Codex\RelatorioTenableMensalv2\credentials\database.env \
  --apply
```

- [ ] **Step 1: Criar fixtures sanitizadas e testes do importador**

Cobrir os estados `complete`, `failed`, `running` e `queued`; mapear o antigo `running` para `INTERRUPTED` e o lote importado para `PAUSED`; rejeitar schema inválido; tornar reaplicação idempotente pelo hash do snapshot.

- [ ] **Step 2: Executar e confirmar falha por comando ausente**

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
C:\Codex\RelatorioTenableMensalv2\.venv\Scripts\python.exe -m pytest tests/test_web_batch_recovery_import.py tests/test_cli.py -q
```

- [ ] **Step 3: Implementar validação, preview e aplicação transacional**

O modo `--dry-run` imprime apenas totais por estado e não altera banco. O modo `--apply` exige banco disponível, cria lote `RECOVERED`, preserva caminhos de manifest/checkpoint existentes e grava evento `RECOVERY_SNAPSHOT_IMPORTED` com hash, nunca com credenciais.

- [ ] **Step 4: Executar testes focados**

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
C:\Codex\RelatorioTenableMensalv2\.venv\Scripts\python.exe -m pytest tests/test_web_batch_recovery_import.py tests/test_cli.py -q
```

- [ ] **Step 5: Commit**

```powershell
git add src/tenable_reports/application/web_batch_recovery_import.py src/tenable_reports/infrastructure/web_batches_postgresql.py src/tenable_reports/cli.py tests/test_web_batch_recovery_import.py tests/test_cli.py
git commit -m "feat: importar lote web em recuperacao"
```

---

## Task 9: Documentar operação, compatibilidade e recuperação

**Files:**

- Modify: `docs/20-arquitetura-e-fluxo-de-dados.md`
- Modify: `docs/22-guia-operacional.md`
- Modify: `docs/23-guia-de-desenvolvimento.md`
- Modify: `.agents/skills/operating-tenable-reports/references/runbook.md`
- Modify: `README.md`
- Modify: `tests/test_project_guidance.py`

- [ ] **Step 1: Escrever/atualizar teste de consistência da documentação**

Exigir menções às ações de lote, estados terminais, preservação de export remoto, comando de importação, rollback e proibição de paralelismo.

- [ ] **Step 2: Executar e confirmar falha até os guias serem atualizados**

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
C:\Codex\RelatorioTenableMensalv2\.venv\Scripts\python.exe -m pytest tests/test_project_guidance.py -q
```

- [ ] **Step 3: Atualizar arquitetura e runbook**

Documentar:

- PostgreSQL como fonte da fila;
- diferença entre pausa, parada e cancelamento remoto de export;
- recuperação após reinício;
- retry geral versus retry específico de WAS/Cloud;
- importação única do snapshot, primeiro em `--dry-run`;
- sequência operacional segura de implantação e rollback;
- indicadores para diagnóstico sem expor identificadores reais.

- [ ] **Step 4: Executar validação dos guias**

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
C:\Codex\RelatorioTenableMensalv2\.venv\Scripts\python.exe -m pytest tests/test_project_guidance.py -q
C:\Codex\RelatorioTenableMensalv2\.venv\Scripts\python.exe tools\validate_project_guidance.py --root .
```

- [ ] **Step 5: Commit**

```powershell
git add README.md docs/20-arquitetura-e-fluxo-de-dados.md docs/22-guia-operacional.md docs/23-guia-de-desenvolvimento.md .agents/skills/operating-tenable-reports/references/runbook.md tests/test_project_guidance.py
git commit -m "docs: registrar operacao dos lotes duraveis"
```

---

## Task 10: Verificação integral e implantação controlada

**Files:**

- Modify only if verification exposes a defect in files already listed above.

- [ ] **Step 1: Executar a suíte completa e as auditorias obrigatórias**

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
C:\Codex\RelatorioTenableMensalv2\.venv\Scripts\python.exe -m pytest -q
C:\Codex\RelatorioTenableMensalv2\.venv\Scripts\python.exe tools\validate_project_guidance.py --root .
C:\Codex\RelatorioTenableMensalv2\.venv\Scripts\python.exe tools\audit_secret_leaks.py
git diff --check
```

- [ ] **Step 2: Fazer autorrevisão explícita contra a especificação**

Verificar um a um: durabilidade, sequencialidade, pausa após atual, parada cooperativa, nenhum cancelamento remoto implícito, retomada por checkpoint, retry seletivo, rerun completo com confirmação, conflitos 409, avisos fora do retry geral, importação idempotente e ausência de mudança nos DOCX.

- [ ] **Step 3: Verificar ausência de marcadores incompletos e inconsistências de tipo**

```powershell
rg -n "TODO|TBD|FIXME|NotImplementedError|pass\s*(#.*)?$" src tests docs README.md
rg -n "COMPLETE_WITH_WARNINGS|COMPLETE_WITH_WARNINGS|CANCELLED_BY_USER|INTERRUPTED" src tests
```

Revisar cada ocorrência intencional de `pass` e alinhar singular/plural dos estados entre domínio, SQL, API e JavaScript.

- [ ] **Step 4: Preparar a janela operacional sem aplicá-la enquanto houver lote vivo**

Confirmar que o lote antigo terminou, parar o servidor antigo, fazer backup lógico das tabelas operacionais, aplicar migrações pelo startup controlado e executar o importador primeiro com `--dry-run`. Somente após validação dos totais, executar `--apply` uma vez.

- [ ] **Step 5: Validar reinício e ações com runner fake**

Criar lote de teste sanitizado, pausar, reiniciar a aplicação, retomar, interromper e repetir somente o item interrompido. Confirmar no PostgreSQL e na interface que o estado sobrevive. Esta etapa não chama Tenable.

- [ ] **Step 6: Teste real mínimo autorizado**

Somente após aprovação explícita, executar um único cliente/período controlado. Durante a espera de export, solicitar pausa e depois parada em execuções separadas; confirmar preservação do UUID/checkpoint nos logs sanitizados e retomada sem repetir coletores já concluídos.

- [ ] **Step 7: Revisar diff e criar commit corretivo apenas se necessário**

```powershell
git status --short
git diff --stat main...HEAD
git diff --check
```

Se a verificação exigir correção, adicionar apenas os arquivos relacionados e usar:

```powershell
git commit -m "fix: concluir verificacao dos lotes duraveis"
```

- [ ] **Step 8: Encerrar pelo fluxo Git aprovado**

Usar `superpowers:finishing-a-development-branch`, confirmar worktree limpo, integrar em `main`, executar novamente a suíte mínima pós-merge, fazer push e remover a branch/worktree de desenvolvimento somente depois do push confirmado.



