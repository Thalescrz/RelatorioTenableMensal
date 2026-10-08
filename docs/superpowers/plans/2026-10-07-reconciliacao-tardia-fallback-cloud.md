# Reconciliação tardia VM/TAG e fallback Cloud Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Corrigir de forma conservadora competências VM/TAG coletadas depois da tolerância, ampliar a correlação de remediações Cloud para containers e regenerar com segurança os documentos mensais afetados.

**Architecture:** A regra temporal pura ajusta somente a visão efetiva dos findings abertos antes do cálculo do dataset, preservando o snapshot original; histórico e TAGs passam a consumir essa mesma visão. O fallback Cloud correlaciona remediações por ID de recurso + CVE em todos os tipos de ativo, e a manutenção recompõe os predecessores e substitui DOCX/manifestos de forma atômica.

**Tech Stack:** Python 3.14, pytest, python-docx, PostgreSQL/psycopg, LibreOffice.

**Spec:** `docs/superpowers/specs/2026-10-07-reconciliacao-tardia-fallback-cloud-design.md`

## Global Constraints

- Períodos usam `[início, fim)` no fuso do cliente.
- Ativos e findings são ligados somente por UUID.
- A evidência normalizada e os snapshots compactos permanecem imutáveis.
- TAG recorta localmente a coleta geral e usa a associação da mesma competência.
- Cloud não transforma fonte ausente em zero nem autoriza nova consulta real.
- Nenhum arquivo versionado recebe nomes de clientes, IPs, hostnames ou credenciais.
- A substituição de documentos exige backup, manifesto atômico e validação visual.

## Review Focus

- Coleta exatamente no limite da tolerância não pode ser tratada como tardia; Task 1 cobre a fronteira.
- `REOPENED` ressurgido após o fim não pode ser contado no mês anterior; Task 1 cobre a exclusão.
- Finding órfão ou informativo não pode ser reintroduzido; Task 1 cobre as duas exclusões.
- ID de recurso repetido entre tipos Cloud não pode gerar correlação aproximada; Task 2 cobre correspondência por ocorrência exata.
- Regeneração interrompida não pode deixar documento e manifesto divergentes; Task 3 cobre rollback atômico.

---

### Task 1: Reconciliação tardia única para geral, histórico e TAG

**Files:**
- Create: `src/tenable_reports/domain/late_collection.py`
- Modify: `src/tenable_reports/domain/report_dataset.py`
- Modify: `src/tenable_reports/application/history.py`
- Test: `tests/test_late_collection_reconciliation.py`
- Test: `tests/test_report_dataset.py`
- Test: `tests/test_history.py`
- Test: `tests/test_tag_report_dataset.py`

**Interfaces:**
- Produces: `reconcile_late_open_findings(findings, *, period, collection_completed_at, grace_days, include_info_severity) -> LateCollectionReconciliation`.
- Consumes: `LateCollectionReconciliation.findings` no dataset e os mesmos critérios em fingerprints históricos.

- [ ] **Step 1: Write failing tests for late OPEN, eligible/ineligible REOPENED, boundary, orphan and informational findings**
- [ ] **Step 2: Run focused tests and confirm failures are caused by missing reconciliation**
- [ ] **Step 3: Implement the pure reconciliation and add sanitized audit counts to `collection_timing`**
- [ ] **Step 4: Make history fingerprints and TAG datasets consume the reconciled view**
- [ ] **Step 5: Run the focused dataset, history and TAG tests**
- [ ] **Step 6: Commit the domain and pipeline change**

### Task 2: Fallback Cloud por recurso + CVE para VMs e containers

**Files:**
- Modify: `src/tenable_reports/application/cloud_enrichment.py`
- Modify: `src/tenable_reports/application/cloud_report_dataset.py`
- Test: `tests/test_cloud_enrichment.py`
- Test: `tests/test_cloud_report_dataset.py`
- Test: `tests/test_cloud_report_docx.py`

