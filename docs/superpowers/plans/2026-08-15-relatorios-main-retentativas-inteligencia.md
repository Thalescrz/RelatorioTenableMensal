# Relatórios Main, Retentativas e Inteligência Customizada — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tornar a geração mensal e pontual determinística, recuperável e sustentável, completando os indicadores customizados e permitindo selecionar, excluir e restaurar a referência histórica `main` pela interface.

**Architecture:** Cada execução permanece imutável. Um registro canônico transacional no PostgreSQL aponta para o `main` de cada cliente e competência compatível. O fluxo prepara o dataset com o `main` anterior, gera e valida os DOCX e somente depois publica o snapshot atual e tenta promovê-lo. Coleta, retentativa e retenção são isoladas em componentes próprios; os produtores de inteligência geram dados atuais antes da camada histórica.

**Tech Stack:** Python 3.11+, `psycopg` 3, `python-docx`, Pillow, PostgreSQL, `unittest`/`pytest`, JavaScript sem framework, PowerShell, LibreOffice.

## Global Constraints

- Preservar todos os textos, títulos, tabelas e parágrafos editoriais dos quatro DOCX de referência.
- Tags afetam somente a tabela/comparação da mesma rede em períodos diferentes.
- A severidade informativa permanece desabilitada por padrão.
- Ausência de histórico não pode ser convertida em zero nem usar um mês mais antigo como substituto.
- Uma execução somente pode tornar-se `main` após dataset, dois DOCX e manifesto serem estruturalmente válidos.
- Credenciais, tokens, hostnames, IPs e dados pessoais não podem vazar em filtros, erros ou auditoria.
- Os testes automatizados não podem iniciar exports reais da Tenable.
- A pasta atual não é um repositório Git. Não executar `git init` automaticamente; os passos de commit só se aplicam depois que o usuário inicializar ou fornecer um repositório.

---

## Mapa de arquivos

### Novos arquivos

- `src/tenable_reports/domain/report_reference.py`: regras puras de identidade, elegibilidade e predecessor imediato.
- `src/tenable_reports/application/report_registry.py`: portas e casos de uso para `main`, exclusão lógica e restauração.
- `src/tenable_reports/infrastructure/report_registry_postgresql.py`: implementação transacional no PostgreSQL.
- `src/tenable_reports/infrastructure/postgresql_migrations/0002_report_main_and_attempts.sql`: esquema canônico e soft delete.
- `src/tenable_reports/application/storage_guard.py`: estimativa, reserva e verificação contínua de disco.
- `src/tenable_reports/application/failures.py`: códigos estruturados e classificação de retentativa.
- `src/tenable_reports/application/current_intelligence.py`: orquestra produtores VM/WAS do período atual.
- `src/tenable_reports/domain/intelligence.py`: agregadores puros de saúde, famílias, EOL e vetores.
- `src/tenable_reports/catalogs/unsupported_signals_v1.json`: sinais explícitos e versionados de software/tecnologia sem suporte.
- `src/tenable_reports/presentation/report_filenames.py`: nomes mensais e intervalos personalizados.
- `src/tenable_reports/presentation/source_filters.py`: texto sanitizado dos filtros por tabela.
- `tests/test_report_reference.py`, `tests/test_report_registry.py`, `tests/test_storage_guard.py`, `tests/test_failures.py`, `tests/test_intelligence.py`, `tests/test_report_filenames.py`, `tests/test_source_filters.py`: testes focados.

### Arquivos modificados

- `src/tenable_reports/application/history.py`: separar preparação e finalização histórica.
- `src/tenable_reports/application/orchestration.py`: tentativas, origem e backoff.
- `src/tenable_reports/application/collect.py`: gravação gzip atômica e retomada.
- `src/tenable_reports/application/normalization.py`: consumir chunks sequencialmente.
- `src/tenable_reports/application/retention.py`: política por categoria e proteção canônica.
- `src/tenable_reports/config/profile.py`: `show_source_filters`.
- `src/tenable_reports/domain/normalization.py`: vetor CVSS normalizado.
- `src/tenable_reports/domain/report_dataset.py`: inteligência atual e proveniência por tabela.
- `pyproject.toml`: empacotamento do catálogo versionado de inteligência.
- `src/tenable_reports/infrastructure/tenable_vm/client.py`: download iterável.
- `src/tenable_reports/infrastructure/tenable_vm/parser.py`: parser incremental.
- `src/tenable_reports/infrastructure/postgresql.py`: status da migration e integração operacional.
- `src/tenable_reports/presentation/full_base_report_docx.py`: notas de filtro.
- `src/tenable_reports/presentation/customizations_report_docx.py`: primeiro mês e mensagens controladas.
- `src/tenable_reports/cli.py`: ordem transacional, nomes, origem e comandos administrativos.
- `src/tenable_reports/webapp/server.py`: API de relatórios, `main`, exclusão, restauração, disco e retry.
- `src/tenable_reports/webapp/static/index.html`, `app.js`, `app.css`: controles e indicadores.
- `orchestration/clients.example.json`, `README.md` e documentação operacional: novos campos e procedimentos.

---

### Task 1: Domínio da referência canônica

**Files:**
- Create: `src/tenable_reports/domain/report_reference.py`
- Create: `tests/test_report_reference.py`

**Interfaces:**
- Produces: `ReportOrigin`, `ReferenceKind`, `ReportReferenceKey`, `ReportCandidate`, `Eligibility`, `reference_key_for_candidate()`, `expected_predecessor_key()`, `main_eligibility()`.
- Consumes: `ReportingPeriod`, timezone IANA e os identificadores já presentes no dataset.

- [ ] **Step 1: Escrever testes falhando para equivalência mensal, predecessor e período parcial**

```python
def test_manual_full_calendar_month_shares_automatic_reference_key():
    automatic = candidate(
        origin=ReportOrigin.SCHEDULED,
        start_at="2026-07-01T03:00:00Z",
        end_at="2026-08-01T03:00:00Z",
        period_mode="PREVIOUS_CALENDAR_MONTH",
    )
    manual = candidate(
        origin=ReportOrigin.MANUAL,
        start_at="2026-07-01T03:00:00Z",
        end_at="2026-08-01T03:00:00Z",
        period_mode="EXPLICIT_RANGE",
    )
    assert reference_key_for_candidate(automatic) == reference_key_for_candidate(manual)


def test_monthly_predecessor_is_immediately_previous_month():
    current = reference_key_for_candidate(candidate_for_month(2026, 8))
    assert expected_predecessor_key(current).period_key == "2026-07"


def test_partial_manual_period_is_not_eligible_for_monthly_main():
    partial = candidate(
        origin=ReportOrigin.MANUAL,
        start_at="2026-07-15T03:00:00Z",
        end_at="2026-08-01T03:00:00Z",
        period_mode="EXPLICIT_RANGE",
    )
    assert main_eligibility(partial).monthly_eligible is False
```

- [ ] **Step 2: Rodar os testes e confirmar RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_report_reference.py -q`

Expected: FAIL porque `tenable_reports.domain.report_reference` ainda não existe.

- [ ] **Step 3: Implementar tipos imutáveis e normalização da chave**

```python
class ReportOrigin(StrEnum):
    SCHEDULED = "SCHEDULED"
    AUTOMATIC_RETRY = "AUTOMATIC_RETRY"
    MANUAL = "MANUAL"


