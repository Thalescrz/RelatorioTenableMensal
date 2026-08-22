# Estabilização do Export VM e Propriedades Seletivas Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tornar a coleta VM resiliente a exports lentos e reduzir payload com propriedades seletivas validadas, configuráveis por cliente e operáveis pela interface web.

**Architecture:** O perfil define estratégia, tamanho de chunk e modo seletivo. O cliente VM emite chunks disponíveis durante o polling; a camada de coleta os persiste imediatamente e mantém manifesto parcial retomável. Uma política de export encapsula seleção de propriedades, validação A/B e fallback, enquanto a interface apenas persiste opções e enfileira validações explícitas.

**Tech Stack:** Python 3.11+, `unittest`/`pytest`, dataclasses, HTTP Tenable VM Export API, JSON/JSONL gzip, servidor web local em `http.server`, JavaScript sem framework.

**Spec:** `docs/superpowers/specs/2026-08-22-estabilizacao-export-vm-properties-seletivas-design.md`

## Global Constraints

- `combined` é a estratégia padrão; `split` permanece experimental e opcional.
- O padrão local é 250 ativos por chunk; a API continua limitada entre 50 e 5000.
- O timeout permanece explícito e não deve ser aumentado silenciosamente.
- Job reutilizado, fornecido ou retomado nunca é cancelado automaticamente.
- O limite superior do período permanece aplicado localmente.
- Propriedades seletivas começam desativadas por cliente e a validação A/B exige ação explícita.
- `output` só é solicitado quando a coluna Output estiver habilitada.
- Segredos, hostnames e IPs não aparecem em logs de validação ou respostas da interface.

---

### Task 1: Configuração VM compatível com perfis existentes

**Files:**
- Modify: `src/tenable_reports/config/profile.py`
- Modify: `tests/test_profile_environment.py`
- Modify: `clients/examples/client-profile.json`

**Interfaces:**
- Produces: `VmExportConfig(strategy: str, num_assets_per_chunk: int, selective_properties: str)`.
- Produces: `ClientProfile.reporting.vm_export: VmExportConfig`.

- [ ] **Step 1: Write the failing profile tests**

```python
def test_vm_export_defaults_preserve_legacy_profiles(self):
    profile = load_client_profile(ROOT / "clients/examples/client-profile.json")
    self.assertEqual(profile.reporting.vm_export.strategy, "combined")
    self.assertEqual(profile.reporting.vm_export.num_assets_per_chunk, 250)
    self.assertEqual(profile.reporting.vm_export.selective_properties, "disabled")

def test_vm_export_rejects_invalid_strategy_chunk_and_mode(self):
    base = {
        "schema_version": 1,
        "client_id": "client-001",
        "display_name": "Cliente",
        "tenant_id": "tenant",
    }
    cases = (
        ({"strategy": "automatic"}, "strategy"),
        ({"num_assets_per_chunk": 49}, "num_assets_per_chunk"),
        ({"selective_properties": "always"}, "selective_properties"),
    )
    for vm_export, message in cases:
        with self.subTest(vm_export=vm_export):
            with self.assertRaisesRegex(ProfileError, message):
                ClientProfile.from_dict({
                    **base,
                    "reporting": {"vm_export": vm_export},
                })
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `C:\Codex\RelatorioTenableMensalv2\.venv\Scripts\python.exe -m pytest tests/test_profile_environment.py -q`

Expected: FAIL because `reporting.vm_export` does not exist.

- [ ] **Step 3: Implement parsing and validation**

Add `VmExportConfig`; accept only `combined|split`, `disabled|validation|enabled`, and chunk sizes from 50 through 5000. Parse `reporting.vm_export` as an optional object and retain defaults for existing JSON.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run: `C:\Codex\RelatorioTenableMensalv2\.venv\Scripts\python.exe -m pytest tests/test_profile_environment.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```text
git add src/tenable_reports/config/profile.py tests/test_profile_environment.py clients/examples/client-profile.json
git commit -m "feat: configure VM exports per client"
```

### Task 2: Persistência incremental de chunks e retomada segura

**Files:**
- Modify: `src/tenable_reports/infrastructure/tenable_vm/client.py`
- Modify: `src/tenable_reports/application/collect.py`
- Modify: `tests/test_vm_client.py`
- Modify: `tests/test_collection.py`

**Interfaces:**
- Produces: `TenableVmClient.wait_for_completion(export_uuid, *, progress_callback=None, chunk_callback=None)`.
- Produces: `collect_vm_snapshot` com os novos argumentos opcionais `logical_job_id` e `resume_from`, atualizando `manifest.partial.json` após cada chunk.
- Produces: `find_resumable_vm_manifest(output_root, *, profile, request, logical_job_id) -> Path | None`.

