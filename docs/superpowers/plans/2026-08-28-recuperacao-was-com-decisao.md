# Recuperação do WAS com decisão do analista — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permitir que uma execução manual aguarde a decisão do analista após falha WAS e que execuções automáticas continuem sem WAS, oferecendo retentativa isolada sem repetir VM.

**Architecture:** A coleta VM produz um checkpoint de retomada antes da decisão WAS. O fluxo manual pode terminar de forma controlada em `WAITING_WAS_DECISION`; um comando de retomada usa o checkpoint para continuar sem WAS ou executar somente WAS. Execuções já publicadas usam o snapshot compacto VM e substituição documental atômica para retentar apenas WAS.

**Tech Stack:** Python 3.14, dataclasses, argparse, PostgreSQL, servidor HTTP local, JavaScript sem framework, pytest, DOCX/LibreOffice.

**Spec:** `docs/superpowers/specs/2026-08-28-recuperacao-was-com-decisao-design.md`

## Global Constraints

- Períodos permanecem `[início, fim)` no fuso do cliente.
- VM geral nunca é filtrado por TAG.
- `OPEN`/`REOPENED` usam `last_found`; `FIXED` usa `last_fixed`.
- WAS é opcional e nunca invalida uma coleta VM íntegra.
- Retentativa WAS não chama export de ativos nem export VM.
- Job WAS `reused`, `provided` ou `resumed` nunca é cancelado automaticamente.
- Falha de coleta WAS não pode ser apresentada como população vazia.
- Segredos e dados identificáveis não entram em logs, checkpoints, fixtures ou documentação.
- Dados intermediários pesados só são removidos depois de publicação validada e histórico compacto persistido.
- O padrão editorial dos DOCX não muda; somente a mensagem de indisponibilidade aprovada é usada.
- Usar TDD: teste falhando, execução RED, implementação mínima e execução GREEN.

---

### Task 1: Contrato de recuperação e checkpoint durável

**Files:**
- Create: `src/tenable_reports/application/was_recovery.py`
- Test: `tests/test_was_recovery.py`

**Interfaces:**
- Consumes: `ReportingPeriod.to_dict()`, caminhos de staging e metadados sanitizados do export WAS.
- Produces: `WasFailureDetails`, `WasRecoveryCheckpoint`, `WasRecoveryDecision`, `write_was_recovery_checkpoint(...)`, `load_was_recovery_checkpoint(...)`.

- [ ] **Step 1: Escrever testes falhando para serialização, validação e compatibilidade**

```python
def test_checkpoint_round_trip_preserves_vm_context_without_credentials(tmp_path):
    checkpoint = WasRecoveryCheckpoint(
        schema_version=1,
        run_id="run-1",
        client_id="cliente-a",
        tenant_id="tenant-a",
        execution_type="MANUAL",
        period={"start_at": "2026-07-01T00:00:00-03:00", "end_at": "2026-08-01T00:00:00-03:00"},
        profile_path="clients/managed/cliente-a.json",
        output_root=str(tmp_path),
        include_output=False,
        was_status="UNAVAILABLE",
        was_failure=WasFailureDetails(code="WAS_COLLECTION_UNAVAILABLE", retryable=True),
    )
    path = write_was_recovery_checkpoint(tmp_path / "checkpoint.json", checkpoint)
    assert load_was_recovery_checkpoint(path) == checkpoint
    assert "secret" not in path.read_text(encoding="utf-8").lower()
```

- [ ] **Step 2: Executar RED**

Run: `python -m pytest -q tests/test_was_recovery.py`

Expected: FAIL porque o módulo e os tipos ainda não existem.

- [ ] **Step 3: Implementar modelos imutáveis e escrita atômica**

```python
class WasRecoveryDecision(StrEnum):
    CONTINUE_WITHOUT_WAS = "continue_without_was"
    RETRY_WAS = "retry_was"

@dataclass(frozen=True, slots=True)
class WasFailureDetails:
    code: str
    message: str = ""
    retryable: bool = False
    export_uuid: str | None = None
    origin: str | None = None
    remote_status: str | None = None
    completed_chunks: int = 0
    total_chunks: int = 0
    timeout_phase: str | None = None
    progress_made: bool = False
    safe_cancel_available: bool = False
```