class ReferenceKind(StrEnum):
    MONTHLY = "MONTHLY"
    EXACT_RANGE = "EXACT_RANGE"


@dataclass(frozen=True, slots=True)
class ReportReferenceKey:
    client_id: str
    tenant_id: str
    kind: ReferenceKind
    period_key: str
    timezone: str
    scope_hash: str
    metric_definition_version: str

    @property
    def stable_key(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

`reference_key_for_candidate()` deve reconhecer mês-calendário pelos limites locais
`dia 1 00:00 → dia 1 00:00`, independentemente de a origem ser manual ou automática.
Para `EXACT_RANGE`, `period_key` deve ser `start_at/end_at` em UTC. O predecessor
mensal subtrai exatamente um mês; o predecessor exato usa uma janela contígua com a
mesma duração e o mesmo modo.

- [ ] **Step 4: Rodar testes focados e a suíte de períodos**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_report_reference.py tests\test_reporting_period.py -q`

Expected: PASS.

- [ ] **Step 5: Commit condicional**

```powershell
git add src/tenable_reports/domain/report_reference.py tests/test_report_reference.py
git commit -m "feat: define canonical report references"
```

Executar somente se a pasta já tiver sido inicializada como repositório pelo usuário.

---

### Task 2: Esquema PostgreSQL e repositório transacional

**Files:**
- Create: `src/tenable_reports/infrastructure/postgresql_migrations/0002_report_main_and_attempts.sql`
- Create: `src/tenable_reports/application/report_registry.py`
- Create: `src/tenable_reports/infrastructure/report_registry_postgresql.py`
- Create: `tests/test_report_registry.py`
- Modify: `src/tenable_reports/infrastructure/postgresql.py`
- Modify: `tests/test_postgresql.py`

**Interfaces:**
- Consumes: `ReportReferenceKey`, `ReportCandidate`, `HistorySnapshot`.
- Produces: protocolo `ReportRegistry` com `promote_main()`, `auto_promote_if_empty()`, `soft_delete()`, `restore()`, `get_main()`, `list_reports()` e `record_reference_event()`.

- [ ] **Step 1: Escrever testes falhando para migration e invariantes**

```python
def test_second_migration_defines_single_main_and_soft_delete():
    sql = migration_text("0002_report_main_and_attempts.sql")
    assert "report_main_references" in sql
    assert "reference_key text primary key" in sql
    assert "deleted_at timestamptz" in sql
    assert "report_reference_events" in sql
    assert "drop constraint if exists history_snapshots_competence_uq" in sql.lower()


def test_promote_main_replaces_reference_in_one_transaction(fake_registry):
    fake_registry.promote_main(KEY, "run-a", actor="analista", reason="primeiro")
    fake_registry.promote_main(KEY, "run-b", actor="analista", reason="dados completos")
    assert fake_registry.get_main(KEY).run_id == "run-b"
    assert [event.new_run_id for event in fake_registry.events] == ["run-a", "run-b"]


def test_main_cannot_be_deleted_without_replacement_or_gap_confirmation(fake_registry):
    fake_registry.promote_main(KEY, "run-a", actor="analista", reason="primeiro")
    with pytest.raises(MainDeletionRequiresDecision):
        fake_registry.soft_delete("run-a", actor="analista", reason="teste")
```

- [ ] **Step 2: Rodar testes e confirmar RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_report_registry.py tests\test_postgresql.py -q`

Expected: FAIL pela ausência da migration e do repositório.

- [ ] **Step 3: Criar a migration aditiva e remover a unicidade antiga de competência**

```sql
alter table tenable_reports.report_runs
    add column if not exists origin text not null default 'MANUAL',
    add column if not exists logical_job_id text,
    add column if not exists attempt_number integer not null default 1,
    add column if not exists period_mode text,
    add column if not exists timezone text,
    add column if not exists scope_hash text,
    add column if not exists metric_definition_version text,
    add column if not exists deleted_at timestamptz,
    add column if not exists deleted_by text,
    add column if not exists deletion_reason text;

alter table tenable_reports.history_snapshots
    drop constraint if exists history_snapshots_competence_uq;

create unique index if not exists history_snapshots_run_uq
    on tenable_reports.history_snapshots(run_id);

create table if not exists tenable_reports.report_main_references (
    reference_key text primary key,
    client_id text not null,
    tenant_id text not null,
    reference_kind text not null,
    period_key text not null,
    timezone text not null,
    scope_hash text not null,
    metric_definition_version text not null,
    run_id text not null unique references tenable_reports.report_runs(run_id) on delete restrict,
    set_by text not null,
    set_reason text not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists tenable_reports.report_reference_events (
    reference_event_id bigint generated always as identity primary key,
    event_type text not null,
    reference_key text,
    previous_run_id text,
    new_run_id text,
    actor text not null,
    reason text not null,
    payload jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);
```

A migration também deve criar índices para `report_runs(client_id, deleted_at,
period_start_at)` e `report_reference_events(reference_key, created_at desc)`.

- [ ] **Step 4: Implementar protocolo, fake de teste e PostgreSQL**

```python
@dataclass(frozen=True, slots=True)
class MainReport:
    reference_key: ReportReferenceKey
    run_id: str
    snapshot: HistorySnapshot


class ReportRegistry(Protocol):
    def get_main(self, key: ReportReferenceKey) -> MainReport | None: ...
    def get_main_snapshot(self, key: ReportReferenceKey) -> HistorySnapshot | None: ...
    def promote_main(self, key: ReportReferenceKey, run_id: str, *, actor: str, reason: str) -> MainReport: ...
    def auto_promote_if_empty(self, key: ReportReferenceKey, run_id: str) -> MainReport: ...
    def soft_delete(self, run_id: str, *, actor: str, reason: str,
                    replacement_run_id: str | None = None,
                    allow_gap: bool = False) -> None: ...
    def restore(self, run_id: str, *, actor: str, reason: str) -> None: ...
```

`PostgresReportRegistry.promote_main()` deve usar `select ... for update` sobre a
chave, validar `status = 'READY_FOR_CONTROLLED_DISTRIBUTION'`, `deleted_at is null` e
a igualdade da chave do candidato antes do `insert ... on conflict ... do update`.
O evento deve ser gravado na mesma transação.

- [ ] **Step 5: Atualizar `PostgresDatabase.status()` e rodar testes**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_report_registry.py tests\test_postgresql.py -q`

Expected: PASS.

- [ ] **Step 6: Commit condicional**

```powershell
git add src/tenable_reports/infrastructure/postgresql_migrations/0002_report_main_and_attempts.sql src/tenable_reports/application/report_registry.py src/tenable_reports/infrastructure/report_registry_postgresql.py src/tenable_reports/infrastructure/postgresql.py tests/test_report_registry.py tests/test_postgresql.py
git commit -m "feat: persist canonical report main references"
```

---

### Task 3: Preparação histórica antes do DOCX e finalização após validação

**Files:**
- Modify: `src/tenable_reports/application/history.py`
- Modify: `src/tenable_reports/domain/history.py`
- Modify: `src/tenable_reports/cli.py`
- Modify: `src/tenable_reports/application/publishing.py`
- Modify: `tests/test_history.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_orchestration.py`

**Interfaces:**
- Consumes: `ReportRegistry`, `SnapshotRepository`, `ReportReferenceKey`.
- Produces: `HistoryPreparation` e `finalize_history_publication()`.

- [ ] **Step 1: Escrever testes falhando para lacuna estrita e publicação tardia**

```python
def test_august_does_not_fall_back_to_june_when_july_main_is_missing():
    registry = registry_with_main(month="2026-06", run_id="run-june")
    prepared = prepare_dataset_history(current=dataset_for("2026-08"), registry=registry)
    assert prepared.predecessor is None
    assert prepared.history_status == "NO_IMMEDIATE_MAIN"


def test_snapshot_is_not_published_until_documents_are_valid():
    snapshots = RecordingSnapshotRepository()
    prepared = prepare_dataset_history(..., snapshot_repository=snapshots)
    assert snapshots.published == []
    finalize_history_publication(prepared, publication_validated=True)
    assert [item.run_id for item in snapshots.published] == [prepared.current.run_id]


def test_failed_docx_validation_never_publishes_or_promotes():
    with pytest.raises(InvalidDocxPackage):
        run_client_with_invalid_document(...)
    assert registry.mains == {}
    assert snapshots.published == []


def test_previous_main_derives_top_positive_vulnerability_changes():
    previous = snapshot_with_plugin_counts({1001: ("Plugin A", 20), 1002: ("Plugin B", 30)})
    current = snapshot_with_plugin_counts({1001: ("Plugin A", 50), 1002: ("Plugin B", 10)})
    customizations = merge_history(previous=previous, current=current)["customizations"]
    assert customizations["vulnerability_evolution"] == [
        {"plugin_id": 1001, "label": "Plugin A", "change": 30}
    ]
```

- [ ] **Step 2: Rodar testes e confirmar RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_history.py tests\test_cli.py tests\test_orchestration.py -q`

Expected: FAIL porque o fluxo atual publica o snapshot dentro de
`publish_dataset_history()` antes dos documentos.

- [ ] **Step 3: Separar preparação e finalização**

```python
@dataclass(frozen=True, slots=True)
class HistoryPreparation:
    current: HistorySnapshot
    predecessor: HistorySnapshot | None
    reference_key: ReportReferenceKey
    enriched_dataset_path: Path
    history_status: str


def prepare_dataset_history(..., registry: ReportRegistry) -> HistoryPreparation:
    current = _history_snapshot(..., include_run_id_in_identity=True)
    predecessor_key = expected_predecessor_key(reference_key_for_snapshot(current))
    predecessor = registry.get_main_snapshot(predecessor_key) if predecessor_key else None
    enriched = _merge_customizations(..., current=current, predecessor=predecessor)
    write_enriched_dataset(enriched)
    return HistoryPreparation(...)


def finalize_history_publication(preparation, *, snapshot_repository,
                                 registry, publication_validated,
                                 auto_promote):
    if not publication_validated:
        raise ValueError("Publicacao invalida nao pode entrar no historico.")
    snapshot_repository.publish(preparation.current)
    if auto_promote:
        registry.auto_promote_if_empty(preparation.reference_key,
                                       preparation.current.run_id)
```

O `snapshot_id` deve incluir `run_id`, permitindo múltiplas execuções imutáveis da
mesma competência. A série mensal usada no DOCX deve conter os `main` anteriores e o
candidato atual, mas não outros candidatos não promovidos.

Estender `HistorySnapshot` com a contagem de findings abertos por `plugin_id` e nome.
Quando existir predecessor, derivar `vulnerability_evolution` pelos deltas de contagem,
mantendo os dez maiores aumentos positivos, desempate por `plugin_id`. Reduções não
entram nesse gráfico porque já são representadas nos indicadores de mitigação. Sem
aumento positivo, registrar `NO_OCCURRENCES`; sem predecessor, registrar `NO_HISTORY`.
As evoluções mensal e executiva continuam sendo derivadas de `monthly_history`, que
contém somente referências `main` anteriores mais o candidato atual.

- [ ] **Step 4: Reordenar `command_run_client()`**

A ordem obrigatória é:

1. coletar e normalizar;
2. produzir inteligência atual;
3. preparar dataset com predecessor `main`;
4. gerar os dois DOCX;
5. validar os pacotes e escrever o manifesto;
6. registrar `report_runs` e publicação;
7. publicar snapshot candidato;
8. promover automaticamente somente se a chave ainda não possui `main`.

- [ ] **Step 5: Rodar testes focados e regressões de geração**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_history.py tests\test_cli.py tests\test_orchestration.py tests\test_full_base_report_docx.py tests\test_customizations_report_docx.py -q`

Expected: PASS.

- [ ] **Step 6: Commit condicional**

```powershell
git add src/tenable_reports/application/history.py src/tenable_reports/domain/history.py src/tenable_reports/cli.py src/tenable_reports/application/publishing.py tests/test_history.py tests/test_cli.py tests/test_orchestration.py
git commit -m "feat: finalize history only after valid publication"
```

---

### Task 4: Falhas estruturadas, preflight de disco e retentativas

**Files:**
- Create: `src/tenable_reports/application/failures.py`
- Create: `src/tenable_reports/application/storage_guard.py`
- Create: `tests/test_failures.py`
- Create: `tests/test_storage_guard.py`
- Modify: `src/tenable_reports/application/orchestration.py`
- Modify: `src/tenable_reports/cli.py`
- Modify: `tests/test_orchestration.py`
- Modify: `orchestration/clients.example.json`

**Interfaces:**
- Produces: `OperationalFailure`, `FailureCode`, `classify_failure()`, `StorageRequirement`, `storage_preflight()`, `ClientAttemptResult`.
- Consumes: último tamanho bem-sucedido do registro de artefatos e `shutil.disk_usage()`.

- [ ] **Step 1: Escrever testes falhando para classificação e reserva**

```python
def test_rate_limit_is_retryable_but_invalid_credentials_are_not():
    assert classify_failure({"error_code": "TENABLE_RATE_LIMIT"}).retryable is True
    assert classify_failure({"error_code": "TENABLE_AUTH_INVALID"}).retryable is False


def test_storage_estimate_uses_history_with_floor():
    assert required_free_bytes(last_success_bytes=4 * GIB) == 10 * GIB
    assert required_free_bytes(last_success_bytes=None) == 10 * GIB


def test_disk_preflight_blocks_before_runner_is_called(fake_disk, fake_runner):
    fake_disk.free = 2 * GIB
    result = execute_with_retry(..., runner=fake_runner, disk=fake_disk)
    assert result.status == "WAITING_RETRY"
    assert fake_runner.calls == []
```

O cálculo deve ser `max(10 GiB, ceil(last_success_bytes * 1.5) + 2 GiB)`; no exemplo
de 4 GiB, o piso de 10 GiB prevalece.

- [ ] **Step 2: Rodar testes e confirmar RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_failures.py tests\test_storage_guard.py tests\test_orchestration.py -q`

Expected: FAIL pela ausência dos componentes.

- [ ] **Step 3: Implementar falhas estruturadas e saída sanitizada da CLI**

```python
class FailureCode(StrEnum):
    TENABLE_RATE_LIMIT = "TENABLE_RATE_LIMIT"
    TENABLE_TEMPORARY = "TENABLE_TEMPORARY"
    TENABLE_AUTH_INVALID = "TENABLE_AUTH_INVALID"
    DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
    DISK_INSUFFICIENT = "DISK_INSUFFICIENT"
    DOCX_INVALID = "DOCX_INVALID"
    PROFILE_INVALID = "PROFILE_INVALID"


@dataclass(frozen=True, slots=True)
class OperationalFailure(Exception):
    code: FailureCode
    message: str
    retryable: bool
```

A CLI deve emitir um último JSON sanitizado com `status`, `error_code`, `retryable`
e `message`. Tracebacks completos permanecem somente no log local e passam pelo
redator de segredos existente.

- [ ] **Step 4: Implementar tentativa original + uma retentativa automática**

Adicionar a `OrchestrationConfig`:

```python
retry_max_attempts: int = 2
retry_delay_seconds: int = 900
minimum_free_gb: int = 10
```

Criar `logical_job_id` estável por cliente/competência e `attempt_number` 1 ou 2.
A segunda tentativa usa `origin=AUTOMATIC_RETRY`, mas mantém
`execution_type=AUTOMATIC_MONTHLY`. Injetar `sleeper: Callable[[float], None]` para
testar sem esperar 15 minutos.

- [ ] **Step 5: Testar sucesso na segunda tentativa e falha permanente**

```python
def test_scheduled_transient_failure_succeeds_on_second_attempt():
    runner = SequenceRunner(rate_limit_failure(), success_payload())
    result = run_orchestration(..., runner=runner, sleeper=lambda _: None)
    assert result.clients[0].status == "COMPLETE"
    assert [a.origin for a in result.clients[0].attempts] == ["SCHEDULED", "AUTOMATIC_RETRY"]


def test_invalid_credentials_do_not_retry():
    runner = SequenceRunner(auth_failure())
    result = run_orchestration(..., runner=runner, sleeper=lambda _: None)
    assert len(result.clients[0].attempts) == 1
```

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_failures.py tests\test_storage_guard.py tests\test_orchestration.py -q`

Expected: PASS.

- [ ] **Step 6: Commit condicional**

```powershell
git add src/tenable_reports/application/failures.py src/tenable_reports/application/storage_guard.py src/tenable_reports/application/orchestration.py src/tenable_reports/cli.py orchestration/clients.example.json tests/test_failures.py tests/test_storage_guard.py tests/test_orchestration.py
git commit -m "feat: add scheduled retries and disk preflight"
```

---

### Task 5: Download gzip atômico e retomada de chunks

**Files:**
- Modify: `src/tenable_reports/infrastructure/tenable_vm/client.py`
- Modify: `src/tenable_reports/infrastructure/tenable_vm/parser.py`
- Modify: `src/tenable_reports/application/collect.py`
- Modify: `src/tenable_reports/application/normalization.py`
- Modify: `tests/test_vm_client.py`
- Modify: `tests/test_collection.py`
- Modify: `tests/test_normalization.py`

**Interfaces:**
- Produces: `iter_chunk_bytes()`, `iter_asset_chunk_bytes()`, `iter_chunk_records()`, `StoredChunk`.
- Consumes: `storage_preflight()` antes de cada chunk e manifests já existentes para retomada.

- [ ] **Step 1: Escrever testes falhando para gzip, `.partial` e retomada**

```python
def test_plain_jsonl_is_persisted_as_valid_gzip(tmp_path):
    result = store_chunk_atomic(tmp_path, [b'{"id":1}\n', b'{"id":2}\n'])
    assert result.path.suffixes[-2:] == [".jsonl", ".gz"]
    with gzip.open(result.path, "rb") as stream:
        assert stream.read() == b'{"id":1}\n{"id":2}\n'
    assert not list(tmp_path.glob("*.partial"))


def test_invalid_partial_chunk_is_not_reused(tmp_path):
    partial = tmp_path / "chunk-000001.jsonl.gz.partial"
    partial.write_bytes(b"broken")
    assert reusable_chunk(partial, expected_sha256="abc") is None


def test_valid_manifest_chunk_skips_network_download(tmp_path, fake_client):
    existing = write_valid_chunk_and_manifest(tmp_path)
    result = collect_vm_snapshot(..., resume_from=existing.manifest)
    assert fake_client.download_calls == []
    assert result.raw_manifest_path.is_file()
```

- [ ] **Step 2: Rodar testes e confirmar RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_vm_client.py tests\test_collection.py tests\test_normalization.py -q`

Expected: FAIL porque o cliente devolve `bytes` completos e a coleta grava JSONL sem compactação.

- [ ] **Step 3: Implementar download iterável e gravação atômica**

```python
@dataclass(frozen=True, slots=True)
class StoredChunk:
    chunk_id: int
    path: Path
    stored_bytes: int
    record_count: int
    content_sha256: str
    storage_sha256: str
    encoding: str = "gzip"
```

`iter_chunk_bytes()` deve ler blocos de 1 MiB da resposta HTTP. A coleta grava em
`*.jsonl.gz.partial`, calcula hash do conteúdo original e do arquivo armazenado,
executa `flush()` e `os.fsync()`, valida o gzip e usa `Path.replace()` para promover.
O manifest registra ambos os hashes e `complete: true`.

- [ ] **Step 4: Fazer a normalização consumir chunks sequencialmente**

`CollectionResult` deixa de reter todos os conteúdos brutos em `chunks`. A
normalização abre os caminhos registrados no manifest, usa `gzip.open()` e entrega
registros ao normalizador um chunk por vez. O dicionário de ativos e o conjunto de
`finding_key` continuam em memória, mas os bytes brutos e todos os chunks não.

- [ ] **Step 5: Verificar RED/GREEN e compatibilidade com fixtures antigas**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_vm_client.py tests\test_collection.py tests\test_normalization.py tests\test_cli.py -q`

Expected: PASS para manifests `.jsonl` antigos e novos `.jsonl.gz`.

- [ ] **Step 6: Commit condicional**

```powershell
git add src/tenable_reports/infrastructure/tenable_vm/client.py src/tenable_reports/infrastructure/tenable_vm/parser.py src/tenable_reports/application/collect.py src/tenable_reports/application/normalization.py tests/test_vm_client.py tests/test_collection.py tests/test_normalization.py
git commit -m "feat: store resumable Tenable chunks as gzip"
```

---

### Task 6: Retenção por categoria e proteção de referência

**Files:**
- Modify: `src/tenable_reports/application/retention.py`
- Modify: `src/tenable_reports/application/orchestration.py`
- Modify: `tests/test_orchestration.py`
- Create: `tests/test_retention.py`

**Interfaces:**
- Produces: `RetentionPolicy`, `RetentionGuard`, `plan_tiered_retention()`.
- Consumes: status da execução, confirmação do snapshot no PostgreSQL e lista de `run_id` main.

- [ ] **Step 1: Escrever testes falhando para horizontes e proteção**

```python
POLICY = RetentionPolicy(
    failed_raw_days=7,
    successful_raw_days=60,
    normalized_days=90,
    documents_days=395,
)


def test_failed_raw_is_candidate_after_seven_days(tmp_path):
    run = failed_run(tmp_path, age_days=8)
    assert categories(plan_tiered_retention(..., policy=POLICY)) == {("raw", run.run_id)}


def test_main_documents_and_dataset_are_protected(tmp_path):
    run = completed_main_run(tmp_path, age_days=500)
    plan = plan_tiered_retention(..., main_run_ids={run.run_id})
    assert not any(c.run_id == run.run_id and c.category in {"reports", "report-datasets"} for c in plan)


def test_raw_can_expire_after_history_is_confirmed_even_for_main(tmp_path):
    run = completed_main_run(tmp_path, age_days=61, history_confirmed=True)
    plan = plan_tiered_retention(...)
    assert any(c.run_id == run.run_id and c.category == "raw" for c in plan)
```

- [ ] **Step 2: Rodar teste e confirmar RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_retention.py tests\test_orchestration.py -q`

Expected: FAIL porque a política atual usa um único `retention_days` e aplicação opt-in.

- [ ] **Step 3: Implementar política e guardas**

O `RetentionGuard` deve negar remoção quando o alvo não tem exatamente a forma
`<root>/<category>/<client>/<run>`, está ativo, é necessário para retry, não possui
snapshot confirmado ou protege `reports`/`report-datasets` de um `main`.

- [ ] **Step 4: Aplicar a política ao final de cada orquestração**

A configuração recebe os quatro horizontes. A política é aplicada automaticamente,
mas o manifesto mantém `retention_candidates`, `retention_removed` e motivo de cada
item ignorado. `--no-apply-retention` fica disponível para manutenção controlada.

- [ ] **Step 5: Rodar testes**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_retention.py tests\test_orchestration.py -q`

Expected: PASS.

- [ ] **Step 6: Commit condicional**

```powershell
git add src/tenable_reports/application/retention.py src/tenable_reports/application/orchestration.py tests/test_retention.py tests/test_orchestration.py
git commit -m "feat: apply tiered artifact retention"
```

---

### Task 7: Produtores de inteligência VM e WAS

**Files:**
- Create: `src/tenable_reports/domain/intelligence.py`
- Create: `src/tenable_reports/application/current_intelligence.py`
- Create: `src/tenable_reports/catalogs/unsupported_signals_v1.json`
- Create: `tests/test_intelligence.py`
- Modify: `src/tenable_reports/domain/normalization.py`
- Modify: `src/tenable_reports/domain/was.py`
- Modify: `src/tenable_reports/domain/report_dataset.py`
- Modify: `src/tenable_reports/application/report_dataset.py`
- Modify: `pyproject.toml`
- Modify: `tests/test_normalization.py`
- Modify: `tests/test_report_dataset.py`

**Interfaces:**
- Produces: `build_current_intelligence()` retornando `CurrentIntelligenceResult(data, statuses, provenance)`.
- Consumes: ativos VM, findings VM, findings WAS, período e cobertura das fontes.

- [ ] **Step 1: Escrever testes falhando para todos os produtores**

```python
def test_scan_auth_health_uses_assets_observed_in_period():
    assets = [observed_asset(authenticated=True), observed_asset(authenticated=False), stale_asset()]
    result = build_scan_auth_health(assets, PERIOD)
    assert result == {"success": 1, "failure": 1, "total": 2}


def test_plugin_family_counts_fixed_findings_in_period():
    rows = build_plugin_family([fixed("Windows"), fixed("Windows"), open_finding("General")], PERIOD)
    assert rows == [{"family": "Windows", "total": 2}]


def test_eol_requires_explicit_tenable_signal():
    rows = build_eol_data([
        finding(name="OpenSSL Unsupported Version Detection"),
        finding(name="Old OpenSSL package"),
    ], catalog=CATALOG)
    assert [row["name"] for row in rows.software] == ["OpenSSL Unsupported Version Detection"]


def test_attack_vectors_group_exploitable_and_frameworks():
    rows = build_attack_vectors([
        finding(vector="NETWORK", exploitable=True, frameworks=("Metasploit",)),
        finding(vector="LOCAL", exploitable=True, frameworks=()),
    ])
    assert row(rows, "Exploitable") == {"framework": "Exploitable", "local": 1, "network": 1, "adjacent_network": 0}
    assert row(rows, "Metasploit")["network"] == 1


def test_was_unsupported_groups_applications_without_inventing_findings():
    result = build_was_unsupported([
        was_finding(name="jQuery Unsupported Version", application="https://a"),
        was_finding(name="jQuery Unsupported Version", application="https://b"),
    ], catalog=CATALOG)
    assert result[0]["applications"] == 2
```

- [ ] **Step 2: Rodar testes e confirmar RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_intelligence.py tests\test_normalization.py tests\test_report_dataset.py -q`

Expected: FAIL pela ausência dos produtores e do vetor CVSS normalizado.

- [ ] **Step 3: Normalizar vetor CVSS**

Adicionar `cvss_attack_vector: str | None` a `NormalizedFinding`. Ler
`plugin.cvss4_vector`, `definition.cvss4_vector`, `plugin.cvss3_vector` e
`definition.cvss3_vector`, nessa ordem. Interpretar somente `AV:N`, `AV:A`, `AV:L`
e `AV:P` como `NETWORK`, `ADJACENT_NETWORK`, `LOCAL` e `PHYSICAL`; valores ausentes
ficam `None`.

- [ ] **Step 4: Criar catálogo explícito de sinais**

```json
{
  "version": "unsupported-signals-v1",
  "name_patterns": [
    "\\bunsupported\\b",
    "\\bend[ -]of[ -]life\\b",
    "\\bno longer supported\\b",
    "\\bobsolete\\b"
  ],
  "excluded_patterns": [
    "supported versions?"
  ]
}
```

O matcher deve analisar `plugin_name`, `synopsis` e `description`, registrar o campo
que provou a classificação e nunca usar apenas idade, versão ou suposição externa.
Adicionar `catalogs/*.json` ao `tool.setuptools.package-data` para que a instalação
editável e o pacote distribuído encontrem exatamente o mesmo catálogo.

- [ ] **Step 5: Implementar os produtores e status de disponibilidade**

`CurrentIntelligenceResult.statuses` deve usar `AVAILABLE`, `NO_OCCURRENCES` ou
`DATA_UNAVAILABLE` por módulo. `plugin_family` usa findings `FIXED` cujo
`last_fixed_at` está no período. `scan_auth_health` usa ativos observados no período e
`last_authenticated_scan_at`. EOL e WAS usam apenas os sinais catalogados. Vetores
usam findings abertos e acionáveis.

- [ ] **Step 6: Integrar ao dataset antes do histórico**

Mesclar em `dataset.customizations` sem apagar `network_tag_snapshots`. Gravar
`customization_statuses` e `customization_provenance` com versão do catálogo, período,
estados e grain.

- [ ] **Step 7: Rodar testes**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_intelligence.py tests\test_normalization.py tests\test_report_dataset.py tests\test_was_normalization.py -q`

Expected: PASS.

- [ ] **Step 8: Commit condicional**

```powershell
git add src/tenable_reports/domain/intelligence.py src/tenable_reports/application/current_intelligence.py src/tenable_reports/catalogs/unsupported_signals_v1.json src/tenable_reports/domain/normalization.py src/tenable_reports/domain/was.py src/tenable_reports/domain/report_dataset.py src/tenable_reports/application/report_dataset.py pyproject.toml tests/test_intelligence.py tests/test_normalization.py tests/test_report_dataset.py
git commit -m "feat: build current-period custom intelligence"
```

---

### Task 8: Primeiro mês, baseline por tag e mensagens controladas no DOCX

**Files:**
- Modify: `src/tenable_reports/application/history.py`
- Modify: `src/tenable_reports/presentation/customizations_report_docx.py`
- Modify: `tests/test_history.py`
- Modify: `tests/test_customizations_report_docx.py`

**Interfaces:**
- Consumes: `monthly_history` com um item, `network_tag_snapshots`, `customization_statuses`.
- Produces: segundo DOCX útil no primeiro mês e `module_results` com status explícito.

- [ ] **Step 1: Escrever testes falhando para um único período**

```python
def test_first_month_renders_current_volume_and_no_history_message(tmp_path):
    dataset = dataset_with_one_month_history()
    result = generate_customizations_report(..., dataset_path=write(dataset), output_path=tmp_path / "custom.docx")
    text = docx_text(result.output_path)
    assert "Não há histórico do período imediatamente anterior para comparação." in text
    assert "Julho" in text
    assert "vm_monthly_volume" in result.rendered_modules


def test_first_tag_month_renders_current_baseline_without_movement(tmp_path):
    dataset = dataset_with_current_tag_snapshot_only()
    result = generate_customizations_report(...)
    text = docx_text(result.output_path)
    assert "Baseline do período atual" in text
    assert "Movimentação" not in text


def test_no_occurrences_keeps_heading_and_editorial_text(tmp_path):
    dataset = dataset_with_status("was_unsupported_tech", "NO_OCCURRENCES")
    text = generated_text(dataset)
    assert "Tecnologias WEB" in text
    assert "Neste mês não foram identificadas tecnologias WEB sem suporte." in text
```

- [ ] **Step 2: Rodar testes e confirmar RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_history.py tests\test_customizations_report_docx.py -q`

Expected: FAIL porque `_monthly_views()` exige duas linhas e os módulos retornam sem mensagem.

- [ ] **Step 3: Permitir séries de um ponto**

Alterar `_monthly_views()` para aceitar `len(rows) >= 1`. Para uma linha, gerar gráfico
de coluna/ponto, não uma linha que sugira tendência. O histórico deve sempre anexar o
candidato atual à lista de `main` anteriores.

- [ ] **Step 4: Renderizar baseline atual por tag**

Quando `network_comparisons` não existir, usar `network_tag_snapshots` para a mesma
tabela de ativos, com coluna `Exploitable`, rótulo de baseline e sem tabela de
movimentação. Quando houver predecessor com a mesma tag, manter o comparativo de dois
períodos existente.

- [ ] **Step 5: Centralizar mensagens por status**

```python
NO_DATA_MESSAGES = {
    "scan_auth_health": "Dados indisponíveis para este indicador.",
    "vm_plugin_family": "Neste mês não foram identificadas vulnerabilidades mitigadas para agrupamento por família de plugin.",
    "vm_eol_software": "Neste mês não foram identificados sistemas ou softwares sem suporte.",
    "vm_exploit_vector": "Neste mês não foram identificadas vulnerabilidades exploráveis com vetor de ataque classificável.",
    "was_unsupported_tech": "Neste mês não foram identificadas tecnologias WEB sem suporte.",
}
```

Cada função deve escrever primeiro o título e o texto editorial já existente, depois
a tabela/gráfico ou a mensagem. `DATA_UNAVAILABLE` usa a mensagem genérica aprovada e
registra a causa no manifesto.

- [ ] **Step 6: Rodar testes**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_history.py tests\test_customizations_report_docx.py -q`

Expected: PASS e nenhum DOCX customizado apenas com capa quando há módulos habilitados.

- [ ] **Step 7: Commit condicional**

```powershell
git add src/tenable_reports/application/history.py src/tenable_reports/presentation/customizations_report_docx.py tests/test_history.py tests/test_customizations_report_docx.py
git commit -m "feat: render first-month custom report baselines"
```

---

### Task 9: Proveniência e filtros opcionais abaixo das tabelas

**Files:**
- Create: `src/tenable_reports/presentation/source_filters.py`
- Create: `tests/test_source_filters.py`
- Modify: `src/tenable_reports/config/profile.py`
- Modify: `src/tenable_reports/domain/report_dataset.py`
- Modify: `src/tenable_reports/presentation/full_base_report_docx.py`
- Modify: `src/tenable_reports/presentation/customizations_report_docx.py`
- Modify: `tests/test_profile_environment.py`
- Modify: `tests/test_full_base_report_docx.py`
- Modify: `tests/test_customizations_report_docx.py`

**Interfaces:**
- Produces: `PresentationConfig.show_source_filters`, `dataset.table_provenance`, `add_source_filter_note()`.
- Consumes: queries sanitizadas dos snapshots e critérios derivados do dataset.

- [ ] **Step 1: Escrever testes falhando para opt-in e sanitização**

```python
def test_source_filter_notes_are_opt_in():
    assert load_profile({"presentation": {}}).presentation.show_source_filters is False
    assert load_profile({"presentation": {"show_source_filters": True}}).presentation.show_source_filters is True


def test_filter_note_contains_reproducible_scope_without_secrets():
    note = format_source_filter(TABLE_PROVENANCE)
    assert "01/07/2026 a 31/07/2026" in note
    assert "OPEN e REOPENED" in note
    assert "sem filtro por tag" in note
    assert "TENABLE_SECRET" not in note
    assert "accessKey" not in note


def test_tag_note_is_used_only_below_network_table():
    notes = collect_filter_notes(generated_docx(show_filters=True))
    assert "Rede: Matriz" in notes["network_top_assets"]
    assert "Rede: Matriz" not in notes["general_top_assets"]
```

- [ ] **Step 2: Rodar testes e confirmar RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_source_filters.py tests\test_profile_environment.py tests\test_full_base_report_docx.py tests\test_customizations_report_docx.py -q`

Expected: FAIL porque a opção e a proveniência por tabela não existem.

- [ ] **Step 3: Adicionar opção e contrato de proveniência**

```python
@dataclass(frozen=True, slots=True)
class PresentationConfig:
    locale: str = "pt-BR"
    vm_top5_include_output: bool = False
    was_top5_include_output: bool = False
    show_source_filters: bool = False
```

O dataset deve gravar `table_provenance` por IDs estáveis, incluindo
`overview`, `top_assets`, `top_open_vulnerabilities`, `web_top5`,
`network_top_assets:<tag_uuid>`, `plugin_family`, `eol`, `attack_vectors` e os demais
quadros de dados. Cada entrada inclui fonte, intervalo, estados, severidades, tag,
limite, grain e versão do catálogo.

- [ ] **Step 4: Adicionar nota discreta após cada tabela de dados**

`add_source_filter_note(document, provenance, enabled=True)` deve criar um parágrafo
de 8 pt, itálico, cinza, começando por `Filtro utilizado:`. Não adicionar nota a
tabelas editoriais, sumário, controle do documento ou capas.

- [ ] **Step 5: Rodar testes**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_source_filters.py tests\test_profile_environment.py tests\test_full_base_report_docx.py tests\test_customizations_report_docx.py -q`

Expected: PASS.

- [ ] **Step 6: Commit condicional**

```powershell
git add src/tenable_reports/presentation/source_filters.py src/tenable_reports/config/profile.py src/tenable_reports/domain/report_dataset.py src/tenable_reports/presentation/full_base_report_docx.py src/tenable_reports/presentation/customizations_report_docx.py tests/test_source_filters.py tests/test_profile_environment.py tests/test_full_base_report_docx.py tests/test_customizations_report_docx.py
git commit -m "feat: add optional source filters below report tables"
```

---

### Task 10: Nomes editoriais dos documentos

**Files:**
- Create: `src/tenable_reports/presentation/report_filenames.py`
- Create: `tests/test_report_filenames.py`
- Modify: `src/tenable_reports/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Produces: `report_filename(display_name, period, kind)`.
- Consumes: nome do cliente e `ReportingPeriod`.

- [ ] **Step 1: Escrever testes falhando para mensal, intervalo e virada de ano**

```python
def test_monthly_filename_uses_portuguese_abbreviation():
    assert report_filename("CLIENTE", july_period(), "base") == "[CLIENTE] Relatório de Vulnerabilidades Tenable JUL26.docx"


def test_custom_monthly_filename():
    assert report_filename("CLIENTE", july_period(), "custom") == "[CLIENTE] Inteligência e Customizações Tenable JUL26.docx"


def test_partial_range_uses_day_month_year_boundaries():
    assert report_filename("CLIENTE", range_period("2026-07-15", "2026-08-15"), "base") == "[CLIENTE] Relatório de Vulnerabilidades Tenable 15JUL26-14AGO26.docx"


def test_cross_year_range_keeps_both_years():
    assert period_suffix(range_period("2025-12-01", "2026-02-01")) == "DEZ25-JAN26"
```

- [ ] **Step 2: Rodar teste e confirmar RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_report_filenames.py tests\test_cli.py -q`

Expected: FAIL porque `command_run_client()` usa nomes técnicos numerados.

- [ ] **Step 3: Implementar nomes e sanitização Windows**

Usar `JAN FEV MAR ABR MAI JUN JUL AGO SET OUT NOV DEZ`. O fim exclusivo do período
deve ser convertido para a última data incluída ao montar o sufixo. Remover somente
`<>:"/\\|?*` e caracteres de controle do nome do arquivo; não alterar o nome dentro
do documento.

- [ ] **Step 4: Integrar ao CLI e ao manifesto**

Substituir `01-relatorio-base-...` e `02-inteligencia...` somente nos nomes finais.
Diretórios internos continuam usando `run_id` e `period_id` seguros.

- [ ] **Step 5: Rodar testes**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_report_filenames.py tests\test_cli.py tests\test_orchestration.py -q`

Expected: PASS.

- [ ] **Step 6: Commit condicional**

```powershell
git add src/tenable_reports/presentation/report_filenames.py src/tenable_reports/cli.py tests/test_report_filenames.py tests/test_cli.py
git commit -m "feat: use editorial Tenable report filenames"
```

---

### Task 11: API e interface para relatórios, main, exclusão, restauração e disco

**Files:**
- Modify: `src/tenable_reports/webapp/server.py`
- Modify: `src/tenable_reports/webapp/static/index.html`
- Modify: `src/tenable_reports/webapp/static/app.js`
- Modify: `src/tenable_reports/webapp/static/app.css`
- Modify: `tests/test_webapp.py`

**Interfaces:**
- Consumes: `PostgresReportRegistry`, `storage_preflight()` e a fila existente.
- Produces: endpoints de listagem e mutação; controles visuais por execução.

- [ ] **Step 1: Escrever testes falhando para endpoints**

```python
def test_report_list_includes_main_deleted_origin_and_reference(client):
    row = client.get("/api/clients/trt15/reports?include_deleted=true").json()["reports"][0]
    assert {"run_id", "origin", "is_main", "deleted_at", "reference_run_id", "size_bytes"} <= row.keys()


def test_promote_endpoint_requires_reason(client):
    response = client.post("/api/reports/run-b/main", json={"actor": "analista", "reason": ""})
    assert response.status_code == 400


def test_delete_main_requires_replacement_or_gap(client):
    response = client.delete("/api/reports/run-main", json={"actor": "analista", "reason": "incompleto"})
    assert response.status_code == 409


def test_restore_does_not_auto_promote(client):
    client.post("/api/reports/run-deleted/restore", json={"actor": "analista", "reason": "recuperação"})
    assert registry.report("run-deleted").is_main is False
```

- [ ] **Step 2: Rodar testes e confirmar RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_webapp.py -q`

Expected: FAIL porque os endpoints não existem.

- [ ] **Step 3: Implementar API**

Adicionar:

- `GET /api/clients/{client_id}/reports?include_deleted=true`;
- `POST /api/reports/{run_id}/main`;
- `DELETE /api/reports/{run_id}`;
- `POST /api/reports/{run_id}/restore`;
- `POST /api/jobs/{job_id}/retry`;
- `GET /api/storage`.

Todas as mutações mantêm a proteção loopback existente, aceitam no máximo 64 KiB,
validam `actor` e `reason` não vazios e retornam erros sanitizados. O retry usa o modo
e a origem do trabalho que falhou.

- [ ] **Step 4: Agrupar documentos por execução na interface**

Cada linha/card de relatório deve mostrar período, origem, status, `MAIN`, referência
usada, tamanho e módulos omitidos. Ações: abrir, baixar, definir como main, excluir e
restaurar. O modal de exclusão oferece substitutos compatíveis; `deixar lacuna` exige
segunda confirmação.

- [ ] **Step 5: Adicionar espaço e consumo**

Mostrar espaço livre, reserva estimada da fila e consumo por cliente. O botão
`Limpar resíduos seguros` chama uma rota administrativa que apenas aplica candidatos
já aprovados pelo `RetentionGuard`; ele não aceita caminho enviado pelo navegador.

- [ ] **Step 6: Expor `show_source_filters` no gerenciamento do cliente**

O checkbox inicia com o valor do perfil, persiste em `presentation` e não altera
credenciais. Testar criação e edição.

- [ ] **Step 7: Rodar testes de servidor e sintaxe JavaScript**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_webapp.py -q`

Run: `node --check src\tenable_reports\webapp\static\app.js`

Expected: ambos PASS.

- [ ] **Step 8: Commit condicional**

```powershell
git add src/tenable_reports/webapp/server.py src/tenable_reports/webapp/static/index.html src/tenable_reports/webapp/static/app.js src/tenable_reports/webapp/static/app.css tests/test_webapp.py
git commit -m "feat: manage report main references in dashboard"
```

---

### Task 12: Migração e backfill dos relatórios existentes

**Files:**
- Create: `src/tenable_reports/application/report_main_backfill.py`
- Create: `tests/test_report_main_backfill.py`
- Modify: `src/tenable_reports/cli.py`
- Modify: `src/tenable_reports/application/postgresql_migration.py`
- Modify: `README.md`
- Modify: `docs/16-postgresql-migracao-e-operacao.md`

**Interfaces:**
- Produces: `plan_main_backfill()` e comando `backfill-report-main --dry-run|--apply`.
- Consumes: `report_runs`, `publications`, `history_snapshots` e documentos válidos existentes.

- [ ] **Step 1: Escrever testes falhando para as três regras de backfill**

```python
def test_single_valid_run_is_auto_selected():
    plan = plan_main_backfill([valid_run("run-a")], used_history_run_ids=set())
    assert plan.promotions == [(KEY, "run-a")]


def test_history_used_run_wins_when_multiple_candidates_exist():
    plan = plan_main_backfill([valid_run("run-a"), valid_run("run-b")], used_history_run_ids={"run-a"})
    assert plan.promotions == [(KEY, "run-a")]


def test_ambiguous_candidates_require_analyst_selection():
    plan = plan_main_backfill([valid_run("run-a"), valid_run("run-b")], used_history_run_ids=set())
    assert plan.promotions == []
    assert plan.alerts[0].code == "MAIN_SELECTION_REQUIRED"
```

- [ ] **Step 2: Rodar teste e confirmar RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_report_main_backfill.py -q`

Expected: FAIL porque o backfill não existe.

- [ ] **Step 3: Implementar planejamento sem mutação e aplicação explícita**

`--dry-run` é o padrão e imprime promoções, ambiguidades e inválidos. `--apply` aplica
cada promoção por `PostgresReportRegistry.promote_main(actor="system-backfill",
reason="migração inicial")`. Nenhum arquivo é modificado ou excluído.

- [ ] **Step 4: Integrar migration operacional**

Documentar e testar:

```powershell
.\.venv\Scripts\python.exe -m tenable_reports database-migrate --database-env-file .\credentials\database.env
.\.venv\Scripts\python.exe -m tenable_reports backfill-report-main --database-env-file .\credentials\database.env --dry-run
.\.venv\Scripts\python.exe -m tenable_reports backfill-report-main --database-env-file .\credentials\database.env --apply
```

- [ ] **Step 5: Rodar testes**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_report_main_backfill.py tests\test_postgresql.py tests\test_cli.py -q`

Expected: PASS.

- [ ] **Step 6: Commit condicional**

```powershell
git add src/tenable_reports/application/report_main_backfill.py src/tenable_reports/application/postgresql_migration.py src/tenable_reports/cli.py tests/test_report_main_backfill.py README.md docs/16-postgresql-migracao-e-operacao.md
git commit -m "feat: backfill canonical report references"
```

---

### Task 13: Verificação integrada e QA visual

**Files:**
- Modify: `tests/test_orchestration.py`
- Modify: `tests/test_customizations_report_docx.py`
- Modify: `docs/14-historico-e-tendencias-fase9.md`
- Modify: `docs/15-orquestracao-e-distribuicao-fase10.md`
- Create: `docs/18-main-retentativas-inteligencia-operacao.md`

**Interfaces:**
- Consumes: todos os componentes anteriores.
- Produces: prova offline reproduzível e procedimento operacional.

- [ ] **Step 1: Criar cenário integrado falhando de julho e agosto**

O teste deve:

1. gerar julho sem predecessor;
2. verificar tabelas atuais, baseline por tag e mensagens sem histórico;
3. validar e promover julho a `main`;
4. gerar uma segunda versão de julho e confirmar que ela não substitui o `main`;
5. promover manualmente a segunda versão;
6. gerar agosto e confirmar que usa exatamente a segunda versão de julho;
7. excluir logicamente o `main` com substituto;
8. confirmar que nova geração usa o substituto.

- [ ] **Step 2: Rodar o cenário integrado**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_orchestration.py::test_two_month_main_reference_end_to_end -q`

Expected: PASS. Se falhar, a mensagem deve identificar uma conexão residual entre
componentes já implementados; não adicionar comportamento novo para fazê-lo passar.

- [ ] **Step 3: Fazer somente os ajustes de integração necessários**

Não adicionar novos comportamentos nesta tarefa. Corrigir apenas nomes de campos,
injeção de repositórios ou ordem de chamadas que impeçam o cenário aprovado.

- [ ] **Step 4: Rodar a suíte completa**

Run: `.\.venv\Scripts\python.exe -m pytest -q`

Expected: zero falhas.

- [ ] **Step 5: Gerar fixtures DOCX offline e validar pacotes**

Run: `.\.venv\Scripts\python.exe -m tenable_reports generate-report-pair --profile .\clients\examples\client-profile-all-customizations.json --dataset .\tests\fixtures\report-dataset-phase5.json --template .\templates\corporate\base-v1.docx --assets-dir .\templates\corporate\assets --base-output .\data\qa\base.docx --custom-output .\data\qa\custom.docx`

Run: `.\.venv\Scripts\python.exe .\scripts\prepare_docx_qa_render.py .\data\qa\base.docx .\data\qa\base-static.docx`

Run: `.\.venv\Scripts\python.exe .\scripts\prepare_docx_qa_render.py .\data\qa\custom.docx .\data\qa\custom-static.docx`

Run: `C:\Codex\LibreOfficePortable\App\libreoffice\program\soffice.exe --headless --convert-to pdf --outdir .\data\qa .\data\qa\base-static.docx`

Run: `C:\Codex\LibreOfficePortable\App\libreoffice\program\soffice.exe --headless --convert-to pdf --outdir .\data\qa .\data\qa\custom-static.docx`

Expected: pacotes válidos, PDFs/imagens renderizados e ausência de páginas vazias
inesperadas, tabelas cortadas ou textos editoriais removidos.

- [ ] **Step 6: Validar segredos e documentação**

Run: `.\.venv\Scripts\python.exe .\tools\audit_secret_leaks.py`

Expected: nenhuma credencial em perfis, manifests, filtros, logs ou DOCX.

Documentar operação do `main`, retry, retenção, restauração, backfill e recuperação de
espaço em `docs/18-main-retentativas-inteligencia-operacao.md`.

- [ ] **Step 7: Commit condicional**

```powershell
git add tests/test_orchestration.py tests/test_customizations_report_docx.py docs/14-historico-e-tendencias-fase9.md docs/15-orquestracao-e-distribuicao-fase10.md docs/18-main-retentativas-inteligencia-operacao.md
git commit -m "test: verify canonical monthly reporting workflow"
```

---

## Ordem de implantação

1. Rodar toda a suíte offline atual e guardar o baseline.
2. Executar Tasks 1–3 para estabelecer a referência histórica sem alterar coleta.
3. Aplicar a migration `0002` e fazer somente o dry-run do backfill.
4. Executar Tasks 4–6 para retentativa, disco e retenção.
5. Executar Tasks 7–10 para dados e documentos.
6. Executar Task 11 para a interface.
7. Revisar o dry-run do backfill; somente depois executar `--apply`.
8. Executar Task 13 e o QA visual.
9. Fazer uma validação real com um único cliente e uma janela pequena somente após
   confirmação explícita do usuário.

## Rollback operacional

- Migration: não remover tabelas durante o rollout. A aplicação antiga ignora as
  colunas e tabelas novas; rollback de código não exige rollback destrutivo do banco.
- Main: manter eventos e permitir promover novamente a referência anterior.
- Retenção: usar `--no-apply-retention` para suspender novas remoções; arquivos já
  removidos fisicamente dependem de backup.
- Coleta gzip: o leitor continua aceitando manifests JSONL antigos.
- Interface: as rotas novas são aditivas; a tela antiga de clientes continua funcional
  enquanto as ações de relatório estiverem indisponíveis.
