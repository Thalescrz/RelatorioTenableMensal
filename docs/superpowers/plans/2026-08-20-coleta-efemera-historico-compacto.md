# Coleta Efêmera e Histórico Compacto — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fazer com que coletas Tenable pesadas existam somente durante a execução, mantendo permanentemente apenas os DOCX e o histórico compacto necessário às comparações.

**Architecture:** VM e WAS gravam chunks gzip; normalização e leitores aceitam JSONL legado ou gzip. Após validar os documentos e confirmar o snapshot compacto no PostgreSQL, uma limpeza vinculada ao `run_id` remove raw, snapshots locais, normalizados e datasets. Falhas preservam staging comprimido por sete dias e a interface web expõe uso, prévia e reciclagem.

**Tech Stack:** Python 3.12+, biblioteca padrão `gzip`, `hashlib` e `zlib`, PostgreSQL 18, psycopg 3, HTML/CSS/JavaScript sem framework, pytest, python-docx.

**Spec:** `docs/superpowers/specs/2026-08-20-coleta-efemera-historico-compacto-design.md`

## Global Constraints

- Não alterar textos, títulos, tabelas ou regras editoriais dos relatórios.
- Não remover dados de uma execução antes de validar DOCX e confirmar snapshot e registro no PostgreSQL.
- Execuções incompletas ou com retentativa pendente ficam protegidas por sete dias.
- DOCX e snapshots compactos permanecem até ação explícita do analista.
- A interface web deve operar a reciclagem sem exigir linha de comando.
- Leitores continuam aceitando `.jsonl` legado durante a transição.
- Caminhos removidos devem estar dentro da raiz e seguir `categoria/cliente/run_id`.
- Segredos, hostnames, IPs e conteúdo de findings não podem aparecer em logs de limpeza.
- O banco atual está vazio; migrations `0001` e `0002` permanecem imutáveis e a evolução entra em `0003`.
- A pasta ainda não é um repositório Git; comandos de commit são checkpoints para uso após a inicialização.

---

### Task 1: I/O JSONL comprimido e compatibilidade legada

**Files:**
- Create: `src/tenable_reports/infrastructure/jsonl_io.py`
- Create: `tests/test_jsonl_io.py`

**Interfaces:**
- Consumes: caminhos `Path` terminados em `.jsonl` ou `.jsonl.gz` e iteráveis de mappings.
- Produces: `JsonlWriteResult`, `iter_jsonl_objects()`, `write_jsonl_gzip_exclusive()` e `resolve_jsonl_artifact()`.

- [ ] **Step 1: escrever testes que expressem o contrato**

```python
def test_gzip_round_trip_is_deterministic_and_streamed(tmp_path):
    path = tmp_path / "findings.jsonl.gz"
    result = write_jsonl_gzip_exclusive(path, ({"id": n} for n in range(3)))
    assert list(iter_jsonl_objects(path)) == [{"id": 0}, {"id": 1}, {"id": 2}]
    assert result.records == 3
    assert result.logical_bytes > result.stored_bytes
    assert result.sha256 == sha256(path.read_bytes()).hexdigest()


def test_reader_keeps_legacy_jsonl_compatibility(tmp_path):
    legacy = tmp_path / "assets.jsonl"
    legacy.write_text('{"id":"a"}\n', encoding="utf-8")
    assert list(iter_jsonl_objects(legacy)) == [{"id": "a"}]


def test_resolver_prefers_gzip_and_rejects_ambiguous_pair(tmp_path):
    (tmp_path / "findings.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "findings.jsonl.gz").write_bytes(gzip.compress(b""))
    with pytest.raises(ValueError, match="ambíguos"):
        resolve_jsonl_artifact(tmp_path, "findings")
```

- [ ] **Step 2: confirmar que os testes falham porque o módulo ainda não existe**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_jsonl_io.py -q --basetemp .\.test-tmp-jsonl-red`

Expected: FAIL de importação de `tenable_reports.infrastructure.jsonl_io`.

- [ ] **Step 3: implementar o writer gzip determinístico e o leitor incremental**

```python
@dataclass(frozen=True, slots=True)
class JsonlWriteResult:
    path: Path
    records: int
    logical_bytes: int
    stored_bytes: int
    sha256: str