**Interfaces:**
- Consumes: ocorrências normalizadas e findings de remediação com recursos/CVEs.
- Produces: `CloudVulnerabilityEnrichment` para o tipo de ativo da ocorrência e cobertura de containers composta pelas duas fontes opcionais.

- [ ] **Step 1: Write failing tests for exact container correlation, ambiguous IDs and generic remediation rejection**
- [ ] **Step 2: Run focused tests and confirm the VM-only hardcoding is the failure**
- [ ] **Step 3: Correlate every exact `(kind, resource_id, cve)` occurrence without heuristic**
- [ ] **Step 4: Include remediation status in container coverage and preserve `PARTIAL/UNAVAILABLE` copy**
- [ ] **Step 5: Run Cloud enrichment, dataset and DOCX tests**
- [ ] **Step 6: Commit the Cloud fallback change**

### Task 3: Manutenção reprodutível dos comparativos publicados

**Files:**
- Modify: `tools/repair_monthly_comparison_documents.py`
- Modify: `tests/test_repair_monthly_comparison_documents.py`
- Modify: `src/tenable_reports/application/history.py`

**Interfaces:**
- Consumes: snapshots compactos atual e predecessor, perfis, manifesto `MAIN` e regra da Task 1.
- Produces: predecessor reconstruído completo e seleção explícita de clientes/documentos para substituição atômica.

- [ ] **Step 1: Write failing tests proving predecessor summary/fingerprints/TAGs are rebuilt and current metrics stay invariant**
- [ ] **Step 2: Run focused tests and confirm the old overlay changes only Top 20**
- [ ] **Step 3: Rebuild the full predecessor snapshot and add safe client selection/audit output**
- [ ] **Step 4: Cover atomic rollback and document selection in focused tests**
- [ ] **Step 5: Run maintenance tests**
- [ ] **Step 6: Commit the maintenance change**

### Task 4: Documentação vigente

**Files:**
- Modify: `CONTEXTO.md`
- Modify: `docs/20-arquitetura-e-fluxo-de-dados.md`
- Modify: `docs/21-catalogo-de-dados-e-metricas.md`
- Modify: `docs/22-guia-operacional.md`
- Modify: `docs/23-guia-de-desenvolvimento.md`

**Interfaces:**
- Consumes: contratos executáveis das Tasks 1 a 3.
- Produces: limites, método, operação e testes descritos sem dados de produção.

- [ ] **Step 1: Update the architecture and metric contracts**
- [ ] **Step 2: Document operational trigger, conservative limits and Cloud coverage behavior**
- [ ] **Step 3: Reconcile `CONTEXTO.md` and run the guidance validator**
- [ ] **Step 4: Commit documentation**

### Task 5: Regeneração autorizada e validação integral

**Files:**
- Modify: documentos `MAIN` de setembro selecionados pelo plano operacional.
- Create: backup e evidência operacional ignorados pelo Git em `data/maintenance-backups` e `data/maintenance-work`.

**Interfaces:**
- Consumes: ferramenta da Task 3, snapshots/hashes válidos e lista auditada de afetados.
- Produces: DOCX substituídos, manifestos reconciliados e evidência sanitizada de validação.

- [ ] **Step 1: Run a dry-run and confirm selected clients/documents, no active jobs and available snapshots**
- [ ] **Step 2: Create the required DOCX artifact marker immediately before authoring**
- [ ] **Step 3: Execute the atomic regeneration without any live Tenable collection**
- [ ] **Step 4: Validate hashes/catalog and compare corrected totals against the audit**
- [ ] **Step 5: Render every changed DOCX with LibreOffice and inspect every page**
- [ ] **Step 6: Run the complete pytest suite, guidance validation, secret audit and `git diff --check`**
- [ ] **Step 7: Perform a whole-branch self-review because delegated agents are not authorized in this session**