- [ ] **Step 1: Write a failing polling test**

```python
def test_wait_notifies_each_chunk_only_once_before_export_finishes(self):
    received = []
    status, chunks = client.wait_for_completion("job", chunk_callback=received.append)
    self.assertEqual(received, [2, 3])
    self.assertEqual(chunks, [2, 3])
```

- [ ] **Step 2: Verify RED for the polling test**

Run: `C:\Codex\RelatorioTenableMensalv2\.venv\Scripts\python.exe -m pytest tests/test_vm_client.py -q`

Expected: FAIL because `chunk_callback` is not accepted.

- [ ] **Step 3: Implement one-shot chunk notifications**

Track notified IDs separately from the latest status. Invoke the callback only for newly available IDs, before testing the terminal state. Let callback failures propagate so storage errors cannot be hidden.

- [ ] **Step 4: Write failing collection tests**

Cover three observable behaviors:

```python
class IncrementalTimeoutClient(TimedOutCollectionClient):
    def wait_for_completion(
        self, export_uuid, *, progress_callback=None, chunk_callback=None
    ):
        if chunk_callback is not None:
            chunk_callback(2)
        status = {
            "status": "PROCESSING",
            "chunks_available": [2],
            "completed_chunks": 1,
            "total_chunks": 2,
            "progress_made": True,
        }
        raise ExportTimeoutError(
            "timeout", export_uuid=export_uuid,
            last_status=status, progress_made=True,
        )

    def download_chunk_bytes(self, export_uuid, chunk_id):
        self.download_calls.append(chunk_id)
        return b'{"id":"finding-2"}\n'

def test_available_chunk_is_persisted_before_timeout(self):
    client = IncrementalTimeoutClient(origin="created")
    client.download_calls = []
    with tempfile.TemporaryDirectory() as directory:
        with self.assertRaises(ExportTimeoutError):
            collect_vm_snapshot(
                client=client,
                profile=load_client_profile(ROOT / "clients/examples/client-profile.json"),
                request=VulnerabilityExportRequest(filters={"state": ["OPEN"]}),
                output_root=directory,
                run_id="run-partial",
                logical_job_id="logical-july",
            )
        manifest_path = next(Path(directory).rglob("manifest.partial.json"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(client.download_calls, [2])
        self.assertEqual([item["chunk_id"] for item in manifest["chunks"]], [2])

def test_partial_manifest_reuses_downloaded_chunk_on_retry(self):
    first_manifest = create_partial_manifest_for_chunk_2()
    retry_client = FinishedClient(chunks={2: b"must-not-download"})
    result = collect_vm_snapshot(
        client=retry_client,
        profile=profile,
        request=request,
        output_root=retry_root,
        run_id="run-retry",
        logical_job_id="logical-july",
        resume_from=first_manifest,
    )
    self.assertEqual(retry_client.download_calls, [])
    self.assertEqual(result.snapshot.record_count, 1)

def test_resume_discovery_requires_same_logical_job_and_query(self):
    self.assertEqual(
        find_resumable_vm_manifest(
            directory,
            profile=profile,
            request=request,
            logical_job_id="logical-july",
        ),
        expected_manifest,
    )
    self.assertIsNone(find_resumable_vm_manifest(
        directory,
        profile=profile,
        request=VulnerabilityExportRequest(filters={"state": ["FIXED"]}),
        logical_job_id="logical-july",
    ))
```

Assertions must inspect the gzip chunk, `manifest.partial.json`, download calls and query hash; they must not assert private helper calls.

- [ ] **Step 5: Verify RED for collection tests**

Run: `C:\Codex\RelatorioTenableMensalv2\.venv\Scripts\python.exe -m pytest tests/test_collection.py -q`

Expected: FAIL because chunks are only downloaded after full completion and no partial manifest exists.

- [ ] **Step 6: Implement incremental persistence**

Create the raw directory before waiting. Persist each newly available chunk atomically, update a sanitized partial manifest after each completed download, and finish with immutable `manifest.json`. Include `logical_job_id`, origin and canonical query hash. Extend `_load_resume_chunks` to accept partial manifests with complete chunks.

- [ ] **Step 7: Preserve cancellation invariants**

On timeout, include downloaded chunk IDs and partial manifest path in the progress event. Auto-cancel only when `job.created_by_current_run` and neither remote nor locally persisted progress exists.

- [ ] **Step 8: Run focused tests**

Run: `C:\Codex\RelatorioTenableMensalv2\.venv\Scripts\python.exe -m pytest tests/test_vm_client.py tests/test_collection.py -q`

Expected: PASS.

- [ ] **Step 9: Commit**