Validar `schema_version == 1`, IDs, período e caminhos. Escrever atomicamente sem credenciais.

- [ ] **Step 4: Executar GREEN**

Run: `python -m pytest -q tests/test_was_recovery.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/tenable_reports/application/was_recovery.py tests/test_was_recovery.py
git commit -m "feat: adiciona checkpoint de recuperacao WAS"
```

---

### Task 2: Propagar falha WAS estruturada e política manual/automática

**Files:**
- Modify: `src/tenable_reports/application/collect_was.py`
- Modify: `src/tenable_reports/application/period_collection.py`
- Modify: `src/tenable_reports/cli.py`
- Modify: `src/tenable_reports/application/orchestration.py`
- Test: `tests/test_collection.py`
- Test: `tests/test_cli.py`
- Test: `tests/test_orchestration.py`

**Interfaces:**
- Consumes: tipos da Task 1.
- Produces: `WasCollectionAttempt.failure`, argumento `--was-failure-policy {continue,wait}` e payload `status=waiting_was_decision`.

- [ ] **Step 1: Escrever testes falhando para metadados e política**

```python
def test_optional_was_timeout_returns_retryable_failure_details():
    attempt = collect_optional_was_snapshot(...)
    assert attempt.failure.export_uuid == "was-job"
    assert attempt.failure.remote_status == "PROCESSING"
    assert attempt.failure.retryable is True

def test_manual_wait_policy_stops_before_dataset_and_writes_checkpoint():
    result = command_run_client(args_with(was_failure_policy="wait"))
    assert result == WAS_DECISION_EXIT_CODE
    assert emitted_payload["status"] == "waiting_was_decision"
    assert build_dataset.call_count == 0
```

- [ ] **Step 2: Executar RED**

Run: `python -m pytest -q tests/test_collection.py tests/test_cli.py tests/test_orchestration.py -k "was or failure_policy"`

Expected: FAIL pela ausência de `failure`, política e estado controlado.

- [ ] **Step 3: Implementar falha estruturada**

Adicionar `failure: WasFailureDetails | None` a `WasCollectionAttempt`. Em timeout, copiar UUID, origem, último estado, chunks, fase, progresso e cancelabilidade. HTTP 401/403/404 usa `WAS_NOT_AVAILABLE`; timeout, 429 e 5xx usam `WAS_COLLECTION_UNAVAILABLE`.

- [ ] **Step 4: Implementar política de saída controlada**

Adicionar `--was-failure-policy`. Execução manual web usa `wait`; automática usa `continue`. Após VM normalizado e falha WAS com `wait`, gravar checkpoint antes de dataset, histórico, DOCX e limpeza. `command_run_client` imprime payload sanitizado e usa `WAS_DECISION_EXIT_CODE = 3`.

- [ ] **Step 5: Executar GREEN**

Run: `python -m pytest -q tests/test_collection.py tests/test_cli.py tests/test_orchestration.py`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/tenable_reports/application/collect_was.py src/tenable_reports/application/period_collection.py src/tenable_reports/application/orchestration.py src/tenable_reports/cli.py tests/test_collection.py tests/test_cli.py tests/test_orchestration.py
git commit -m "feat: pausa execucao manual quando WAS requer decisao"
```

---

### Task 3: Retomar a execução sem repetir VM

**Files:**
- Modify: `src/tenable_reports/application/was_recovery.py`
- Modify: `src/tenable_reports/cli.py`
- Modify: `src/tenable_reports/application/publishing.py`
- Test: `tests/test_was_recovery.py`
- Test: `tests/test_cli.py`
- Test: `tests/test_publishing.py`

**Interfaces:**
- Consumes: checkpoint e decisão `continue_without_was` ou `retry_was`.
- Produces: `resume_was_recovery(...)`, comando `resume-was` e publicação a partir do staging VM existente.

- [ ] **Step 1: Escrever testes falhando para continuação e retentativa isolada**

```python
def test_continue_without_was_builds_documents_without_vm_calls():
    result = resume_was_recovery(checkpoint, WasRecoveryDecision.CONTINUE_WITHOUT_WAS, deps)
    deps.collect_assets.assert_not_called()
    deps.collect_vm.assert_not_called()
    assert result.was_collection_status == "UNAVAILABLE"

