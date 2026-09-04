# Timeout Faseado e Checkpoint Cloud Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fazer o pipeline `STAGED_V1` aguardar exports VM por até 10 horas sem transformar o aviso de 15 minutos em falha e garantir que o componente Cloud chegue ao build local em um checkpoint terminal, permitindo publicação parcial e retentativa quando a coleta Cloud falhar.

**Architecture:** O cliente VM continuará calculando estagnação e emitindo telemetria, mas o comando remoto faseado desativará somente o timeout legado por inatividade; o orçamento durável total de 36.000 segundos continuará sendo o limite operacional. O Cloud será coletado e materializado em dataset durante a fase remota, sem renderizar DOCX; o dataset validado será referenciado pelo checkpoint e renderizado apenas pelo worker local serial. Falha Cloud será terminal para o componente, mas não bloqueará os documentos VM. Checkpoints legados ainda `PENDING` serão classificados como retentáveis e nunca reutilizados diretamente no build.

**Tech Stack:** Python 3.14, PostgreSQL/psycopg, API REST Tenable VM, GraphQL Tenable Cloud, pytest e pipeline CLI `collect-client`/`build-client`.

---

## Evidência da causa raiz

- TRT11 foi encerrado em aproximadamente 915 segundos com 1/2 chunks, embora o endpoint de status continuasse respondendo.
- TRT15 foi encerrado em aproximadamente 914 segundos com 0/6 chunks, pelo mesmo gatilho.
- `command_collect_client` passa simultaneamente `remote_progress_warning_seconds=900` e o valor legado `manual_no_progress_seconds=900`; `_wait_for_completion` transforma o segundo em `ExportTimeoutError`.
- TRT8 concluiu VM 2/2 e WAS 1/1, mas o build falhou porque `_checkpoint_from_collected_period` grava Cloud habilitado como `PENDING` e `_cloud_resume_from_checkpoint` aceita somente `COMPLETE`. Não existe hoje etapa que converta esse `PENDING` antes do build.

## Task 1: Separar alerta de estagnação do timeout legado

**Files:**
- Modify: `src/tenable_reports/cli.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_cli_collection_routing.py`

- [x] Escrever teste de regressão provando que `collect-client` ativa política de aviso sem timeout por inatividade.
- [x] Preservar o comportamento do comando monolítico/legado e seus valores de perfil.
- [x] No pipeline faseado, passar `no_progress_timeout_seconds=None`, mantendo `stall_warning_seconds=900` e `max_wait_seconds=36000`.
- [x] Confirmar que a coleta continua após o alerta e só termina em estado remoto terminal, interrupção explícita ou orçamento total.

## Task 2: Separar coleta/dataset Cloud da renderização

**Files:**
- Modify: `src/tenable_reports/application/cloud_execution.py`
- Modify: `src/tenable_reports/cli.py`
- Modify: `tests/test_cloud_execution.py`
- Modify: `tests/test_cli_collection_routing.py`

- [x] Escrever teste RED para `CloudExecutionRequest(render_documents=False)`: deve coletar, gravar dataset e snapshot, mas não renderizar nem validar DOCX.
- [x] Executar Cloud no `collect-client` depois de VM/WAS e antes da criação do checkpoint.
- [x] Gravar dataset, hash, capabilities, versão do conector, status e avisos no checkpoint.
- [x] No `build-client`, retomar `COMPLETE` apenas em `RENDER`; para `FAILED`, publicar VM sem chamada Cloud ao vivo e registrar o componente parcial/retentável.
- [x] Garantir que nenhum token ou payload sensível seja incluído no checkpoint.

## Task 3: Recuperar checkpoints Cloud incompletos com segurança

**Files:**
- Modify: `src/tenable_reports/application/failures.py`
- Modify: `src/tenable_reports/webapp/durable_dashboard_queue.py`
- Modify: `tests/test_failures.py`
- Modify: `tests/test_web_batch_derivation.py`

- [x] Classificar `Checkpoint Cloud ainda não está completo` como falha retentável de componente/checkpoint.
- [x] Validar o conteúdo do checkpoint antes de uma retentativa derivada.
- [x] Reutilizar diretamente somente checkpoint cujos componentes habilitados estejam em estado terminal; se houver `PENDING`, voltar para `REMOTE_QUEUED` e refazer apenas a preparação remota necessária.
- [x] Preservar UUID e manifesto VM já existentes quando a retentativa volta à fase remota.

## Task 4: Documentar e verificar

**Files:**
- Modify: `docs/22-guia-operacional.md`

- [x] Documentar que 15 minutos é alerta e 10 horas é o único teto de espera do UUID no `STAGED_V1`.
- [x] Documentar a fronteira Cloud: API/dataset na fase remota, DOCX na fase local, falha Cloud não bloqueia VM.
- [x] Executar testes focados, depois a suíte completa, validação das orientações, auditoria de secrets e `git diff --check`.

## Comandos de verificação

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
.\.venv\Scripts\python.exe -m pytest -q tests/test_cli.py tests/test_cli_collection_routing.py tests/test_cloud_execution.py tests/test_failures.py tests/test_web_batch_derivation.py
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe tools\validate_project_guidance.py --root .
.\.venv\Scripts\python.exe tools\audit_secret_leaks.py
git diff --check
```
