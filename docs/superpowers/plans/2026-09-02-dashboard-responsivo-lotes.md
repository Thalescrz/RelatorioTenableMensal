# Dashboard Responsivo e Lotes Observaveis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Impedir que ações como **Adicionar à fila** e **Salvar cliente** fiquem bloqueadas por atualizações lentas, reduzir drasticamente o custo de `/api/state`, tornar clientes/falhas/retentativas verificáveis e recuperar exports VM pelo mesmo UUID durante uma janela total de até 10 horas.

**Architecture:** O navegador passa a coordenar uma única atualização de estado por vez e desacopla a confirmação de mutações da atualização completa do painel. No servidor, lotes, jobs e eventos são carregados em massa uma vez por estado, enquanto a varredura de temporários usa cache curto invalidável. O detalhe dos clientes de um lote é carregado somente sob demanda; códigos de falha e tentativas WAS continuam vindo do histórico durável já existente. O export VM passa a ser uma operação remota durável: UUID e chunks persistidos sobrevivem a timeout/restart, a retentativa consulta o mesmo job e somente os chunks ausentes são baixados.

**Tech Stack:** Python 3.14, PostgreSQL/psycopg, `ThreadingHTTPServer`, JavaScript sem bundler em formato UMD, pytest e Node.js para testes dos helpers.

**Spec:** `docs/superpowers/specs/2026-09-01-coleta-concorrente-renderizacao-serial-design.md`, especialmente objetivos 6/11, seções 5, 8, 9.1, 11, 12 e 15.

## Global Constraints

- Não interromper, reiniciar, retentar ou alterar os jobs reais que estiverem ativos durante o desenvolvimento.
- Trabalhar somente em `codex/dashboard-responsivo-lotes` até validação, fluxo Git completo e remoção da branch.
- PostgreSQL continua sendo a fonte durável de lotes, jobs e eventos; não criar cache de estado operacional no navegador ou em arquivos.
- O endpoint de mutação confirma persistência antes de responder; atualizar o painel é uma operação posterior e não pode manter o botão bloqueado.
- Polling periódico nunca pode executar duas requisições `/api/state` simultâneas nem aplicar resposta mais antiga sobre estado mais novo.
- O detalhe de um lote é carregado sob demanda por `/api/batches/<id>`; a resposta global não recebe todos os eventos históricos.
- Erros, payloads e eventos permanecem sanitizados; nenhuma credencial, hostname, IP, pessoa ou e-mail pode aparecer em logs, testes ou respostas novas.
- Nenhuma regra de período, TAG, WAS, Cloud, `MAIN`, DOCX ou concorrência remota será alterada por este plano.
- Para VM, retentar significa primeiro consultar e recuperar o mesmo `export_uuid`; não significa criar outro export. A política WAS `retry_then_continue` permanece independente.
- O orçamento de espera VM é de 36.000 segundos por UUID, somando fila e processamento e sobrevivendo a reinícios. O timeout de cada requisição HTTP permanece curto.
- Chunks anunciados pela Tenable são baixados imediatamente, mesmo fora de ordem e antes de `FINISHED`, e cada download confirmado é persistido atomicamente.
- `QUEUED`/`PROCESSING` com resposta 200 confirma que a Tenable ainda reconhece o job, mas somente mudança de estado/contadores ou novo chunk confirma progresso real.
- Ao atingir 10 horas, preservar UUID e chunks e terminar como falha temporária recuperável; nunca cancelar automaticamente um export reutilizado.
- Não criar índice PostgreSQL sem `EXPLAIN` demonstrando necessidade; os índices atuais por `batch_id` devem ser reutilizados primeiro. A migration de metadados duráveis do export VM prevista neste plano é justificada pela perda comprovada do vínculo entre job, UUID e `manifest.partial.json` durante falhas.
- Critério operacional: com a base atual, `/api/state` deve responder em até 3 segundos a frio e 1 segundo com cache de armazenamento aquecido; uma chamada lenta nunca bloqueia nova mutação após a confirmação do POST.

---

## Evidência-base da última geração geral

Diagnóstico somente leitura do último lote `GENERATE_ALL`, iniciado em 02/09/2026:

| Classe observada | Jobs | Evidência | Resolução deste plano |
|---|---:|---|---|
| Timeout na fila VM | 18 | Todos preservaram UUID, `manifest.partial.json` e `export-state.json` no disco, mas `collection_checkpoint_path` ficou vazio e o banco não reteve o vínculo de retomada. | Task 8: persistência durável do UUID/manifest, orçamento total de 10 horas e retomada dos chunks ausentes. |
| Manifest normalizado procurado no escopo errado | 3 | O checkpoint aponta artefatos existentes sob `data/manual`, enquanto o build reconstrói `data/normalized`; o `run_id` do checkpoint é coerente e o diretório procurado não existe. | Task 9: raiz de armazenamento derivada do checkpoint e validação completa das dependências antes do build. |
| `WinError 5` ao substituir `export-state.json` | 2 | O alvo é sempre o arquivo de telemetria do export VM/WAS; os diretórios e arquivos existem e são graváveis após a falha, compatível com contenção transitória do Windows. | Task 10: temporário único, `fsync`, retry curto de `os.replace` e isolamento de falha opcional WAS. |
| Autenticação Tenable 401 | 1 | Falha imediata em `/assets/v2/export`, antes de qualquer coleta útil. | Task 7: `TENABLE_AUTH_INVALID`, sem retentativa automática, com orientação para testar/atualizar a API VM. |
| Transporte Tenable | 1 | O job já tinha UUID, chunks persistidos e progresso antes da interrupção. | Tasks 7 e 8: `TENABLE_TEMPORARY`, backoff dentro do orçamento e retomada pelo mesmo UUID. |

O lote terminou com 25 de 25 jobs falhos, nenhum `report_run` vinculado aos seus
`logical_job_id` e nenhum documento publicado. Apesar das causas distintas, todos
os jobs foram gravados como `UNEXPECTED`; essa perda de classificação é tratada
na Task 7. A análise não iniciou, retentou nem cancelou exports.