def test_retry_was_collects_only_was_then_resumes():
    result = resume_was_recovery(checkpoint, WasRecoveryDecision.RETRY_WAS, deps)
    deps.collect_was.assert_called_once()
    deps.collect_assets.assert_not_called()
    deps.collect_vm.assert_not_called()
```

- [ ] **Step 2: Executar RED**

Run: `python -m pytest -q tests/test_was_recovery.py tests/test_cli.py -k "resume_was or continue_without_was"`

Expected: FAIL porque a retomada ainda não existe.

- [ ] **Step 3: Extrair montagem pós-coleta reutilizável**

```python
def _assemble_period_from_existing(
    args: argparse.Namespace,
    *,
    profile: ClientProfile,
    period: ReportingPeriod,
    output_root: Path,
    run_id: str,
    execution_type: str,
    was_collection_status: str,
    warnings: tuple[Mapping[str, Any], ...],
) -> _CollectedPeriodExecution:
    ...
```

A função usa `load_report_dataset_inputs`, datasets gerais/TAG e histórico existentes; não instancia clientes Tenable.

- [ ] **Step 4: Implementar `resume-was`**

O comando recebe checkpoint, decisão, perfil, credenciais, banco e templates. Para `retry_was`, consulta o UUID anterior antes de retomar ou criar nova tentativa. Para `continue_without_was`, não chama API. Ambos retomam a montagem e publicação.

- [ ] **Step 5: Provar ausência de chamadas VM**

Run: `python -m pytest -q tests/test_was_recovery.py tests/test_cli.py tests/test_publishing.py`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/tenable_reports/application/was_recovery.py src/tenable_reports/application/publishing.py src/tenable_reports/cli.py tests/test_was_recovery.py tests/test_cli.py tests/test_publishing.py
git commit -m "feat: retoma relatorio apos decisao WAS"
```

---

### Task 4: Persistência operacional da decisão WAS

**Files:**
- Create: `src/tenable_reports/infrastructure/postgresql_migrations/0008_was_recoveries.sql`
- Create: `src/tenable_reports/infrastructure/was_recovery_postgresql.py`
- Modify: `src/tenable_reports/infrastructure/postgresql.py`
- Test: `tests/test_was_recovery_postgresql.py`
- Test: `tests/test_postgresql.py`

**Interfaces:**
- Consumes: checkpoint e falha estruturada.
- Produces: `PostgresWasRecoveryRepository.upsert`, `.pending`, `.get`, `.record_decision`, `.mark_complete`, `.mark_expired`.

- [ ] **Step 1: Escrever testes falhando**

```python
def test_repository_round_trips_pending_recovery(pg_database):
    repository.upsert(recovery)
    assert repository.get(recovery.run_id).status == "WAITING_WAS_DECISION"

def test_record_decision_is_idempotent(pg_database):
    first = repository.record_decision("run-1", "retry_was", idempotency_key="run-1:retry_was")
    second = repository.record_decision("run-1", "retry_was", idempotency_key="run-1:retry_was")
    assert first == second
```

- [ ] **Step 2: Executar RED**

Run: `python -m pytest -q tests/test_was_recovery_postgresql.py tests/test_postgresql.py -k was_recovery`

Expected: FAIL por migration/repositório ausentes.

- [ ] **Step 3: Criar migration aditiva**

Criar `tenable_reports.was_recoveries` com run/client/tenant, status, checkpoint, UUID/origem/estado, chunks, retentabilidade, cancelabilidade segura, decisão, chave idempotente, metadata JSONB e timestamps.

- [ ] **Step 4: Implementar repositório sanitizado**

Persistir somente identificadores operacionais, caminhos locais e metadados sanitizados. Consultas web sempre filtram cliente.

- [ ] **Step 5: Executar GREEN**

Run: `python -m pytest -q tests/test_was_recovery_postgresql.py tests/test_postgresql.py`

Expected: PASS; integração PostgreSQL pode ser explicitamente skipped sem ambiente de teste.

- [ ] **Step 6: Commit**

```powershell
git add src/tenable_reports/infrastructure/postgresql_migrations/0008_was_recoveries.sql src/tenable_reports/infrastructure/was_recovery_postgresql.py src/tenable_reports/infrastructure/postgresql.py tests/test_was_recovery_postgresql.py tests/test_postgresql.py
git commit -m "feat: persiste recuperacoes WAS"
```