def iter_jsonl_objects(path: str | Path) -> Iterator[dict[str, Any]]:
    source = Path(path)
    opener = gzip.open if source.name.endswith(".gz") else source.open
    with opener(source, mode="rt", encoding="utf-8", newline="") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"Registro JSONL não é objeto em {source}, linha {line_number}.")
            yield value
```

O writer usa arquivo `.partial`, `gzip.GzipFile(mtime=0)`, permissões `0o600`, substituição atômica e exclusão do partial em falha. O checksum é calculado sobre o arquivo armazenado.

- [ ] **Step 4: executar os testes do módulo**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_jsonl_io.py -q --basetemp .\.test-tmp-jsonl-green`

Expected: PASS.

- [ ] **Step 5: checkpoint de commit**

```powershell
git add src/tenable_reports/infrastructure/jsonl_io.py tests/test_jsonl_io.py
git commit -m "feat: add deterministic compressed jsonl io"
```

---

### Task 2: Comprimir WAS e artefatos normalizados

**Files:**
- Modify: `src/tenable_reports/application/collect_was.py`
- Modify: `src/tenable_reports/application/normalize.py`
- Modify: `src/tenable_reports/application/normalize_was.py`
- Modify: `src/tenable_reports/application/report_dataset.py`
- Modify: `src/tenable_reports/application/history.py`
- Modify: `src/tenable_reports/application/postgresql_migration.py`
- Modify: `tests/test_collection.py`
- Modify: `tests/test_normalization.py`
- Modify: `tests/test_was_normalization.py`
- Modify: `tests/test_report_dataset.py`
- Modify: `tests/test_history.py`

**Interfaces:**
- Consumes: utilitários da Task 1 e chunks VM já armazenados como `.jsonl.gz`.
- Produces: WAS raw e normalizados em `.jsonl.gz`, manifests com `logical_bytes` e `stored_bytes`, leitores transparentes para formatos novo e legado.

- [ ] **Step 1: escrever testes para os novos nomes e compatibilidade**

```python
def test_normalization_writes_compressed_artifacts(tmp_path):
    # Acrescentar estas asserções ao teste de integração que já cria `result`
    # com CollectionResult de ativos e findings sanitizados.
    assert result.assets_path.name == "assets.jsonl.gz"
    assert result.findings_path.name == "findings.jsonl.gz"
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["artifacts"]["findings"]["stored_bytes"] < manifest["artifacts"]["findings"]["logical_bytes"]


def test_report_dataset_reads_legacy_or_gzip_normalized_snapshot(tmp_path):
    # Executar o builder existente duas vezes sobre o fixture do teste:
    # uma com os três JSONL e outra após convertê-los para JSONL.GZ.
    assert legacy_artifact.result.dataset.to_dict() == compressed_artifact.result.dataset.to_dict()
```

Também cobrir WAS para garantir `chunk-000001.jsonl.gz` e `was-findings.jsonl.gz`.

- [ ] **Step 2: executar os testes direcionados e observar as falhas de contrato**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_collection.py tests\test_normalization.py tests\test_was_normalization.py tests\test_report_dataset.py tests\test_history.py -q --basetemp .\.test-tmp-compression-red`

Expected: FAIL nos nomes `.jsonl.gz` e nos leitores que ainda procuram `.jsonl`.

- [ ] **Step 3: adaptar os produtores**

`collect_was_snapshot()` deve gravar o conteúdo retornado pela API diretamente com `write_jsonl_gzip_exclusive()` após `parse_chunk_response()`. `normalize_collections()` e `normalize_was_collection()` passam geradores de `to_dict()` ao writer compartilhado e não montam um `bytes` contendo todo o JSONL.

O manifesto usa:

```python
"artifacts": {
    "findings": {
        "uri": result.path.resolve().as_uri(),
        "records": result.records,
        "logical_bytes": result.logical_bytes,
        "stored_bytes": result.stored_bytes,
        "sha256": result.sha256,
        "compression": "gzip",
    }
}
```

- [ ] **Step 4: adaptar consumidores e migração legada**

`report_dataset.py` e `history.py` chamam `resolve_jsonl_artifact(directory, stem)` e `iter_jsonl_objects(path)`. A migração classifica `.jsonl.gz` como `jsonl_data_gzip`, sem abrir conteúdo sensível.

- [ ] **Step 5: executar os testes direcionados**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_collection.py tests\test_normalization.py tests\test_was_normalization.py tests\test_report_dataset.py tests\test_history.py -q --basetemp .\.test-tmp-compression-green`