```text
git add src/tenable_reports/infrastructure/tenable_vm/client.py src/tenable_reports/application/collect.py tests/test_vm_client.py tests/test_collection.py
git commit -m "fix: persist VM chunks while exports run"
```

### Task 3: Estratégia combinada padrão e retomada entre tentativas

**Files:**
- Modify: `src/tenable_reports/application/collect.py`
- Modify: `src/tenable_reports/cli.py`
- Modify: `tests/test_collection.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Produces: `collect_vm_snapshot_by_state` com `strategy="combined"`, `logical_job_id=None` e `resume_from=None` como argumentos opcionais.
- Consumes: `ClientProfile.reporting.vm_export` from Task 1.
- Consumes: partial manifest discovery from Task 2.

- [ ] **Step 1: Write failing strategy tests**

Verify that the default starts one export with all states, `strategy="split"` starts active/fixed exports, and a provided UUID never splits.

- [ ] **Step 2: Verify RED**

Run: `C:\Codex\RelatorioTenableMensalv2\.venv\Scripts\python.exe -m pytest tests/test_collection.py -q`

Expected: the default behavior still splits.

- [ ] **Step 3: Implement explicit strategy selection**

Use `combined` unless the caller explicitly passes `split`. Preserve aggregate manifests for split mode and add `strategy="combined"` to the normal manifest metadata.

- [ ] **Step 4: Write failing CLI integration tests**

Patch the external client boundary and assert that `_execute_period` uses the profile chunk size, strategy, logical job ID and a compatible discovered resume manifest. Assert that command-line override remains possible for diagnostics.

- [ ] **Step 5: Implement CLI wiring**

Make `--num-assets` default to `None` on complete-report commands, resolve it from the profile when omitted, and add `--vm-export-strategy`. Pass `logical_job_id` and the explicit or discovered resume manifest to collection.

- [ ] **Step 6: Run focused tests**

Run: `C:\Codex\RelatorioTenableMensalv2\.venv\Scripts\python.exe -m pytest tests/test_collection.py tests/test_cli.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```text
git add src/tenable_reports/application/collect.py src/tenable_reports/cli.py tests/test_collection.py tests/test_cli.py
git commit -m "fix: restore combined VM export strategy"
```

### Task 4: Contrato de propriedades seletivas e normalização compatível

**Files:**
- Create: `src/tenable_reports/application/vm_export_policy.py`
- Modify: `src/tenable_reports/domain/normalization.py`
- Create: `tests/test_vm_export_policy.py`
- Modify: `tests/test_normalization.py`
- Modify: `src/tenable_reports/cli.py`

**Interfaces:**
- Produces: `selective_vm_properties(include_output: bool) -> tuple[str, ...]`.
- Produces: `validate_selective_records(records) -> SelectiveContractResult`.
- Produces: `compare_vm_exports(full_records, selective_records) -> VmExportComparison`.
- Produces: `collect_vm_snapshot_with_policy` retornando `VmExportPolicyResult` e recebendo `mode` explicitamente.

- [ ] **Step 1: Write failing property-list tests**

Assert official nested names, report narrative fields and conditional `output`; assert there are no legacy unsupported property names.

- [ ] **Step 2: Verify RED**

Run: `C:\Codex\RelatorioTenableMensalv2\.venv\Scripts\python.exe -m pytest tests/test_vm_export_policy.py -q`

Expected: FAIL because the policy module does not exist.

- [ ] **Step 3: Implement the official selective property set**

Keep the tuple deduplicated and deterministic. Include the fields documented in the design; rely on always-included identity/framework/patch properties without redundantly requesting them.

- [ ] **Step 4: Write failing normalization tests**

Use a complete selective-format record containing `definition.cvss3.base_score`, `definition.cvss3.base_vector`, framework booleans and `definition.exploitability_ease`. Assert identical normalized report fields to a legacy-format fixture.

- [ ] **Step 5: Implement dual-format normalization**

Keep legacy paths first, add official nested definition paths, read `definition.references`, and derive Exploitable from the direct flag first and selective exploit metadata only when the direct flag is absent.

- [ ] **Step 6: Write failing validation/fallback tests**

Cover exact A/B match, identity divergence, narrative coverage divergence, HTTP 400 fallback, contract fallback, and no fallback for 401/403/429/timeout.

- [ ] **Step 7: Implement policy, comparison and fallback**

Validation mode returns the full collection and writes a sanitized comparison artifact. Enabled mode returns selective data when valid and performs one full-payload fallback only for HTTP 400 or contract failure.

- [ ] **Step 8: Run focused tests**

Run: `C:\Codex\RelatorioTenableMensalv2\.venv\Scripts\python.exe -m pytest tests/test_vm_export_policy.py tests/test_normalization.py tests/test_cli.py -q`

Expected: PASS.

- [ ] **Step 9: Commit**