---

### Task 1: Coordenador single-flight de atualização do navegador

**Files:**
- Create: `src/tenable_reports/webapp/static/dashboard_refresh.js`
- Modify: `src/tenable_reports/webapp/static/index.html:341-343`
- Modify: `src/tenable_reports/webapp/static/app.js:452-455,1353-1357`
- Modify: `tests/test_web_batch_ui.py`

**Interfaces:**
- Produces: `TenableDashboardRefresh.createRefreshCoordinator({ load, apply, onError })`.
- Produces: `coordinator.refresh({ ensureAfterCurrent?: boolean }) -> Promise<void>` e `coordinator.isRunning() -> boolean`.
- Consumes: `api('/api/state')`, `render()` e o tratamento de erro atual do painel.

- [ ] **Step 1: Escrever testes RED do coordenador**

Adicionar `_run_dashboard_refresh_script()` em `tests/test_web_batch_ui.py`, seguindo o padrão dos helpers UMD existentes, e cobrir este contrato determinístico com promises controladas:

```javascript
const calls = [];
let releaseFirst;
const first = new Promise(resolve => { releaseFirst = resolve; });
const coordinator = helpers.createRefreshCoordinator({
  load: () => { calls.push('load'); return calls.length === 1 ? first : Promise.resolve({revision: 2}); },
  apply: value => calls.push(`apply:${value.revision}`),
  onError: error => calls.push(`error:${error.message}`),
});
const a = coordinator.refresh();
const b = coordinator.refresh();
releaseFirst({revision: 1});
await Promise.all([a, b]);
return calls;
```

Esperar somente um `load`. Em outro caso, chamar o segundo refresh com `{ensureAfterCurrent: true}` e esperar exatamente dois `load`, em série, com aplicação `revision:1` antes de `revision:2`. Adicionar caso de rejeição que chama `onError` e libera `isRunning()`.

- [ ] **Step 2: Confirmar RED**

Run:

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
.\.venv\Scripts\python.exe -m pytest tests/test_web_batch_ui.py -q -k "refresh_coordinator"
```

Expected: FAIL porque `dashboard_refresh.js` e `createRefreshCoordinator` ainda não existem.

- [ ] **Step 3: Implementar o helper mínimo**

Criar UMD compatível com Node/navegador. Uma atualização periódica durante outra deve retornar a promise em andamento sem criar nova chamada. Uma mutação confirmada usa `ensureAfterCurrent: true`, marcando uma única atualização subsequente. O loop deve limpar `inFlight` em `finally` e nunca manter mais de um follow-up pendente.

Integrar antes de `app.js` em `index.html`:

```html
<script src="/static/dashboard_refresh.js" defer></script>
```

Em `app.js`, construir o coordenador uma vez e substituir `refresh()` por delegação. O `setInterval` permanece em três segundos, mas chamadas periódicas são coalescidas.

- [ ] **Step 4: Confirmar GREEN e sintaxe JS**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_web_batch_ui.py -q -k "refresh_coordinator or frontend"
node --check src\tenable_reports\webapp\static\dashboard_refresh.js
node --check src\tenable_reports\webapp\static\app.js
```

- [ ] **Step 5: Commit**

```powershell
git add src/tenable_reports/webapp/static/dashboard_refresh.js src/tenable_reports/webapp/static/index.html src/tenable_reports/webapp/static/app.js tests/test_web_batch_ui.py
git commit -m "fix: serializar atualizacoes do dashboard"
```

### Task 2: Liberar ações assim que a mutação for persistida

**Files:**
- Modify: `src/tenable_reports/webapp/static/app.js:1326-1357`
- Modify: `tests/test_web_batch_ui.py`
- Modify: `tests/test_webapp.py`

**Interfaces:**
- Consumes: `coordinator.refresh({ensureAfterCurrent: true})` da Task 1.
- Mantém: `POST /api/jobs` responde `202` somente depois de o lote/job existir no PostgreSQL.
- Produces: feedback imediato com a quantidade persistida e botão reutilizável sem aguardar `/api/state`.

- [ ] **Step 1: Escrever regressão RED para cliente A seguido de cliente B**

No teste JavaScript, extrair o fluxo de submissão para uma função local testável ou verificar o contrato por helper: o primeiro POST resolve, o refresh fica pendente e uma segunda submissão precisa conseguir iniciar antes da liberação do refresh. As expectativas são:

```javascript
return {
  postCalls: ['client-a', 'client-b'],
  firstButtonDisabledAfterPost: false,
  secondPostStartedBeforeRefreshFinished: true,
};
```

Em `tests/test_webapp.py`, confirmar que dois `POST /api/jobs` sequenciais para clientes distintos criam dois `GENERATE_ONE`, ainda que o primeiro job esteja `REMOTE_RUNNING`. Confirmar também que repetir o mesmo cliente responde conflito e não duplica job.

- [ ] **Step 2: Confirmar RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_web_batch_ui.py tests/test_webapp.py -q -k "submit or distinct_client or queue_button"
```

- [ ] **Step 3: Alterar somente o ciclo da interface**

Após `await api('/api/jobs', ...)`:

1. fechar o diálogo;
2. mostrar o toast com `result.jobs.length`;
3. reabilitar o botão imediatamente;
4. solicitar `void refresh(true, {ensureAfterCurrent: true})` sem aguardar o estado completo.

O `catch` continua mostrando erro e o `finally` continua idempotente. Não realizar optimistic insert de job: a fonte visual continua sendo a resposta do estado, enquanto a confirmação do toast representa apenas o POST persistido.

- [ ] **Step 4: Confirmar GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_web_batch_ui.py tests/test_webapp.py -q -k "submit or distinct_client or queue_button"
node --check src\tenable_reports\webapp\static\app.js
```

- [ ] **Step 5: Commit**

