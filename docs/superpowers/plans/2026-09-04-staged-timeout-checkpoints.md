# Timeout Faseado e Checkpoint Cloud Implementation Plan

> **Registro de decisão:** este arquivo preserva o desenho ou plano considerado no
> momento da entrega. Ele não comprova sozinho o estado atual nem a conclusão de
> cada etapa. Consulte o [contexto atual](../../../CONTEXTO.md) e o
> [índice da documentação](../../README.md).

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

## Follow-up operacional de 05/09/2026

O lote de retentativa `0fe22381` expôs duas lacunas depois da entrega original:

- 24 clientes já apresentavam todos os componentes aplicáveis em estado terminal,
  mas os respectivos jobs permaneciam em `REMOTE_RUNNING` e nenhum deles havia
  avançado individualmente para `READY_FOR_BUILD`;
- no TRT8, a consulta Cloud terminou com o dataset completo, mas a publicação do
  snapshot falhou com `CLOUD_SNAPSHOT_PUBLICATION_FAILED`. A janela automática
  seguinte perdeu a compatibilidade entre o `run_id` e o diretório curto do
  componente e encerrou com `Caminho do checkpoint de componente incompatível`.

As tarefas abaixo são obrigatórias antes de considerar o fluxo faseado encerrado.
Elas devem ser executadas com dados sintéticos ou artefatos locais sanitizados; a
suíte não deve iniciar uma coleta real.

## Task 5: Liberar cada cliente para montagem de forma independente

**Files:**
- Modify: `src/tenable_reports/webapp/durable_dashboard_queue.py`
- Modify: `src/tenable_reports/infrastructure/web_batch_components_postgresql.py`
- Test: `tests/test_durable_job_queue.py`
- Test: `tests/test_web_batch_components_postgresql.py`

- [x] Escrever teste RED com dois clientes concorrentes: o primeiro possui todos
  os componentes aplicáveis terminais e o segundo mantém VM em processamento.
- [x] Provar no teste que `_finalize_remote_components` consolida e valida somente
  o primeiro cliente, emite `COLLECTION_READY` e o move para `READY_FOR_BUILD` sem
  esperar o segundo cliente ou o lote inteiro.
- [x] Tornar a finalização idempotente: chamadas concorrentes ou repetidas não
  podem duplicar checkpoint, evento, claim de build nem documento.
- [x] Garantir que `COMPLETE`, `NOT_APPLICABLE` e falhas terminais toleradas sejam
  avaliados por componente e por cliente; um componente ainda em execução bloqueia
  apenas seu próprio cliente.
- [x] Preservar o único worker local de montagem e permitir que ele consuma cada
  `READY_FOR_BUILD` enquanto outros clientes continuam em `REMOTE_RUNNING`.
- [x] Expor telemetria suficiente para distinguir “componentes remotos concluídos”,
  “consolidando checkpoint”, “pronto para montagem” e “montando documento”.

**Critério de aceite:** em um lote com vários clientes, o primeiro cliente remoto
concluído deve entrar na fila de montagem antes do término dos demais, sem depender
de polling manual nem de uma transição global do lote.

## Task 6: Recuperar a publicação Cloud sem repetir a coleta concluída

**Files:**
- Modify: `src/tenable_reports/application/cloud_execution.py`
- Modify: `src/tenable_reports/application/component_collection.py`
- Modify: `src/tenable_reports/cli.py`
- Modify: `src/tenable_reports/webapp/durable_dashboard_queue.py`
- Test: `tests/test_cloud_execution.py`
- Test: `tests/test_component_collection.py`
- Test: `tests/test_cli_collection_routing.py`
- Test: `tests/test_durable_job_queue.py`

- [x] Escrever teste RED reproduzindo a sequência observada: dataset Cloud gravado,
  falha na publicação do snapshot, abertura automática da janela 2 e retomada com
  o mesmo cliente, tenant, período e job lógico.
- [x] Persistir caminho, hash e identidade do dataset já concluído antes de tentar
  publicar o snapshot, de modo que `CLOUD_SNAPSHOT_PUBLICATION_FAILED` preserve um
  checkpoint Cloud retentável e não descarte a coleta GraphQL válida.
- [x] Centralizar a derivação do diretório curto do componente para que inicial,
  retry automático e retry manual usem o mesmo identificador estável, sem exigir
  que o nome da pasta seja igual ao `run_id` da tentativa.
- [x] Validar escopo por `client_id`, `tenant_id`, período, componente, job lógico e
  hash do artefato; incompatibilidade real deve produzir código local específico,
  nunca `UNEXPECTED` nem nova chamada silenciosa à API.
- [x] Na retentativa de publicação, recarregar e revalidar o dataset persistido,
  publicar o snapshot e seguir para o merge do checkpoint sem repetir as 11.371
  páginas Cloud já coletadas.
- [x] Se o dataset estiver ausente ou com hash inválido, manter o erro visível e
  exigir retry Cloud seletivo; não marcar o componente como concluído e não afetar
  VM, WAS, TAG ou documentos já publicados.

**Critério de aceite:** uma falha local posterior à coleta Cloud deve ser reparada
somente a partir do dataset preservado. A quantidade de chamadas GraphQL não pode
aumentar durante a retentativa de publicação.

## Task 7: Regressão, observabilidade e documentação do follow-up

**Files:**
- Modify: `docs/20-arquitetura-e-fluxo-de-dados.md`
- Modify: `docs/22-guia-operacional.md`
- Modify: `docs/23-guia-de-desenvolvimento.md`

- [x] Adicionar teste integrado do pipeline com clientes concluindo a coleta em
  ordens diferentes e montagem serial ocorrendo enquanto o lote remoto continua.
- [x] Adicionar teste de reinício entre a falha de publicação Cloud e sua retomada,
  comprovando a durabilidade do dataset e do checkpoint.
- [x] Registrar tempos por transição e códigos sanitizados de falha local, sem
  expor caminhos de checkpoint na API ou na interface.
- [x] Documentar a transição por cliente e a recuperação de publicação Cloud sem
  nova coleta.
- [x] Executar todos os comandos de verificação abaixo antes do fluxo Git.

## Comandos de verificação

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
.\.venv\Scripts\python.exe -m pytest -q tests/test_cli.py tests/test_cli_collection_routing.py tests/test_cloud_execution.py tests/test_failures.py tests/test_web_batch_derivation.py
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe tools\validate_project_guidance.py --root .
.\.venv\Scripts\python.exe tools\audit_secret_leaks.py
git diff --check
```