Expected: PASS.

- [ ] **Step 6: checkpoint de commit**

```powershell
git add src/tenable_reports/application src/tenable_reports/application/postgresql_migration.py tests
git commit -m "feat: compress collection and normalized artifacts"
```

---

### Task 3: Fingerprints históricos compactos no PostgreSQL

**Files:**
- Create: `src/tenable_reports/domain/fingerprints.py`
- Create: `src/tenable_reports/infrastructure/postgresql_migrations/0003_compact_history_and_cleanup.sql`
- Modify: `src/tenable_reports/domain/history.py`
- Modify: `src/tenable_reports/application/history.py`
- Modify: `src/tenable_reports/infrastructure/postgresql.py`
- Modify: `tests/test_history.py`
- Modify: `tests/test_postgresql.py`
- Create: `tests/test_fingerprints.py`

**Interfaces:**
- Consumes: chave canônica `NormalizedFinding.finding_key` e `HistorySnapshot`.
- Produces: `fingerprint_finding_key()`, `pack_fingerprints()`, `unpack_fingerprints()` e persistência em `bytea` versionada.

- [ ] **Step 1: escrever testes de determinismo, serialização e comparação**

```python
def test_fingerprint_is_stable_and_non_reversible():
    value = fingerprint_finding_key("asset-1|19506|443|tcp")
    assert len(value) == 16
    assert value == fingerprint_finding_key("asset-1|19506|443|tcp")
    assert b"asset-1" not in value


def test_packed_fingerprints_round_trip_sorted_and_unique():
    values = [b"b" * 16, b"a" * 16, b"a" * 16]
    assert unpack_fingerprints(pack_fingerprints(values)) == (b"a" * 16, b"b" * 16)
```

O teste PostgreSQL verifica que `payload` não contém `open_finding_keys` e que as três colunas `bytea` restauram um `HistorySnapshot` equivalente.

- [ ] **Step 2: confirmar falha antes da implementação**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_fingerprints.py tests\test_history.py tests\test_postgresql.py -q --basetemp .\.test-tmp-fingerprint-red`

Expected: FAIL por ausência do módulo, migration e colunas.

- [ ] **Step 3: implementar fingerprint e pacote binário versionado**

```python
FINGERPRINT_VERSION = "sha256-128-v1"