```powershell
git add src/tenable_reports/webapp/static/app.js tests/test_web_batch_ui.py tests/test_webapp.py
git commit -m "fix: confirmar inclusao na fila sem bloquear o formulario"
```

### Task 3: Leitura PostgreSQL em massa para lotes, jobs e eventos

**Files:**
- Modify: `src/tenable_reports/application/web_batches.py:275-342`
- Modify: `src/tenable_reports/application/web_batches_memory.py:120-140,651-655`
- Modify: `src/tenable_reports/infrastructure/web_batches_postgresql.py:338-368,1177-1195`
- Modify: `tests/test_web_batches.py`
- Modify: `tests/test_web_batches_postgresql.py`

**Interfaces:**
- Produces: `list_batch_jobs_for_batches(batch_ids: Sequence[UUID]) -> Mapping[UUID, tuple[WebBatchJob, ...]]`.
- Produces: `list_events_for_batches(batch_ids: Sequence[UUID]) -> Mapping[UUID, tuple[WebBatchEvent, ...]]`.
- Mantém: `list_batch_jobs(batch_id)` e `list_events(batch_id)` como wrappers compatíveis.

- [ ] **Step 1: Escrever testes RED de equivalência e quantidade de consultas**

Criar dois lotes com posições e eventos intercalados. Exigir que o resultado em massa preserve:

```python
assert tuple(job.position for job in jobs_by_batch[batch_a]) == (1, 2)
assert tuple(event.event_type for event in events_by_batch[batch_b]) == (
    "JOB_STARTED", "JOB_PROGRESS", "JOB_FINISHED"
)
assert jobs_by_batch[unknown_batch] == ()
assert events_by_batch[unknown_batch] == ()
```

No adapter PostgreSQL fake, afirmar uma única abertura de conexão e uma única execução para todos os IDs, usando `WHERE batch_id = ANY(%s)`. Lista vazia deve retornar `{}` sem conexão.

- [ ] **Step 2: Confirmar RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_web_batches.py tests/test_web_batches_postgresql.py -q -k "for_batches or bulk"
```

- [ ] **Step 3: Implementar consultas em massa**

Adicionar as assinaturas ao protocolo, implementar sob o mesmo lock no repositório em memória e usar uma consulta ordenada no PostgreSQL:

```sql
select <job_columns>
from tenable_reports.web_batch_jobs
where batch_id = any(%s)
order by batch_id, position, id
```

Eventos usam `order by batch_id, created_at, id`. Inicializar no mapping todos os IDs solicitados para manter resultado determinístico.

- [ ] **Step 4: Confirmar GREEN e plano de execução**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_web_batches.py tests/test_web_batches_postgresql.py -q
```

Executar `EXPLAIN (COSTS OFF)` das duas consultas na base local, sem imprimir payloads. Só criar migration de índice se o plano não usar os índices atuais de `batch_id`.

- [ ] **Step 5: Commit**

```powershell
git add src/tenable_reports/application/web_batches.py src/tenable_reports/application/web_batches_memory.py src/tenable_reports/infrastructure/web_batches_postgresql.py tests/test_web_batches.py tests/test_web_batches_postgresql.py
git commit -m "perf: carregar jobs e eventos de lotes em massa"
```

### Task 4: Construir um único snapshot operacional por `/api/state`

**Files:**
- Modify: `src/tenable_reports/webapp/durable_dashboard_queue.py:528-735`
- Modify: `src/tenable_reports/webapp/server.py:2777-2825,3430-3492`
- Modify: `tests/test_durable_job_queue.py`
- Modify: `tests/test_webapp.py`

**Interfaces:**
- Produces: `DashboardQueueSnapshot(jobs, batches, active_job_count)` como dataclass imutável.
- Produces: `DurableDashboardJobQueue.dashboard_snapshot(*, job_batch_limit: int = 500, summary_batch_limit: int = 50) -> DashboardQueueSnapshot`.
- Consumes: métodos em massa da Task 3.
- Mantém: `snapshot()` e `batches_snapshot()` como wrappers para consumidores legados.

- [ ] **Step 1: Escrever RED com repositório contador**

Instrumentar um repositório em memória para contar chamadas. Uma chamada a `dashboard_snapshot()` com sete lotes deve resultar em:

```python
assert calls == {
    "list_batches": 1,
    "list_batch_jobs_for_batches": 1,
    "list_events_for_batches": 1,
}
assert snapshot.active_job_count == 2
assert snapshot.jobs[0]["created_at"] >= snapshot.jobs[-1]["created_at"]
assert len(snapshot.batches) == 7
```

Adicionar teste HTTP que chama `/api/state` e prova que `storage_status` recebe `active_job_count`, sem invocar `jobs.snapshot()` novamente.

- [ ] **Step 2: Confirmar RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_durable_job_queue.py tests/test_webapp.py -q -k "dashboard_snapshot or state_query_count"
```

- [ ] **Step 3: Implementar agregação única**

Carregar até 500 lotes uma vez, obter jobs/eventos em massa, montar os rows de job e os 50 resumos de lote a partir das mesmas estruturas. `DashboardApplication.state()` usa:

```python
queue_state = self.jobs.dashboard_snapshot()
jobs = list(queue_state.jobs)
batches = list(queue_state.batches)
storage = self.storage_status(active_job_count=queue_state.active_job_count)
```

O fallback legado pode continuar usando `snapshot()`/`batches_snapshot()`, mas o executor durável PostgreSQL não pode cair no caminho N+1.

- [ ] **Step 4: Confirmar GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_durable_job_queue.py tests/test_webapp.py -q
```

- [ ] **Step 5: Commit**

```powershell
git add src/tenable_reports/webapp/durable_dashboard_queue.py src/tenable_reports/webapp/server.py tests/test_durable_job_queue.py tests/test_webapp.py
git commit -m "perf: reutilizar snapshot operacional do dashboard"
```