---

### Task 5: Fila, rotas e interface de decisão

**Files:**
- Modify: `src/tenable_reports/webapp/server.py`
- Modify: `src/tenable_reports/webapp/static/app.js`
- Modify: `src/tenable_reports/webapp/static/styles.css`
- Test: `tests/test_webapp.py`
- Test: `tests/test_webapp_historical_ui.py`

**Interfaces:**
- Consumes: recoveries pendentes e comando `resume-was`.
- Produces: `JobQueue.enqueue_was_recovery(...)`, `POST /api/was-recoveries/{run_id}/continue`, `POST /api/was-recoveries/{run_id}/retry`.

- [ ] **Step 1: Escrever testes falhando**

```python
def test_manual_was_timeout_becomes_waiting_decision_not_failed():
    job = queue.run_payload({"status": "waiting_was_decision", "run_id": "run-1"}, returncode=3)
    assert job["status"] == "WAITING_WAS_DECISION"

def test_retry_route_enqueues_only_was_command():
    post("/api/was-recoveries/run-1/retry", {"confirmation": "RETENTAR WAS run-1"})
    assert "resume-was" in observed_command
    assert "run-client" not in observed_command
```

- [ ] **Step 2: Executar RED**

Run: `python -m pytest -q tests/test_webapp.py tests/test_webapp_historical_ui.py -k "was and (decision or retry or waiting)"`

Expected: FAIL.

- [ ] **Step 3: Implementar fila e rotas idempotentes**

`WAITING_WAS_DECISION` não conta como ativo. Validar cliente, checkpoint, estado, confirmação e concorrência. `enqueue_was_recovery` chama apenas `python -m tenable_reports resume-was ...`.

- [ ] **Step 4: Implementar interface**

Mostrar **WAS requer decisão**, UUID, origem, estado, chunks e tempo. Manual pendente recebe **Continuar sem WAS** e **Tentar WAS novamente**. Automática publicada recebe somente retentativa. Códigos `WAS_*` aparecem como **Vulnerabilidades WEB**, nunca TAG.

- [ ] **Step 5: Executar GREEN**

Run: `python -m pytest -q tests/test_webapp.py tests/test_webapp_historical_ui.py`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/tenable_reports/webapp/server.py src/tenable_reports/webapp/static/app.js src/tenable_reports/webapp/static/styles.css tests/test_webapp.py tests/test_webapp_historical_ui.py
git commit -m "feat: adiciona decisao e retentativa WAS na interface"
```

---

### Task 6: Retentativa WAS após publicação automática

**Files:**
- Modify: `src/tenable_reports/application/was_recovery.py`
- Modify: `src/tenable_reports/application/compact_snapshots.py`
- Modify: `src/tenable_reports/application/collection_execution.py`
- Modify: `src/tenable_reports/application/publishing.py`
- Modify: `src/tenable_reports/cli.py`
- Test: `tests/test_was_recovery.py`
- Test: `tests/test_compact_snapshots.py`
- Test: `tests/test_orchestration.py`
- Test: `tests/test_publishing.py`

**Interfaces:**
- Consumes: snapshot compacto do run publicado e novo snapshot WAS.
- Produces: replay VM local e substituição atômica dos documentos afetados.

- [ ] **Step 1: Escrever testes falhando**

```python
def test_published_was_retry_uses_compact_vm_without_live_vm_calls():
    result = retry_published_was(run_context, dependencies)
    assert result.general_collection_repeated is False
    assert result.before_vm_metrics_sha256 == result.after_vm_metrics_sha256

def test_document_replacement_keeps_original_until_validation_passes():
    with pytest.raises(DocumentValidationError):
        replace_documents_atomically(manifest, invalid_candidates)
    assert original_document.read_bytes() == original_bytes