def fingerprint_finding_key(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()[:16]


def pack_fingerprints(values: Iterable[bytes]) -> bytes:
    ordered = sorted(set(values))
    if any(len(value) != 16 for value in ordered):
        raise ValueError("Fingerprint deve possuir 16 bytes.")
    return zlib.compress(b"".join(ordered), level=9)
```

`HistorySnapshot` passa a usar tuplas de `bytes` para abertos, corrigidos e ressurgidos. CSV exporta somente resumos, como definido na especificação.

- [ ] **Step 4: criar migration idempotente `0003`**

```sql
alter table tenable_reports.history_snapshots
    add column if not exists fingerprint_version text,
    add column if not exists open_fingerprints bytea,
    add column if not exists fixed_fingerprints bytea,
    add column if not exists resurfaced_fingerprints bytea;

alter table tenable_reports.report_runs
    add column if not exists cleanup_status text not null default 'NOT_REQUIRED',
    add column if not exists cleanup_completed_at timestamptz,
    add column if not exists cleanup_bytes bigint not null default 0;
```

Adicionar checks para `cleanup_bytes >= 0` e estado permitido. Manter o índice predecessor existente.

- [ ] **Step 5: adaptar o repositório PostgreSQL**

`PostgresSnapshotRepository.publish()` grava payload resumido e os pacotes `bytea` na mesma transação. Leituras reconstroem fingerprints por `unpack_fingerprints()`. Publicação repetida do mesmo `run_id` permanece idempotente.

- [ ] **Step 6: executar testes e migration no PostgreSQL local vazio**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_fingerprints.py tests\test_history.py tests\test_postgresql.py -q --basetemp .\.test-tmp-fingerprint-green`

Run: `.\.venv\Scripts\python.exe -m tenable_reports database-migrate --database-env-file .\credentials\database.env`

Expected: testes PASS e `0003_compact_history_and_cleanup.sql` aplicada uma única vez.

- [ ] **Step 7: checkpoint de commit**

```powershell
git add src/tenable_reports/domain src/tenable_reports/infrastructure src/tenable_reports/application/history.py tests
git commit -m "feat: persist compact historical fingerprints"
```

---

### Task 4: Limpeza imediata pós-publicação e retenção de falhas

**Files:**
- Modify: `src/tenable_reports/application/retention.py`
- Modify: `src/tenable_reports/application/publishing.py`
- Modify: `src/tenable_reports/application/orchestration.py`
- Modify: `src/tenable_reports/infrastructure/postgresql.py`
- Modify: `src/tenable_reports/cli.py`
- Modify: `tests/test_retention.py`
- Modify: `tests/test_orchestration.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: execução publicada, snapshot confirmado, raiz por modo, `client_id` e `run_id`.
- Produces: `plan_published_run_cleanup()`, `apply_cleanup_plan()`, estados `PENDING`, `COMPLETE`, `PARTIAL` e `FAILED`, além de auditoria de bytes.

- [ ] **Step 1: escrever testes de segurança e limpeza imediata**

```python
def test_published_run_cleanup_removes_only_transient_categories(tmp_path):
    create_run_categories(tmp_path, "client-a", "run-a")
    plan = plan_published_run_cleanup(
        scoped_output_root=tmp_path,
        client_id="client-a",
        run_id="run-a",
        publication_confirmed=True,
        history_confirmed=True,
    )
    removed = apply_cleanup_plan(scoped_output_root=tmp_path, candidates=plan.candidates)
    assert categories(removed) == {"raw", "snapshots", "normalized", "report-datasets"}
    assert (tmp_path / "reports" / "client-a" / "run-a").is_dir()


@pytest.mark.parametrize("publication,history", [(False, True), (True, False)])
def test_cleanup_refuses_unconfirmed_run(tmp_path, publication, history):
    with pytest.raises(ValueError, match="confirmada"):
        plan_published_run_cleanup(
            scoped_output_root=tmp_path,
            client_id="client-a",
            run_id="run-a",
            publication_confirmed=publication,
            history_confirmed=history,
        )
```

Também testar que falha com oito dias é elegível, falha com seis dias não é, execução ativa nunca é removida e um caminho fora da raiz é recusado.

- [ ] **Step 2: executar testes e observar falhas**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_retention.py tests\test_orchestration.py tests\test_cli.py -q --basetemp .\.test-tmp-cleanup-red`

Expected: FAIL porque a retenção atual só considera idade.

- [ ] **Step 3: implementar plano vinculado ao run publicado**

```python
TRANSIENT_CATEGORIES = ("raw", "snapshots", "normalized", "report-datasets")


def plan_published_run_cleanup(*, scoped_output_root, client_id, run_id,
                               publication_confirmed, history_confirmed):
    if not publication_confirmed or not history_confirmed:
        raise ValueError("Publicação e histórico precisam estar confirmados.")
    return CleanupPlan(tuple(
        CleanupCandidate(root / category / client_id / run_id, category, client_id, run_id)
        for category in TRANSIENT_CATEGORIES
        if (root / category / client_id / run_id).is_dir()
    ))
```

`apply_cleanup_plan()` soma tamanhos antes da remoção, usa apenas `Path.resolve().relative_to(root.resolve())`, exige quatro segmentos conhecidos e registra resíduos individualmente.

- [ ] **Step 4: integrar após `finalize_history_publication()`**

Somente depois da validação e da confirmação no PostgreSQL, a CLI marca `cleanup_status=PENDING`, aplica o plano, registra bytes e muda para `COMPLETE` ou `PARTIAL`. Uma falha de limpeza não invalida os DOCX; ela gera alerta e fica disponível para nova tentativa.

- [ ] **Step 5: ajustar retenção temporal de falhas**

`RetentionPolicy` passa a ter `failed_staging_days=7` e `logs_days=90`. A varredura por idade considera apenas execuções falhas ou resíduos `CLEANUP_PENDING`; execuções publicadas usam o plano imediato.

- [ ] **Step 6: executar testes direcionados**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_retention.py tests\test_orchestration.py tests\test_cli.py -q --basetemp .\.test-tmp-cleanup-green`

Expected: PASS.

- [ ] **Step 7: checkpoint de commit**

```powershell
git add src/tenable_reports/application src/tenable_reports/infrastructure/postgresql.py src/tenable_reports/cli.py tests
git commit -m "feat: recycle transient data after publication"
```

---

### Task 5: Estimativa comprimida e painel web de armazenamento

**Files:**
- Modify: `src/tenable_reports/application/storage_guard.py`
- Modify: `src/tenable_reports/webapp/server.py`
- Modify: `src/tenable_reports/webapp/static/index.html`
- Modify: `src/tenable_reports/webapp/static/app.js`
- Modify: `src/tenable_reports/webapp/static/app.css`
- Modify: `tests/test_storage_guard.py`
- Modify: `tests/test_webapp.py`

**Interfaces:**
- Consumes: raiz de dados, histórico de `logical_bytes`, `stored_bytes`, resíduos e estado PostgreSQL.
- Produces: `StorageSnapshot`, `estimate_compressed_peak()`, `GET /api/storage`, `POST /api/storage/cleanup/preview` e `POST /api/storage/cleanup/apply`.

- [ ] **Step 1: escrever testes da estimativa e endpoints**

```python
def test_compressed_estimate_uses_observed_ratio_with_safe_floor():
    estimate = estimate_compressed_peak(
        last_logical_bytes=10 * GIB,
        last_stored_bytes=1500 * MIB,
        minimum_free_gb=10,
    )
    assert estimate.estimated_staging_bytes < 3 * GIB
    assert estimate.required_free_bytes >= 10 * GIB


def test_storage_cleanup_preview_never_removes_data(client):
    response = client.post("/api/storage/cleanup/preview", json={})
    assert response.status_code == 200
    assert response.json()["removed_bytes"] == 0
```

Testar aplicação, resíduos protegidos, banco indisponível e erro sanitizado.

- [ ] **Step 2: executar testes e confirmar ausência dos endpoints**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_storage_guard.py tests\test_webapp.py -q --basetemp .\.test-tmp-storage-red`

Expected: FAIL nos novos contratos.

- [ ] **Step 3: implementar métricas e endpoints**

`GET /api/storage` retorna apenas números e estados:

```json
{
  "free_bytes": 0,
  "temporary_bytes": 0,
  "pending_cleanup_runs": 0,
  "last_cleanup_at": null,
  "last_cleanup_status": "NEVER_RUN",
  "by_client": []
}
```

Preview e aplicação chamam o mesmo serviço de retenção. Aplicação exige PostgreSQL disponível e nunca aceita um parâmetro para ignorar proteções.

- [ ] **Step 4: implementar o card simples na página geral**

Adicionar card `Armazenamento` com espaço livre, temporários, pendências, última reciclagem e botão `Limpar dados temporários`. O primeiro clique abre a prévia; o segundo confirma apenas os itens elegíveis. Alertas usam o painel geral existente.

- [ ] **Step 5: executar testes direcionados**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_storage_guard.py tests\test_webapp.py -q --basetemp .\.test-tmp-storage-green`

Expected: PASS.

- [ ] **Step 6: checkpoint de commit**

```powershell
git add src/tenable_reports/application/storage_guard.py src/tenable_reports/webapp tests
git commit -m "feat: add storage monitoring and cleanup ui"
```

---

### Task 6: Configuração, documentação e proteção para GitHub

**Files:**
- Modify: `orchestration/clients.example.json`
- Modify: `README.md`
- Modify: `.gitignore`
- Modify: `tests/test_orchestration.py`

**Interfaces:**
- Consumes: políticas definidas nas Tasks 1–5.
- Produces: configuração segura de exemplo, documentação operacional e exclusão de resíduos do Git.

- [ ] **Step 1: atualizar teste de configuração**

```python
def test_example_uses_ephemeral_storage_defaults():
    config = load_orchestration_config(ROOT / "orchestration/clients.example.json")
    assert config.failed_staging_days == 7
    assert config.logs_days == 90
    assert config.cleanup_after_publish is True
```

- [ ] **Step 2: executar o teste e confirmar falha dos campos novos**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_orchestration.py -q --basetemp .\.test-tmp-config-red`

Expected: FAIL por ausência das opções.

- [ ] **Step 3: documentar o ciclo operacional**

O README deve explicar: coleta comprimida; gatilho de confirmação; descarte pós-sucesso; sete dias para falhas; DOCX e snapshot permanentes; painel de armazenamento; impossibilidade de regenerar relatórios antigos a partir do bruto.

- [ ] **Step 4: ampliar `.gitignore`**

Adicionar explicitamente:

```gitignore
.tmp/
.test-tmp-*/
.test-cache-*/
artifacts/
clients/managed/
orchestration/clients.json
*.log
*.sqlite
*.sqlite3
*.db
~$*.docx
```

Manter `clients/examples/`, `orchestration/clients.example.json`, `.env.example` e `credentials/*.env.example` versionáveis.

- [ ] **Step 5: executar teste de configuração e auditoria de segredos**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_orchestration.py -q --basetemp .\.test-tmp-config-green`

Run: `.\.venv\Scripts\python.exe tools\audit_secret_leaks.py --root .`

Expected: PASS e nenhum segredo real entre arquivos elegíveis ao Git.

- [ ] **Step 6: checkpoint de commit**

```powershell
git add README.md .gitignore orchestration/clients.example.json tests/test_orchestration.py
git commit -m "docs: document ephemeral collection lifecycle"
```

---

### Task 7: Verificação integrada e prova de dois períodos

**Files:**
- Modify: `tests/test_orchestration.py`
- Modify: `tests/test_history.py`
- Create: `docs/16-armazenamento-e-reciclagem.md`

**Interfaces:**
- Consumes: todas as Tasks anteriores.
- Produces: prova de que dois meses continuam comparáveis sem os dados pesados do primeiro mês.

- [ ] **Step 1: escrever o teste ponta a ponta**

```python
def test_two_months_compare_after_first_month_transients_are_removed(tmp_path):
    july = execute_published_fixture(tmp_path, period="2026-07")
    assert not july.raw_dir.exists()
    assert not july.normalized_dir.exists()
    assert july.base_docx.is_file()

    august = execute_published_fixture(tmp_path, period="2026-08")
    assert august.dataset["customizations"]["history_status"]["status"] == "COMPATIBLE_PREDECESSOR"
    assert august.dataset["customizations"]["monthly_history"][0]["period_id"] == "2026-07"
```

- [ ] **Step 2: executar teste ponta a ponta**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_orchestration.py tests\test_history.py -q --basetemp .\.test-tmp-e2e-storage`

Expected: PASS.

- [ ] **Step 3: aplicar migrations e verificar banco**

Run: `.\.venv\Scripts\python.exe -m tenable_reports database-migrate --database-env-file .\credentials\database.env`

Run: `.\.venv\Scripts\python.exe -m tenable_reports database-status --database-env-file .\credentials\database.env`

Expected: migrations `0001`, `0002` e `0003`; tabelas operacionais continuam vazias até uma coleta real.

- [ ] **Step 4: executar a suíte completa em temporário isolado**

Run: `.\.venv\Scripts\python.exe -m pytest -q --basetemp .\.test-tmp-storage-final -o cache_dir=.\.test-cache-storage-final`

Expected: todos os testes e subtestes passam.

- [ ] **Step 5: validar a interface local**

Iniciar com `scripts/run_web.ps1`, abrir a página geral, confirmar card de armazenamento, prévia vazia, aplicação segura e ausência de erros no console. Nenhuma coleta real será iniciada nesta verificação.

- [ ] **Step 6: registrar operação e recuperação**

Criar `docs/16-armazenamento-e-reciclagem.md` com estados, política, mensagens da interface, recuperação de `CLEANUP_PENDING` e comportamento de falhas.

- [ ] **Step 7: checkpoint de commit**

```powershell
git add tests docs/16-armazenamento-e-reciclagem.md
git commit -m "test: verify compact history after transient cleanup"
```