### Task 5: Cache curto e invalidável da varredura de temporários

**Files:**
- Create: `src/tenable_reports/webapp/storage_snapshot.py`
- Modify: `src/tenable_reports/webapp/server.py:2777-2979`
- Create: `tests/test_dashboard_storage_snapshot.py`
- Modify: `tests/test_webapp.py`

**Interfaces:**
- Produces: `TransientStorageSnapshot(temporary_bytes: int, by_client: Mapping[str, int], scanned_at: float)`.
- Produces: `TransientStorageSnapshotCache(ttl_seconds: float = 30.0, monotonic: Callable[[], float] = time.monotonic)`.
- Produces: `cache.get(output_root: Path, client_ids: Sequence[str]) -> TransientStorageSnapshot` e `cache.invalidate() -> None`.
- Consumes: `active_job_count` da Task 4; `shutil.disk_usage` e estado de retenção continuam atuais em cada resposta.

- [ ] **Step 1: Escrever RED com relógio e scanner falsos**

Cobrir:

```python
first = cache.get(root, ("client-a",))
second = cache.get(root, ("client-a",))
assert scanner.calls == 1
clock.advance(31)
third = cache.get(root, ("client-a",))
assert scanner.calls == 2
cache.invalidate()
fourth = cache.get(root, ("client-a",))
assert scanner.calls == 3
```

Adicionar arquivos que desaparecem durante `stat()` e confirmar que são ignorados. Garantir lock para duas threads simultâneas produzirem uma única varredura.

- [ ] **Step 2: Confirmar RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_dashboard_storage_snapshot.py tests/test_webapp.py -q -k "storage_snapshot or storage_cache"
```

- [ ] **Step 3: Implementar cache apenas para a parte cara**

Mover o `rglob` das categorias transitórias para o novo módulo. Não armazenar `disk_usage`, pendências de retenção ou reserva da fila no cache. Invalidar após limpeza aplicada, purge de conjunto ou qualquer operação local que remova staging pelo servidor.

- [ ] **Step 4: Confirmar GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_dashboard_storage_snapshot.py tests/test_webapp.py tests/test_storage_guard.py -q
```

- [ ] **Step 5: Commit**

```powershell
git add src/tenable_reports/webapp/storage_snapshot.py src/tenable_reports/webapp/server.py tests/test_dashboard_storage_snapshot.py tests/test_webapp.py
git commit -m "perf: armazenar snapshot curto dos temporarios"
```

### Task 6: Expor lotes recentes e clientes sob demanda

**Files:**
- Modify: `src/tenable_reports/webapp/static/index.html`
- Modify: `src/tenable_reports/webapp/static/app.js:228-272`
- Modify: `src/tenable_reports/webapp/static/app.css:252-305`
- Modify: `src/tenable_reports/webapp/durable_dashboard_queue.py:650-702`
- Modify: `tests/test_web_batch_ui.py`
- Modify: `tests/test_webapp.py`

**Interfaces:**
- Consumes: `GET /api/batches/<batch_id>` já existente.
- Produces: seletor compacto dos dez lotes recentes e botão **Ver clientes do lote**.
- Produces: detalhe com `client_id`, fase, status, tentativa, código sanitizado e resumo de retentativa WAS derivado dos eventos do próprio job.

- [ ] **Step 1: Escrever RED HTTP/UI**

No HTTP, criar um lote geral terminal e uma geração individual mais nova. Confirmar que `/api/state` oferece ambos nos resumos e que `/api/batches/<geral>` retorna seus jobs. Para os eventos WAS:

```python
assert detail["jobs"][0]["was_attempts"] == 2
assert detail["jobs"][0]["was_retry_performed"] is True
assert detail["jobs"][0]["was_retry_outcome"] == "TIMED_OUT"
```

Na UI, exigir `batch-select`, `batch-client-dialog` e `data-open-batch-clients`. O seletor preserva a escolha do operador durante refresh se o lote ainda existir; caso contrário, escolhe o lote ativo mais recente e depois o lote mais recente.

- [ ] **Step 2: Confirmar RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_web_batch_ui.py tests/test_webapp.py -q -k "batch_detail or batch_selector or was_retry_summary"
```

- [ ] **Step 3: Implementar detalhe sem ampliar `/api/state`**

O resumo global continua sem eventos. Ao clicar **Ver clientes do lote**, chamar uma única vez `/api/batches/<id>` e renderizar linhas compactas. Calcular `was_attempts` no servidor contando eventos `JOB_PROGRESS` com `source='tenable_was_findings'` e `status='STARTED'`; o resultado usa somente estado/origem sanitizados e nunca mostra UUID quando não for necessário para uma ação autorizada.

- [ ] **Step 4: Confirmar GREEN e acessibilidade básica**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_web_batch_ui.py tests/test_webapp.py -q
node --check src\tenable_reports\webapp\static\app.js
```

Verificar teclado, foco ao abrir/fechar o diálogo e rótulos explícitos dos estados.

- [ ] **Step 5: Commit**

```powershell
git add src/tenable_reports/webapp/static/index.html src/tenable_reports/webapp/static/app.js src/tenable_reports/webapp/static/app.css src/tenable_reports/webapp/durable_dashboard_queue.py tests/test_web_batch_ui.py tests/test_webapp.py
git commit -m "feat: detalhar clientes e retries dos lotes"
```

### Task 7: Preservar classificação estruturada das falhas faseadas

**Files:**
- Modify: `src/tenable_reports/application/failures.py`
- Modify: `src/tenable_reports/webapp/durable_dashboard_queue.py:894-946`
- Modify: `tests/test_failures.py`
- Modify: `tests/test_durable_job_queue.py`
- Modify: `tests/test_webapp.py`

**Interfaces:**
- Consumes: `classify_failure(value) -> OperationalFailure` existente.
- Produces: códigos `LOCAL_ARTIFACT_SCOPE_MISMATCH`, `CHECKPOINT_ARTIFACT_MISSING` e `LOCAL_FILESYSTEM_TRANSIENT`.
- Mantém: `TENABLE_TEMPORARY` retryable, `TENABLE_AUTH_INVALID` não retryable e mensagens sanitizadas.