```text
git add src/tenable_reports/application/vm_export_policy.py src/tenable_reports/domain/normalization.py src/tenable_reports/cli.py tests/test_vm_export_policy.py tests/test_normalization.py tests/test_cli.py
git commit -m "feat: validate selective VM export properties"
```

### Task 5: Interface web e validação A/B explícita

**Files:**
- Modify: `src/tenable_reports/webapp/server.py`
- Modify: `src/tenable_reports/webapp/static/index.html`
- Modify: `src/tenable_reports/webapp/static/app.js`
- Modify: `src/tenable_reports/webapp/static/app.css`
- Modify: `tests/test_webapp.py`
- Modify: `tests/test_orchestration.py`
- Modify: `src/tenable_reports/application/orchestration.py`

**Interfaces:**
- Produces: perfil editável com `vm_export_strategy`, `vm_num_assets_per_chunk` e `vm_selective_properties`.
- Produces: `POST /api/clients/{client_id}/vm-export/validate` que enfileira uma execução A/B explícita.
- Consumes: override CLI `--vm-selective-mode validation` from Task 4.

- [ ] **Step 1: Write failing configuration API tests**

Assert that add/list/update round-trip the three fields without exposing secrets and reject invalid values through `ProfileError`/`ValueError`.

- [ ] **Step 2: Verify RED**

Run: `C:\Codex\RelatorioTenableMensalv2\.venv\Scripts\python.exe -m pytest tests/test_webapp.py -q`

Expected: the fields are absent.

- [ ] **Step 3: Implement server-side profile persistence**

Create new profiles with the safe defaults, expose fields in `list_clients`, and update only `reporting.vm_export` for edited clients.

- [ ] **Step 4: Write failing orchestration and route tests**

Assert that the validation route requires a known enabled client, enqueues one manual job with `vm_selective_mode=validation`, and that orchestration propagates the override to `run-client`.

- [ ] **Step 5: Implement validation job plumbing**

Carry `vm_selective_mode` through `JobQueue`, `OrchestrationRequest`, `orchestrate` and `run-client`. Keep ordinary report jobs unchanged.

- [ ] **Step 6: Implement the compact UI controls**

Add one “Coleta VM” fieldset below the capability checkboxes. Populate values while editing, include them in form submission, and wire “Validar export otimizado” to a confirmation followed by the validation endpoint. Show success/failure through the existing toast and progress card.

- [ ] **Step 7: Validate frontend behavior**

Run the static contract tests and inspect the page at desktop width, confirming labels, disabled state for unsaved clients and no layout overflow.

- [ ] **Step 8: Run focused tests**

Run: `C:\Codex\RelatorioTenableMensalv2\.venv\Scripts\python.exe -m pytest tests/test_webapp.py tests/test_orchestration.py tests/test_cli.py -q`

Expected: PASS.

- [ ] **Step 9: Commit**

```text
git add src/tenable_reports/webapp src/tenable_reports/application/orchestration.py tests/test_webapp.py tests/test_orchestration.py tests/test_cli.py
git commit -m "feat: manage optimized VM exports in web UI"
```

### Task 6: Verificação integrada e documentação operacional

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/plans/2026-08-22-estabilizacao-export-vm-properties-seletivas.md`

**Interfaces:**
- Documents: estratégia padrão, chunks, retomada, modos seletivos, fallback e validação web.

- [ ] **Step 1: Update README with operator workflow**

Document that combined/250/full is the safe default, how to run the A/B validation from the client card, how to interpret `PASSED`/`FAILED`/fallback, and that validation starts two live exports only after confirmation.

- [ ] **Step 2: Run focused regression suites**

Run: `C:\Codex\RelatorioTenableMensalv2\.venv\Scripts\python.exe -m pytest tests/test_vm_client.py tests/test_collection.py tests/test_profile_environment.py tests/test_vm_export_policy.py tests/test_normalization.py tests/test_cli.py tests/test_orchestration.py tests/test_webapp.py -q`

Expected: PASS.

- [ ] **Step 3: Run the full suite**

Run: `C:\Codex\RelatorioTenableMensalv2\.venv\Scripts\python.exe -m pytest -q`

Expected: all tests pass with zero failures.

- [ ] **Step 4: Inspect the final diff and plan coverage**

Run: `git status --short` and `git diff --check`.

Expected: only planned files are modified and no whitespace errors are reported.

- [ ] **Step 5: Commit documentation and completion markers**

```text
git add README.md docs/superpowers/specs/2026-08-22-estabilizacao-export-vm-properties-seletivas-design.md docs/superpowers/plans/2026-08-22-estabilizacao-export-vm-properties-seletivas.md
git commit -m "docs: explain resilient VM export workflow"
```
