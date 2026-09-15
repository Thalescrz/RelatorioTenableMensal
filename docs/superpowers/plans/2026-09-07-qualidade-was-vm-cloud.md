# Qualidade WAS, VM vazia e padrao Cloud Implementation Plan

> **Registro de decisão:** este arquivo preserva o desenho ou plano considerado no
> momento da entrega. Ele não comprova sozinho o estado atual nem a conclusão de
> cada etapa. Consulte o [contexto atual](../../../CONTEXTO.md) e o
> [índice da documentação](../../README.md).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminar a coluna WAS vazia, tornar coleta VM vazia diagnosticavel e alinhar o DOCX Cloud ao padrao corporativo geral.

**Architecture:** O cliente WAS fornece metadados paginados de plugins, a camada de aplicacao os persiste e enriquece o modelo normalizado por Plugin ID, e a apresentacao escolhe dinamicamente as colunas. A regra de qualidade fica no dominio do dataset e a formatacao Cloud reutiliza constantes e convencoes do relatorio geral.

**Tech Stack:** Python 3.14, python-docx, PostgreSQL/psycopg, pytest, LibreOffice.

**Spec:** `docs/superpowers/specs/2026-09-07-qualidade-was-vm-cloud-design.md`

## Global Constraints

- O relatorio geral nunca e filtrado por TAG.
- Periodos usam `[inicio, fim)` no fuso do cliente.
- Findings e ativos continuam ligados por UUID.
- Consultas de catalogo e testes nunca expoem credenciais nem payload real.
- Nenhuma coleta real e iniciada durante a implementacao.
- O relatorio Cloud ampliado e o unico modelo Cloud.

---

### Task 1: Familia WAS por catalogo e coluna dinamica

**Files:**
- Modify: `src/tenable_reports/infrastructure/tenable_was/client.py`
- Modify: `src/tenable_reports/application/plugin_catalog.py`
- Modify: `src/tenable_reports/infrastructure/plugin_catalog_postgresql.py`
- Modify: `src/tenable_reports/application/normalize_was.py`
- Modify: `src/tenable_reports/application/period_collection.py`
- Modify: `src/tenable_reports/presentation/full_base_report_docx.py`
- Test: `tests/test_was_client.py`
- Test: `tests/test_plugin_catalog.py`
- Test: `tests/test_was_normalization.py`
- Test: `tests/test_full_base_report_docx.py`

**Interfaces:**
- Consumes: `GET /was/v2/plugins?limit=200&offset=N&sort=plugin_id:asc`.
- Produces: `list_was_plugins(wanted_plugin_ids=...)`, `find_by_plugin_ids(...)` e tabela WAS com esquema dinamico.

- [x] **Step 1: Write failing tests for pagination, enrichment and both table schemas**
- [x] **Step 2: Run focused tests and confirm the expected failures**
- [x] **Step 3: Implement the minimum client, repository, enrichment and renderer changes**
- [x] **Step 4: Run focused tests and confirm they pass**

### Task 2: Aviso de VM vazia com ativos

**Files:**
- Modify: `src/tenable_reports/domain/report_dataset.py`
- Test: `tests/test_report_dataset.py`
- Modify: `docs/22-guia-operacional.md`

**Interfaces:**
- Consumes: populacoes normalizadas de ativos e findings.
- Produces: `ReportQualityIssue(code="VM_FINDINGS_EMPTY_WITH_ASSETS", severity="WARNING")`.

- [x] **Step 1: Write a failing domain test for assets present with zero VM findings**
- [x] **Step 2: Run the focused test and confirm the warning is absent**
- [x] **Step 3: Add the warning without changing zero-valued metrics**
- [x] **Step 4: Run report dataset tests**

### Task 3: Padronizacao visual do Cloud

**Files:**
- Modify: `src/tenable_reports/presentation/cloud_report_sections.py`
- Modify: `src/tenable_reports/presentation/cloud_report_docx.py`
- Test: `tests/test_cloud_report_docx.py`
- Modify: `docs/23-guia-de-desenvolvimento.md`

**Interfaces:**
- Consumes: constantes tipograficas e cromaticas corporativas.
- Produces: DOCX Cloud com Calibri e hierarquia equivalente ao relatorio geral.

- [x] **Step 1: Write failing structural tests for fonts, headings and tables**
- [x] **Step 2: Run the focused tests and confirm the current divergence**
- [x] **Step 3: Align generated styles and direct formatting**
- [x] **Step 4: Run Cloud DOCX tests**

### Task 4: Regressao e validacao visual

**Files:**
- Modify: `docs/20-arquitetura-e-fluxo-de-dados.md`
- Modify: `docs/21-catalogo-de-dados-e-metricas.md`

**Interfaces:**
- Consumes: fixtures sanitizadas existentes.
- Produces: evidencia estrutural, visual e de regressao completa.

- [x] **Step 1: Generate sanitized WAS and Cloud DOCX fixtures**
- [x] **Step 2: Render affected pages with LibreOffice and inspect them**
- [x] **Step 3: Run the complete test suite, guidance validation, secret audit and `git diff --check`**
- [x] **Step 4: Review the final diff without merging or pushing**