- [ ] **Step 1: Escrever RED a partir das classes e subtipos observados**

Parametrizar:

| Entrada sanitizada | Código esperado | Retryable |
|---|---|---:|
| `Tempo maximo excedido na fila do export VM.` | `TENABLE_TEMPORARY` | sim |
| `endpoint=/assets/v2/export status=401` | `TENABLE_AUTH_INVALID` | não |
| `Falha de transporte ao acessar a Tenable.` | `TENABLE_TEMPORARY` | sim |
| `Nao foi possivel ler o artefato: <path>/manifest.json` com checkpoint em outro escopo | `LOCAL_ARTIFACT_SCOPE_MISMATCH` | não |
| Dependência declarada no checkpoint ausente ou inválida | `CHECKPOINT_ARTIFACT_MISSING` | não |
| `WinError 5 Access is denied` em `export-state.json` | `LOCAL_FILESYSTEM_TRANSIENT` | sim |

Adicionar teste de `_run_executor_job` provando que `result.error_code`/`retryable` estruturados não são substituídos por `UNEXPECTED`.

- [ ] **Step 2: Confirmar RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_failures.py tests/test_durable_job_queue.py tests/test_webapp.py -q -k "classification or structured_failure"
```

- [ ] **Step 3: Aplicar classificação na fronteira correta**

Em `_run_executor_job`, para `FAILED`, executar `failure = classify_failure(result)` e preencher `error_code`, `error_message` e `retryable` no payload a partir de `failure`. Preservar um código estruturado já produzido pela camada inferior. Não iniciar retry automaticamente; a classificação apenas permite que a interface e `retry-incomplete` tomem decisões corretas. A interface deve orientar credenciais/permissões para 401, correção local para escopo/checkpoint e retomada pelo mesmo UUID para timeout/transporte.

- [ ] **Step 4: Confirmar GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_failures.py tests/test_durable_job_queue.py tests/test_webapp.py -q
```

- [ ] **Step 5: Commit**

```powershell
git add src/tenable_reports/application/failures.py src/tenable_reports/webapp/durable_dashboard_queue.py tests/test_failures.py tests/test_durable_job_queue.py tests/test_webapp.py
git commit -m "fix: preservar codigos de falha da execucao faseada"
```

### Task 8: Recuperar o export VM pelo mesmo UUID e esperar até 10 horas

**Files:**
- Modify: `src/tenable_reports/config/environment.py:82-89,133-143`
- Modify: `src/tenable_reports/application/orchestration.py:397-414`
- Modify: `src/tenable_reports/application/collect.py:323-705`
- Modify: `src/tenable_reports/application/period_collection.py:166-198`
- Modify: `src/tenable_reports/infrastructure/tenable_vm/client.py:629-821`
- Modify: `src/tenable_reports/webapp/server.py:462-466,1425-1505,1721-1728,1841-1866`
- Modify: `src/tenable_reports/webapp/durable_dashboard_queue.py:340-430,986-1012`
- Modify: `src/tenable_reports/application/web_batches.py`
- Modify: `src/tenable_reports/infrastructure/web_batches_postgresql.py`
- Create: `src/tenable_reports/infrastructure/postgresql_migrations/0012_vm_export_recovery.sql`
- Modify: `src/tenable_reports/webapp/static/app.js:76-110,790-825`
- Modify: `tests/test_profile_environment.py`
- Modify: `tests/test_orchestration.py`
- Modify: `tests/test_vm_client.py`
- Modify: `tests/test_collection.py`
- Modify: `tests/test_web_batch_derivation.py`
- Modify: `tests/test_durable_job_queue.py`
- Modify: `tests/test_webapp_historical_ui.py`

**Interfaces:**
- Mantém: `GET /vulns/export/{export_uuid}/status` como fonte primária do estado remoto.
- Mantém: `manifest.partial.json` como checkpoint de UUID, query e chunks persistidos.
- Produces: orçamento total de `36_000` segundos por UUID, sem manter uma requisição HTTP aberta.
- Produces: `last_status_ok_at`, `last_progress_at`, `last_remote_status`, `chunks_available`, `persisted_chunks` e `consecutive_status_errors`.
- Produces: `vm_export_uuid`, `vm_resume_manifest_path`, `remote_export_started_at`, `remote_status_at` e `remote_progress_at` como estado durável do job, não apenas eventos ou arquivos órfãos.
- Produces: ação **Verificar export preservado**, que consulta/baixa o UUID existente e nunca inicia um POST novo.

- [ ] **Step 1: Escrever RED para o contrato de tempo total**

Atualizar defaults VM de fila e processamento para 36.000 segundos, mas introduzir
um único prazo durável calculado desde `remote_export_started_at`/primeira
observação do UUID. Com relógio falso, provar:

```python
assert retry.export_uuid == first.export_uuid
assert retry.remaining_wait_seconds == 36_000 - elapsed_before_restart
assert http_request_timeout_seconds < 36_000
```

Uma retentativa ou reinício não pode restaurar o orçamento para 10 horas. O aviso
de estagnação continua ocorrendo antes do teto, mas não encerra a coleta.

- [ ] **Step 2: Escrever RED para retentativa pelo UUID/checkpoint**

Partir de um job falho/interrompido que contenha `vm_export_uuid` e
`manifest.partial.json`. Exigir que o callback de progresso grave ambos
atomicamente no job antes do próximo polling e que `retry-incomplete` preserve ambos no novo
payload/comando. Cobrir:

- status `PROCESSING`: zero chamadas a `POST /vulns/export`;
- status `FINISHED`: concluir download e build com o mesmo UUID;
- status 429/5xx/transporte: preservar UUID e classificar como temporário;
- status 404/expirado ou terminal: registrar por que o UUID é inutilizável antes de
  permitir um novo export;