```

- [ ] **Step 2: Executar RED**

Run: `python -m pytest -q tests/test_was_recovery.py tests/test_compact_snapshots.py tests/test_publishing.py -k "published_was_retry or document_replacement"`

Expected: FAIL.

- [ ] **Step 3: Implementar replay compacto**

Usar `CompactFindingSnapshot` e `materialize_compact_snapshot_run` para reconstruir dataset VM local, coletar somente WAS e comparar hash das métricas VM antes/depois. Diferença bloqueia publicação.

- [ ] **Step 4: Implementar substituição atômica**

Gerar base/customizado/TAGs em diretório temporário do mesmo volume, validar DOCX, preservar originais até atualizar manifesto/PostgreSQL e manter Cloud inalterado.

- [ ] **Step 5: Executar GREEN**

Run: `python -m pytest -q tests/test_was_recovery.py tests/test_compact_snapshots.py tests/test_orchestration.py tests/test_publishing.py`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/tenable_reports/application/was_recovery.py src/tenable_reports/application/compact_snapshots.py src/tenable_reports/application/collection_execution.py src/tenable_reports/application/publishing.py src/tenable_reports/cli.py tests/test_was_recovery.py tests/test_compact_snapshots.py tests/test_orchestration.py tests/test_publishing.py
git commit -m "feat: retenta WAS publicado sem repetir VM"
```

---

### Task 7: Mensagem editorial, documentação e validação completa

**Files:**
- Modify: apresentação Word somente nos pontos que renderizam módulos WEB
- Modify: `DESIGN.md`
- Modify: `docs/13-was-fase8.md`
- Modify: `docs/18-main-retentativas-inteligencia-operacao.md`
- Modify: `docs/20-arquitetura-e-fluxo-de-dados.md`
- Modify: `docs/22-guia-operacional.md`
- Modify: `docs/23-guia-de-desenvolvimento.md`
- Modify: `.agents/skills/operating-tenable-reports/references/runbook.md`
- Test: testes DOCX existentes do relatório-base e WAS

**Interfaces:**
- Consumes: `was_collection_status` e falha sanitizada.
- Produces: distinção editorial entre população vazia e coleta indisponível.

- [ ] **Step 1: Escrever teste falhando da mensagem**

```python
def test_was_unavailable_does_not_claim_no_web_vulnerabilities(rendered_docx):
    text = extract_document_text(rendered_docx)
    assert "Não foi possível obter os dados de vulnerabilidades WEB" in text
    assert "não foram identificadas vulnerabilidades WEB" not in text
```

- [ ] **Step 2: Executar RED**

Run: `python -m pytest -q tests -k "was and (docx or unavailable)"`

Expected: FAIL pela mensagem indistinta.

- [ ] **Step 3: Implementar mensagem e atualizar guias**

Usar a frase de indisponibilidade somente quando a fonte falhou. Preservar a mensagem de zero apenas quando a coleta concluiu vazia. Documentar estados, botões, fluxo automático, retentativa, UUID e retenção.

- [ ] **Step 4: Executar testes focados e renderização**

Run: `python -m pytest -q tests -k "was or webapp or publishing"`

Renderizar um DOCX de fixture com WAS indisponível e inspecionar as páginas afetadas com LibreOffice; não usar dados reais.

- [ ] **Step 5: Executar verificação completa**

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
C:\Codex\RelatorioTenableMensalv2\.venv\Scripts\python.exe -m pytest -q
C:\Codex\RelatorioTenableMensalv2\.venv\Scripts\python.exe tools\validate_project_guidance.py --root .
C:\Codex\RelatorioTenableMensalv2\.venv\Scripts\python.exe tools\audit_secret_leaks.py
git diff --check
```

Expected: zero falhas, orientação válida, zero vazamentos e diff íntegro.

- [ ] **Step 6: Commit**

```powershell
git add DESIGN.md docs .agents/skills/operating-tenable-reports/references/runbook.md src/tenable_reports/presentation tests
git commit -m "docs: documenta recuperacao e retentativa WAS"
```

---

## Self-review

- Cobertura: decisão manual, continuação automática, retentativa isolada, cancelamento seguro, persistência, interface, publicação atômica, mensagem editorial e retenção estão em tarefas explícitas.
- Sem placeholders: não há `TBD`, `TODO` ou etapas genéricas sem contrato.
- Tipos: `WasFailureDetails`, `WasRecoveryCheckpoint` e `WasRecoveryDecision` são definidos na Task 1 e reutilizados com os mesmos nomes.
- Escopo: nenhuma métrica VM, TAG ou Cloud é alterada.