- origem `provided`/`resumed`: nunca cancelar automaticamente.

O código atual já localiza manifestos compatíveis e encaminha `--vm-export-uuid`
em alguns caminhos; os testes devem fechar as lacunas de derivação e impedir
fallback silencioso para um POST novo.

- [ ] **Step 3: Escrever RED para download incremental fora de ordem**

Com respostas de status sucessivas:

```python
{"status": "PROCESSING", "chunks_available": [2]}
{"status": "PROCESSING", "chunks_available": [2, 0]}
{"status": "FINISHED", "chunks_available": [2, 0, 1]}
```

exigir downloads `[2, 0, 1]`, uma vez cada, e atualização atômica do checkpoint
após cada chunk. Para chunks persistidos `[0, 2]` e disponíveis `[0, 1, 2]`,
baixar somente `1`.

- [ ] **Step 4: Confirmar RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_profile_environment.py tests/test_orchestration.py tests/test_vm_client.py tests/test_collection.py tests/test_web_batch_derivation.py tests/test_durable_job_queue.py tests/test_webapp_historical_ui.py -q -k "36000 or total_budget or preserved_uuid or incremental_chunk or retry_export"
```

Expected: FAIL nos defaults de 1.800/7.200 segundos, na perda do tempo acumulado
entre tentativas e nos fallbacks silenciosos que descartam UUID.

- [ ] **Step 5: Implementar o orçamento durável e o heartbeat**

Persistir timestamps UTC, além dos contadores monotônicos. Cada polling atualiza
`last_status_ok_at` quando a consulta direta retorna 200. Atualizar
`last_progress_at` somente quando:

- o estado remoto avança;
- `finished_chunks`/`completed_chunks` cresce;
- aparece novo `chunk_id`;
- cresce outro contador terminal relevante.

`PROCESSING` sem mudança não atualiza progresso. Polling usa o backoff limitado
já existente (10 a 30/60 segundos) e respeita `Retry-After` para 429 quando
presente. Erros transitórios de polling são absorvidos dentro do orçamento com
contador de falhas consecutivas. Eventos idênticos devem ser coalescidos: persistir
somente em mudança de estado/contador/chunk e um heartbeat no máximo a cada cinco
minutos, evitando multiplicar milhares de eventos durante 10 horas.

- [ ] **Step 6: Reutilizar antes de criar e baixar assim que disponível**

Na entrada da coleta:

1. validar que checkpoint corresponde a cliente, tenant, período, filtros e hash;
2. consultar o UUID preservado;
3. baixar `chunks_available - persisted_chunks`;
4. continuar polling do mesmo UUID se ainda estiver ativo;
5. somente criar novo export quando não existe UUID compatível ou quando o anterior
   está comprovadamente inutilizável e a política permite substituição.

Não limpar `provided_export_uuid`/`resumed_export_uuid` silenciosamente. Retornar
um resultado estruturado que permita à camada operacional decidir sobre um novo
POST. Ao atingir 10 horas, produzir `TENABLE_TEMPORARY`, manter checkpoint e não
cancelar o job remoto.

- [ ] **Step 7: Mostrar existência e progresso como sinais diferentes**

Na interface do lote/cliente exibir:

- UUID abreviado com opção de copiar;
- origem (`created`, `provided` ou `resumed`);
- estado remoto, chunks disponíveis e persistidos;
- “Tenable confirmou este job às HH:MM”;
- “último progresso real há X”;
- para 0/0: “job aceito; a Tenable ainda não anunciou a quantidade de chunks”.

Adicionar **Verificar export preservado** com confirmação. A ação consulta o mesmo
UUID, baixa chunks já disponíveis e retoma o fluxo quando possível; não cria outro
export e não cancela o atual.

- [ ] **Step 8: Confirmar GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_profile_environment.py tests/test_orchestration.py tests/test_vm_client.py tests/test_collection.py tests/test_web_batch_derivation.py tests/test_durable_job_queue.py tests/test_webapp_historical_ui.py -q
node --check src\tenable_reports\webapp\static\app.js
```

- [ ] **Step 9: Commit**

```powershell
git add src/tenable_reports/config/environment.py src/tenable_reports/application/orchestration.py src/tenable_reports/application/collect.py src/tenable_reports/application/period_collection.py src/tenable_reports/application/web_batches.py src/tenable_reports/infrastructure/tenable_vm/client.py src/tenable_reports/infrastructure/web_batches_postgresql.py src/tenable_reports/infrastructure/postgresql_migrations/0012_vm_export_recovery.sql src/tenable_reports/webapp/server.py src/tenable_reports/webapp/durable_dashboard_queue.py src/tenable_reports/webapp/static/app.js tests/test_profile_environment.py tests/test_orchestration.py tests/test_vm_client.py tests/test_collection.py tests/test_web_batch_derivation.py tests/test_durable_job_queue.py tests/test_webapp_historical_ui.py
git commit -m "feat: recuperar export vm por ate dez horas"
```

### Task 9: Corrigir o escopo e fechar o contrato do checkpoint staged

**Files:**
- Modify: `src/tenable_reports/cli.py:879-882,2025-2190,2222-2550`
- Modify: `src/tenable_reports/application/staged_execution.py:198-550`
- Modify: `src/tenable_reports/application/report_dataset.py:141-230`
- Modify: `tests/test_staged_execution.py`
- Modify: `tests/test_cli_collection_routing.py`

**Interfaces:**
- Produces: `checkpoint.storage_scope`/raiz resolvida coerente com `execution_type` e `mode`.
- Mantém: `checkpoint.run_id` como identidade autoritativa da coleta.
- Produces: índice fechado de todos os artefatos necessários ao build: dataset canônico, findings normalizados, manifest normalizado, assets normalizados, snapshots e datasets TAG.

- [ ] **Step 1: Escrever RED reproduzindo o erro do manifest**

Criar checkpoint manual cujos artefatos estejam sob `data/manual` e executar o
build com `--output-root data`. Exigir leitura de
`data/manual/normalized/<cliente>/<run>/manifest.json`, nunca
`data/normalized/...`. Repetir para `automatic-monthly` e provar que uma raiz já
escopada não recebe `manual/manual`.

Adicionar caso em que `job.run_id` difere do `checkpoint.run_id`: o build deve
usar somente o run do checkpoint e não reconstruir caminhos a partir da tentativa
local.

- [ ] **Step 2: Escrever RED para fechamento das dependências**

Exigir que o checkpoint liste e valide hash/tamanho/caminho de todos os arquivos
consumidos depois por `load_report_dataset_inputs`. Se o manifest, assets ou
snapshot estiver ausente, a fase remota não avança para `READY_FOR_BUILD` e
retorna `CHECKPOINT_ARTIFACT_MISSING`; não deve falhar tardiamente como
`Nao foi possivel ler o artefato`.

- [ ] **Step 3: Confirmar RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_staged_execution.py tests/test_cli_collection_routing.py -q -k "scoped or checkpoint or artifact_closure"
```

- [ ] **Step 4: Resolver a raiz uma única vez**

Gravar no checkpoint a raiz/escopo efetivamente usada pela coleta. No build,
validar que ela permanece dentro de `storage_root` e passá-la a history, dataset,
snapshot, publicação e limpeza. Remover reconstruções paralelas baseadas apenas em
`Path(args.output_root)`; não mover nem copiar artefatos para mascarar o erro.

Uma publicação só pode registrar `READY_FOR_CONTROLLED_DISTRIBUTION` após existir
manifest de publicação válido e pelo menos um documento esperado registrado no
PostgreSQL. Falha de build deve manter o job terminal falho sem criar execução
publicável parcial.

- [ ] **Step 5: Confirmar GREEN e commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_staged_execution.py tests/test_cli_collection_routing.py tests/test_postgresql.py -q
```

```powershell
git add src/tenable_reports/cli.py src/tenable_reports/application/staged_execution.py src/tenable_reports/application/report_dataset.py tests/test_staged_execution.py tests/test_cli_collection_routing.py tests/test_postgresql.py
git commit -m "fix: preservar escopo dos artefatos no build staged"
```

### Task 10: Tornar o estado de export resiliente no Windows

**Files:**
- Modify: `src/tenable_reports/application/collect.py:111-133`
- Modify: `src/tenable_reports/application/collect_was.py:62-110`
- Modify: `tests/test_collection.py`
- Modify: `tests/test_was_recovery.py`

**Interfaces:**
- Produces: substituição atômica com temporário único no mesmo diretório, `fsync` e retry limitado para compartilhamento transitório no Windows.
- Mantém: falta de espaço, corrupção e caminhos inválidos como falhas fatais explícitas.
- Mantém: falha local transitória de telemetria WAS não invalida VM já concluído.

- [ ] **Step 1: Escrever RED para contenção do `os.replace`**

Simular `PermissionError`/sharing violation nas duas primeiras substituições de
`export-state.json` e sucesso na terceira. Exigir conteúdo final completo, nenhum
temporário determinístico compartilhado e limpeza dos temporários. Simular erro
persistente e exigir `LOCAL_FILESYSTEM_TRANSIENT` com caminho sanitizado.

- [ ] **Step 2: Escrever RED para isolamento WAS**

Com VM já persistido, provocar contenção transitória apenas no estado WAS. Exigir
que `collect_optional_was_snapshot` produza aviso estruturado e preserve a geração
VM. Não capturar indiscriminadamente `OSError`: `ENOSPC`, hash inválido e corrupção
devem continuar abortando com causa própria.

- [ ] **Step 3: Implementar escrita robusta e confirmar GREEN**

Usar `tempfile.mkstemp` no diretório de destino, escrever/flush/`fsync`, fechar o
descritor e aplicar `os.replace` com backoff curto e limitado apenas aos códigos
transitórios do Windows. Remover o temporário no `finally`.

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_collection.py tests/test_was_recovery.py -q -k "replace or permission or filesystem or optional_was"
```

- [ ] **Step 4: Commit**

```powershell
git add src/tenable_reports/application/collect.py src/tenable_reports/application/collect_was.py tests/test_collection.py tests/test_was_recovery.py
git commit -m "fix: resistir a contencao de arquivos de progresso"
```

### Task 11: Verificação operacional, documentação e fluxo Git

**Files:**
- Modify: `docs/22-guia-operacional.md`
- Modify: `docs/23-guia-de-desenvolvimento.md`
- Modify: `.agents/skills/operating-tenable-reports/references/runbook.md`
- Test: suíte completa, benchmark local e interface.

**Interfaces:**
- Documenta: confirmação assíncrona, leitura do lote, diferença entre retry WAS interno e retomada VM pelo mesmo UUID, cache de armazenamento, diagnóstico de lentidão e os sinais públicos de atividade do export Tenable.

- [ ] **Step 1: Atualizar guias sem apagar decisões históricas**

Registrar que:

- toast **adicionado à fila** significa persistência concluída;
- atualização visual pode chegar depois sem bloquear novo cliente;
- polling é single-flight;
- detalhes de lote são carregados sob demanda;
- retry WAS continua com sua política própria; retentativa VM primeiro reutiliza UUID/checkpoint e só permite novo export após comprovar que o anterior é inutilizável;
- 10 horas representam o teto total local por UUID, não uma garantia de conclusão da Tenable nem o timeout de uma única requisição HTTP;
- resposta 200 em `QUEUED`/`PROCESSING` confirma existência do job; somente mudança de estado/contadores ou chunk novo confirma progresso;
- chunks VM ficam disponíveis por até 24 horas após serem criados e devem ser persistidos imediatamente;
- `API ao vivo · Falhou` descreve o modo e o resultado, não prova falha de credencial.

- [ ] **Step 2: Executar regressão focada**

Run:

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
.\.venv\Scripts\python.exe -m pytest tests/test_web_batch_ui.py tests/test_webapp.py tests/test_durable_job_queue.py tests/test_web_batches.py tests/test_web_batches_postgresql.py tests/test_dashboard_storage_snapshot.py tests/test_failures.py tests/test_staged_execution.py tests/test_cli_collection_routing.py tests/test_collection.py tests/test_was_recovery.py -q
```

- [ ] **Step 3: Executar verificação integral obrigatória**

Run:

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe tools\validate_project_guidance.py --root .
.\.venv\Scripts\python.exe tools\audit_secret_leaks.py
node --check src\tenable_reports\webapp\static\dashboard_refresh.js
node --check src\tenable_reports\webapp\static\app.js
git diff --check
```

- [ ] **Step 4: Validar a interface sem API Tenable**

Com servidor local isolado/fake e banco de teste:

1. atrasar `/api/state` por dez segundos;
2. adicionar cliente A e confirmar que o botão libera após o `202`;
3. antes de o estado retornar, adicionar cliente B;
4. confirmar dois jobs persistidos e apenas um `/api/state` simultâneo;
5. abrir o lote geral enquanto uma geração individual mais recente existe;
6. conferir lista, fases, códigos e retry WAS;
7. medir `/api/state` a frio e aquecido, registrando tempos sem payload sensível.

Não usar credenciais reais nem iniciar export. Nenhum DOCX muda nesta entrega, portanto não há gate LibreOffice.

- [ ] **Step 5: Commit da documentação**

```powershell
git add docs/22-guia-operacional.md docs/23-guia-de-desenvolvimento.md .agents/skills/operating-tenable-reports/references/runbook.md
git commit -m "docs: operar dashboard responsivo e lotes detalhados"
```

- [ ] **Step 6: Fluxo Git completo após aprovação final**

Somente depois de todos os gates passarem e de confirmar que nenhuma geração real depende do servidor antigo:

```powershell
git status --short --branch
git switch main
git pull --ff-only
git merge --no-ff codex/dashboard-responsivo-lotes
git push origin main
git branch -d codex/dashboard-responsivo-lotes
```

Reiniciar o servidor uma única vez após o merge, confirmar `GET /api/state = 200` e validar que os jobs duráveis existentes reaparecem sem criação automática de coleta ou retentativa.

## Critérios de aceite consolidados

- O segundo cliente pode ser adicionado enquanto o primeiro executa e enquanto um refresh anterior ainda está pendente.
- Cada cliente continua protegido contra duas execuções simultâneas próprias.
- Nunca existem dois `/api/state` ativos no mesmo navegador.
- Uma resposta antiga não substitui estado mais novo.
- Uma resposta do estado reutiliza um único snapshot de jobs/lotes e não repete a varredura de disco dentro da mesma requisição.
- A consulta PostgreSQL deixa de abrir conexões por lote para jobs e eventos.
- O painel permite selecionar um lote recente e carregar seus clientes sob demanda.
- A interface diferencia retry WAS interno de nova tentativa de job/lote.
- Falhas transitórias Tenable deixam de aparecer como `UNEXPECTED`; falhas locais permanecem separadas e não geram retry silencioso.
- A montagem staged usa o mesmo escopo `manual`/`automatic-monthly` gravado pela coleta e valida todas as suas dependências antes de entrar em `READY_FOR_BUILD`.
- Nenhum `report_run` fica publicável sem manifest e ao menos um documento esperado registrado.
- Contenção transitória de `export-state.json` é retentada sem descartar chunks; falha opcional WAS não derruba VM já concluído.
- Um 401 é apresentado como credencial/permissão VM inválida e não consome retentativas automáticas.
- Uma retentativa VM com UUID recuperável não envia novo `POST /vulns/export`.
- Chunks disponíveis são baixados uma única vez, inclusive antes de `FINISHED` e fora de ordem.
- A interface distingue “job confirmado pela Tenable” de “progresso real confirmado”.
- O teto de 10 horas sobrevive a reinício/retentativa e termina preservando UUID e chunks, sem cancelamento remoto.
- Polling sem mudança não gera eventos ilimitados; há registro por mudança e heartbeat espaçado.
- A base atual atende aos limites de 3 segundos a frio e 1 segundo aquecido, ou o desvio é documentado com evidência antes de qualquer tuning adicional.
- Nenhum secret ou dado sensível novo aparece na interface, logs, testes ou PostgreSQL.

## Fora de escopo desta entrega

- Garantir que a Tenable conclua o export; a aplicação apenas observa os sinais expostos pela API e recupera o resultado quando disponível.
- Redução automática da concorrência remota ou alteração do teto de 64 clientes.
- Mudança de tamanho de chunk ou propriedades seletivas.
- Mudança editorial ou estrutural nos relatórios DOCX.
- Exclusão, cancelamento ou retomada automática de qualquer lote existente.
- Correção automática de credenciais/permissões Tenable inválidas; a aplicação somente classifica e orienta o operador.

## Rollback

- O helper JavaScript pode ser removido restaurando o `refresh()` anterior; jobs persistidos não são afetados.
- Os métodos em massa são aditivos e os métodos unitários permanecem disponíveis.
- O cache de armazenamento pode ser configurado com TTL zero para comportamento sem cache.
- O detalhe de lote usa endpoint já existente e pode ser ocultado sem migração de dados.
- O teto VM pode voltar ao valor anterior por configuração; UUIDs e checkpoints persistidos permanecem compatíveis.
- A recuperação pode ser desabilitada sem apagar checkpoints; nunca exigir cancelamento remoto como rollback.
- A migration `0012_vm_export_recovery.sql` é aditiva e guarda apenas metadados de recuperação. Seu rollback funcional deixa de preencher/usar essas colunas sem apagar UUIDs ou checkpoints já gravados; nenhum índice novo é esperado.
