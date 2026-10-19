# Portabilidade Windows/Linux e renderizacao reproduzivel - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tornar a aplicacao instalavel e verificavel em Windows e Linux, preservando o visual Windows atual e usando um perfil Carlito/Liberation Sans deterministico no Linux.

**Architecture:** A deteccao de plataforma, fontes, LibreOffice e agendador sera centralizada em capacidades injetaveis. Os geradores continuarao compartilhando as mesmas regras e receberao perfis tipograficos explicitos; adaptadores Windows permanecerao compativeis enquanto Linux ganhara bootstrap e systemd proprios.

**Tech Stack:** Python 3.14.5, python-docx, docxcompose, Pillow, psycopg 3, PostgreSQL 18, LibreOffice 26.2.4.2, PowerShell, Bash, systemd, pytest.

**Spec:** `docs/superpowers/specs/2026-10-16-portabilidade-windows-linux-design.md`

## Global Constraints

- O baseline de retorno e a tag `baseline/pre-portabilidade-windows-linux-2026-10-16`, commit `4be0592`.
- Windows x64 e Ubuntu Server 24.04 LTS x64 sao as plataformas iniciais homologadas.
- Os pacotes oficiais iniciais usam Python 3.14.5, PostgreSQL 18 e LibreOffice 26.2.4.2.
- O perfil Windows continua usando Calibri e as ocorrencias atuais de Arial; essas fontes nao entram no Git.
- O perfil Linux usa Carlito e Liberation Sans com arquivos, licencas e hashes versionados.
- Os artefatos oficiais de deploy sao offline: toda dependencia redistribuivel fica no pacote e a instalacao nao faz download nem solicita etapas manuais depois de iniciada com os privilegios requeridos.
- Dependencias proprietarias nao redistribuiveis usam primeiro a instalacao licenciada do host Windows. Um payload licenciado e opcional para recuperacao, mas deve ser completo, autorizado e validado; ausencia de ambas as fontes bloqueia o setup, sem fallback visual.
- Ausencia ou divergencia de fonte/LibreOffice bloqueia a geracao oficial antes de qualquer chamada Tenable.
- Periodos, metricas, historico, `MAIN`, retries, idempotencia e textos editoriais nao mudam por plataforma.
- A interface permanece em loopback; este plano nao abre firewall, nao liga `0.0.0.0` e nao adiciona exposicao publica.
- Fixtures e baselines sao sinteticos e nao contem cliente, IP, hostname, e-mail, pessoa, UUID rastreavel ou segredo real.
- Nenhum gate executa coleta real, migracao de producao, publicacao oficial ou aplicacao de agenda real durante os testes.
- Toda mudanca de comportamento segue TDD, commit pequeno e validacao Windows antes de iniciar o gate Linux seguinte.

## Review Focus

- Windows com Calibri presente e Arial ausente: o preflight deve bloquear apenas o componente que ainda exige Arial, sem usar fonte bitmap.
- Linux sem systemd de usuario: geracao manual continua disponivel e o agendador fica `UNAVAILABLE`, sem fallback para cron.
- LibreOffice correto no `PATH`, mas com versao divergente: diagnostico funciona, publicacao oficial e bloqueada e nenhuma coleta remota inicia.
- Backup operacional de schema mais novo ou com arquivo fora da raiz declarada: a analise rejeita tudo antes de gravar banco ou arquivos.
- Execucoes simultaneas com perfis tipograficos diferentes: cada render usa seu proprio perfil, sem estado global compartilhado.

---

### Task 1: Congelar o baseline Windows e estabilizar a descoberta de testes

**Files:**
- Modify: `pyproject.toml`
- Create: `tools/capture_visual_baseline.py`
- Create: `tools/compare_visual_baseline.py`
- Create: `tests/fixtures/visual_baselines/windows-calibri-v1/manifest.json`
- Create: `tests/test_visual_baseline.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: geradores oficiais e fixtures sanitizadas existentes.
- Produces: `capture_visual_baseline(*, project_root: Path, output_root: Path, soffice: Path) -> Path` e `compare_visual_baseline(*, baseline_manifest: Path, candidate_root: Path) -> VisualComparison`.

- [ ] **Step 1: Escrever testes que exijam descoberta somente em `tests/`, normalizacao do DOCX e manifesto dos quatro tipos**

  `test_pytest_configuration_ignores_operational_directories`, `test_capture_manifest_covers_all_official_report_kinds` e `test_normalized_docx_comparison_ignores_only_volatile_metadata` devem falhar antes das ferramentas existirem.

- [ ] **Step 2: Confirmar a falha esperada**

  Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_visual_baseline.py`
  Expected: FAIL por funcoes/modulo ausentes, sem acessar `data/` ou `artifacts/`.

- [ ] **Step 3: Implementar a menor captura deterministica e configurar `testpaths = ["tests"]`**

  O manifesto registra plataforma, Python, bibliotecas, fontes, LibreOffice, arquivos normalizados, paginas e dimensoes; nao aceita campos operacionais. Adicionar `.visual-qa/` ao `.gitignore` para PDFs/PNGs temporarios.

- [ ] **Step 4: Capturar e revisar o baseline Windows atual**

  Run: `.\.venv\Scripts\python.exe tools\capture_visual_baseline.py --profile windows-calibri-v1 --soffice C:\Codex\LibreOfficePortable\App\libreoffice\program\soffice.exe --output-root .visual-qa\windows-calibri-v1`
  Expected: quatro DOCX, PDFs e imagens sanitizadas; manifesto sem caminhos pessoais, segredos ou dados reais.

- [ ] **Step 5: Executar testes e validadores**

  Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_visual_baseline.py tests/test_base_report_docx.py tests/test_customizations_report_docx.py tests/test_tag_report_docx.py tests/test_cloud_report_docx.py`
  Expected: PASS e comparacao do candidato atual com o baseline aprovada.

- [ ] **Step 6: Commit**

  `git commit -m "test: capture windows report visual baseline"`

### Task 2: Introduzir deteccao centralizada de plataforma

**Files:**
- Create: `src/tenable_reports/infrastructure/runtime_platform.py`
- Create: `tests/test_runtime_platform.py`

**Interfaces:**
- Consumes: `platform.system()` e `platform.machine()` por `PlatformProbe` injetavel.
- Produces: `RuntimePlatform`, `PlatformProbe`, `SystemPlatformProbe`, `RuntimePlatformInfo` e `detect_runtime_platform(probe: PlatformProbe | None = None) -> RuntimePlatformInfo`.

- [ ] **Step 1: Escrever testes de Windows, Linux, arquitetura normalizada e sistema nao suportado**

  Os testes afirmam `WINDOWS`, `LINUX` ou `UNSUPPORTED`, preservam a arquitetura sanitizada e comprovam que variaveis de ambiente nao falsificam o sistema.

- [ ] **Step 2: Confirmar falha**

  Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_runtime_platform.py`
  Expected: FAIL por modulo ausente.

- [ ] **Step 3: Implementar os tipos imutaveis e a deteccao unica**

  Nenhum modulo de apresentacao chama `platform.system()` diretamente; a injecao fica restrita ao `PlatformProbe`.

- [ ] **Step 4: Executar testes**

  Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_runtime_platform.py`
  Expected: PASS.

- [ ] **Step 5: Commit**

  `git commit -m "feat: detect runtime platform centrally"`

### Task 3: Criar perfis tipograficos verificaveis e empacotar fontes Linux

**Files:**
- Create: `src/tenable_reports/presentation/font_profiles.py`
- Create: `src/tenable_reports/assets/fonts/carlito/`
- Create: `src/tenable_reports/assets/fonts/liberation-sans/`
- Create: `src/tenable_reports/assets/fonts/LICENSES.md`
- Modify: `pyproject.toml`
- Create: `tests/test_font_profiles.py`

**Interfaces:**
- Consumes: `RuntimePlatformInfo` da Task 2, raiz do projeto e diretorio Windows de fontes injetavel.
- Produces: `FontProfile`, `FontFace`, `FontProfileError` e `resolve_font_profile(platform: RuntimePlatformInfo, *, windows_fonts: Path | None = None) -> FontProfile`.

- [ ] **Step 1: Adicionar fixtures de arquivos de fonte falsos e testes de selecao/hashes**

  Cobrir `windows-calibri-v1`, `linux-carlito-v1`, fonte ausente, hash divergente, Arial ausente e duas resolucoes concorrentes sem estado global.

- [ ] **Step 2: Confirmar falha**

  Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_font_profiles.py`
  Expected: FAIL por tipos ausentes.

- [ ] **Step 3: Adicionar Carlito/Liberation Sans e respectivas licencas**

  Registrar SHA-256 de regular, bold e italic em um manifesto dentro do pacote. Nao adicionar Calibri ou Arial ao repositorio.

- [ ] **Step 4: Implementar resolucao sem fallback silencioso**

  `FontProfile` expoe familia DOCX, familia alternativa e caminhos regulares/bold/italic; qualquer requisito invalido levanta `FontProfileError` com codigo estavel e caminho sanitizado.

- [ ] **Step 5: Executar testes e validar pacote instalavel**

  Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_font_profiles.py tests/test_project_guidance.py`
  Expected: PASS; wheel contem templates, imagens, migrations, estaticos e fontes Linux.

- [ ] **Step 6: Commit**

  `git commit -m "feat: add verified platform font profiles"`

### Task 4: Injetar tipografia nos builders DOCX sem alterar o Windows

**Files:**
- Modify: `src/tenable_reports/presentation/base_report_docx.py`
- Modify: `src/tenable_reports/presentation/full_base_report_docx.py`
- Modify: `src/tenable_reports/presentation/customizations_report_docx.py`
- Modify: `src/tenable_reports/presentation/tag_report_docx.py`
- Modify: `src/tenable_reports/presentation/cloud_report_docx.py`
- Modify: `src/tenable_reports/presentation/cloud_report_sections.py`
- Modify: `src/tenable_reports/presentation/document_control.py`
- Modify: `tests/test_base_report_docx.py`
- Modify: `tests/test_full_base_report_docx.py`
- Modify: `tests/test_customizations_report_docx.py`
- Modify: `tests/test_tag_report_docx.py`
- Modify: `tests/test_cloud_report_docx.py`
- Modify: `tests/test_document_control.py`

**Interfaces:**
- Consumes: `FontProfile` da Task 3.
- Produces: parametro keyword-only `font_profile: FontProfile | None = None` em `generate_base_report`, `generate_full_base_report`, `generate_customizations_report`, `generate_tag_report` e `generate_cloud_report`; `None` resolve o perfil do host uma vez na borda publica.

- [ ] **Step 1: Escrever testes que injetam perfis Windows e Linux em todos os documentos**

  Afirmar Calibri no perfil Windows, Carlito no Linux, controle documental consistente e nenhuma ocorrencia gerada de Times New Roman ou `ImageFont.load_default`.

- [ ] **Step 2: Confirmar que os novos testes falham**

  Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_base_report_docx.py tests/test_full_base_report_docx.py tests/test_customizations_report_docx.py tests/test_tag_report_docx.py tests/test_cloud_report_docx.py tests/test_document_control.py`
  Expected: FAIL porque os geradores ainda ignoram o perfil.

- [ ] **Step 3: Propagar `FontProfile` pelos builders e helpers compartilhados**

  Remover literais tipograficos dos caminhos de geracao, sem alterar texto, tamanho, cor, alinhamento, ordem ou dados.

- [ ] **Step 4: Provar compatibilidade Windows estrutural e visual**

  Run: `.\.venv\Scripts\python.exe tools\capture_visual_baseline.py --profile windows-calibri-v1 --soffice C:\Codex\LibreOfficePortable\App\libreoffice\program\soffice.exe --output-root .visual-qa\task-4`
  Run: `.\.venv\Scripts\python.exe tools\compare_visual_baseline.py --baseline tests\fixtures\visual_baselines\windows-calibri-v1\manifest.json --candidate-root .visual-qa\task-4`
  Expected: nenhuma divergencia fora da tolerancia de antialiasing.

- [ ] **Step 5: Executar testes DOCX completos**

  Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_base_report_docx.py tests/test_full_base_report_docx.py tests/test_customizations_report_docx.py tests/test_tag_report_docx.py tests/test_cloud_report_docx.py tests/test_document_control.py tests/test_official_document_backfill.py`
  Expected: PASS.

- [ ] **Step 6: Commit**

  `git commit -m "refactor: inject font profiles into docx builders"`

### Task 5: Remover caminhos Windows e fallback bitmap dos graficos

**Files:**
- Modify: `src/tenable_reports/presentation/monthly_visuals.py`
- Modify: `src/tenable_reports/presentation/cloud_visuals.py`
- Modify: `src/tenable_reports/presentation/customizations_report_docx.py`
- Modify: `tests/test_cloud_visuals.py`
- Modify: `tests/test_customizations_report_docx.py`
- Modify: `tests/test_tag_report_docx.py`

**Interfaces:**
- Consumes: `FontProfile` e `FontFace` da Task 3.
- Produces: `load_image_font(face: FontFace, *, size: int) -> ImageFont.FreeTypeFont`; funcoes `render_*` passam a exigir ou herdar o perfil do documento chamador.

- [ ] **Step 1: Escrever testes para caminho Windows, caminho Linux, fonte ilegivel e concorrencia**

  Afirmar que fonte ausente produz `FONT_PROFILE_INVALID`, que nenhum grafico usa `ImageFont.load_default()` e que perfis paralelos nao se contaminam.

- [ ] **Step 2: Confirmar falha**

  Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_cloud_visuals.py tests/test_customizations_report_docx.py tests/test_tag_report_docx.py`
  Expected: FAIL nos cenarios de perfil injetado/ausente.

- [ ] **Step 3: Substituir `_font()` locais pelo loader compartilhado**

  Preservar tamanhos, coordenadas e escolha Arial/Calibri do perfil Windows; usar Liberation Sans/Carlito equivalentes no Linux.

- [ ] **Step 4: Comparar o baseline Windows novamente**

  Run: `.\.venv\Scripts\python.exe tools\capture_visual_baseline.py --profile windows-calibri-v1 --soffice C:\Codex\LibreOfficePortable\App\libreoffice\program\soffice.exe --output-root .visual-qa\task-5`
  Run: `.\.venv\Scripts\python.exe tools\compare_visual_baseline.py --baseline tests\fixtures\visual_baselines\windows-calibri-v1\manifest.json --candidate-root .visual-qa\task-5`
  Expected: PASS sem mudanca de layout ou fonte.

- [ ] **Step 5: Executar testes**

  Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_cloud_visuals.py tests/test_customizations_report_docx.py tests/test_tag_report_docx.py tests/test_cloud_report_docx.py`
  Expected: PASS.

- [ ] **Step 6: Commit**

  `git commit -m "refactor: render charts with verified font profiles"`

### Task 6: Centralizar LibreOffice e o preflight de publicacao

**Files:**
- Create: `src/tenable_reports/infrastructure/libreoffice.py`
- Create: `src/tenable_reports/application/environment_preflight.py`
- Modify: `scripts/render_cloud_report_fixture.py`
- Modify: `scripts/prepare_docx_qa_render.py`
- Modify: `src/tenable_reports/cli.py`
- Create: `tests/test_libreoffice.py`
- Create: `tests/test_environment_preflight.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: plataforma da Task 2, perfil de fontes da Task 3, configuracao PostgreSQL e raizes persistentes.
- Produces: `LibreOfficeInstallation`, `resolve_libreoffice(...)`, `OfficeRenderer.render_docx(...)`, `CapabilityStatus`, `PreflightIssue`, `RuntimeCapabilities` e `run_environment_preflight(...)`.

- [ ] **Step 1: Escrever testes de prioridade de caminho, versao, timeout e preflight parcial**

  Cobrir executavel explicito, portatil Windows, `PATH` Linux, versao `26.2.4.2`, versao divergente, fonte ausente, banco indisponivel, falta de permissao de escrita e host `UNSUPPORTED` em modo somente diagnostico.

- [ ] **Step 2: Confirmar falha**

  Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_libreoffice.py tests/test_environment_preflight.py tests/test_cli.py -k "preflight or libreoffice"`
  Expected: FAIL por contratos ausentes.

- [ ] **Step 3: Implementar resolucao, render isolado e codigos `READY/WARNING/BLOCKED`**

  Adicionar CLI `environment-status --json`; ela nunca acessa Tenable e nunca imprime DSN, senha ou conteudo de documento.

- [ ] **Step 4: Integrar o gate antes da criacao de jobs oficiais**

  Chamadas de geracao real rejeitam `FONT_PROFILE_INVALID` ou `LIBREOFFICE_VERSION_MISMATCH` antes de enfileirar coleta; falha de agendador nao bloqueia geracao manual.

- [ ] **Step 5: Executar testes**

  Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_libreoffice.py tests/test_environment_preflight.py tests/test_cli.py tests/test_durable_job_queue.py`
  Expected: PASS e runner falso comprova zero chamadas Tenable quando bloqueado.

- [ ] **Step 6: Commit**

  `git commit -m "feat: add runtime preflight and office renderer"`

### Task 7: Generalizar o agendador preservando o contrato Windows

**Files:**
- Create: `src/tenable_reports/infrastructure/monthly_scheduler.py`
- Modify: `src/tenable_reports/infrastructure/windows_task_scheduler.py`
- Modify: `src/tenable_reports/application/monthly_schedule.py`
- Modify: `src/tenable_reports/webapp/server.py`
- Modify: `src/tenable_reports/webapp/static/monthly_schedule.js`
- Modify: `tests/test_windows_task_scheduler.py`
- Modify: `tests/test_monthly_schedule.py`
- Modify: `tests/test_webapp.py`
- Create: `tests/test_monthly_schedule_ui.js`

**Interfaces:**
- Consumes: plataforma da Task 2 e o comando mensal existente.
- Produces: `MonthlyScheduler` protocol, `SchedulerBackend`, `SchedulerState`, `select_monthly_scheduler(...)`; payload generico `scheduler` e campo legado `windows_task` no Windows.

- [ ] **Step 1: Escrever testes do protocolo, selecao e compatibilidade do JSON Windows**

  Cobrir consulta, aplicacao, ativacao, desativacao, confirmacao existente e UI usando `scheduler` com fallback temporario para `windows_task`.

- [ ] **Step 2: Confirmar falha**

  Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_windows_task_scheduler.py tests/test_monthly_schedule.py tests/test_webapp.py -k "monthly_schedule"`
  Expected: FAIL por payload/protocolo ausente.

- [ ] **Step 3: Extrair contrato comum sem mudar comandos Windows**

  `WindowsTaskScheduler.task_command()` e listas `schtasks.exe` permanecem byte a byte nos testes existentes; `MonthlyScheduleService` deixa de nomear Windows internamente.

- [ ] **Step 4: Migrar a UI e validar JavaScript**

  Run: `node --test tests/test_monthly_schedule_ui.js`
  Run: `node --check src/tenable_reports/webapp/static/monthly_schedule.js`
  Expected: PASS com rotulos genericos e payload legado aceito.

- [ ] **Step 5: Executar testes Python**

  Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_windows_task_scheduler.py tests/test_monthly_schedule.py tests/test_webapp.py -k "monthly_schedule"`
  Expected: PASS.

- [ ] **Step 6: Commit**

  `git commit -m "refactor: generalize monthly scheduler backend"`

### Task 8: Implementar o agendador systemd de usuario

**Files:**
- Create: `src/tenable_reports/infrastructure/systemd_user_timer_scheduler.py`
- Create: `deploy/linux/tenable-reports-monthly.service.template`
- Create: `deploy/linux/tenable-reports-monthly.timer.template`
- Create: `tests/test_systemd_user_timer_scheduler.py`
- Modify: `tests/test_monthly_schedule.py`

**Interfaces:**
- Consumes: `MonthlyScheduler`, `SchedulerState` e configuracao mensal da Task 7.
- Produces: `SystemdUserTimerScheduler(project_root: Path, config_path: Path, unit_root: Path, runner=...)` e selecao Linux padrao; `ExternalScheduler` para modo explicitamente externo.

- [ ] **Step 1: Escrever testes de unidades, argumentos e estados**

  Cobrir `systemctl --user`, `daemon-reload`, `enable --now`, disable, unidade divergente, systemd ausente e conteudo com caminhos escapados sem `shell=True`.

- [ ] **Step 2: Confirmar falha**

  Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_systemd_user_timer_scheduler.py tests/test_monthly_schedule.py`
  Expected: FAIL por adaptador ausente.

- [ ] **Step 3: Implementar systemd e modo externo sem fallback para cron**

  O timer executa o mesmo `run-monthly-batch`, com `WorkingDirectory` e configuracao explicitos. Aplicar/ativar/desativar preservam a confirmacao atual.

- [ ] **Step 4: Executar testes**

  Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_systemd_user_timer_scheduler.py tests/test_monthly_schedule.py tests/test_windows_task_scheduler.py`
  Expected: PASS nas duas plataformas simuladas.

- [ ] **Step 5: Commit**

  `git commit -m "feat: add linux systemd monthly scheduler"`

### Task 9: Expor diagnostico sanitizado no painel administrativo

**Files:**
- Modify: `src/tenable_reports/webapp/server.py`
- Modify: `src/tenable_reports/webapp/static/index.html`
- Modify: `src/tenable_reports/webapp/static/app.js`
- Modify: `src/tenable_reports/webapp/static/app.css`
- Create: `src/tenable_reports/webapp/static/environment_status.js`
- Create: `tests/test_environment_status_ui.js`
- Modify: `tests/test_webapp.py`

**Interfaces:**
- Consumes: `RuntimeCapabilities` da Task 6 e `SchedulerState` da Task 7.
- Produces: GET `/api/admin/environment-status` e bloco Admin **Ambiente**, sem mutacoes.

- [ ] **Step 1: Escrever testes HTTP e JS do payload sanitizado**

  Afirmar plataforma, arquitetura, perfil, LibreOffice, PostgreSQL, armazenamento, agenda e issues; rejeitar DSN, senha, token, caminhos de credencial e dados de clientes.

- [ ] **Step 2: Confirmar falha**

  Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_webapp.py -k "environment_status"`
  Expected: FAIL com rota 404.

- [ ] **Step 3: Implementar endpoint e apresentacao somente leitura**

  Estados `READY`, `WARNING` e `BLOCKED` recebem texto operacional; nenhum botao corrige ambiente ou instala pacote automaticamente.

- [ ] **Step 4: Executar testes de UI**

  Run: `node --test tests/test_environment_status_ui.js`
  Run: `node --check src/tenable_reports/webapp/static/environment_status.js`
  Run: `node --check src/tenable_reports/webapp/static/app.js`
  Expected: PASS.

- [ ] **Step 5: Executar testes Python**

  Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_webapp.py -k "environment_status or monthly_schedule"`
  Expected: PASS.

- [ ] **Step 6: Commit**

  `git commit -m "feat: show sanitized runtime diagnostics"`

### Task 10: Tornar instalacao e dependencias reproduziveis nas duas plataformas

**Files:**
- Create: `deploy/runtime-manifest.json`
- Create: `deploy/offline-bundle.schema.json`
- Create: `deploy/windows/bundle-layout.json`
- Create: `deploy/linux/bundle-layout.json`
- Create: `requirements/lock/windows-py314.txt`
- Create: `requirements/lock/linux-py314.txt`
- Modify: `scripts/setup.ps1`
- Create: `scripts/setup.sh`
- Create: `scripts/bootstrap_postgresql.sh`
- Create: `scripts/run_web.sh`
- Create: `scripts/run_monthly_orchestration.sh`
- Create: `tools/build_offline_bundle.py`
- Create: `tools/verify_offline_bundle.py`
- Create: `tools/validate_runtime_manifest.py`
- Create: `tests/test_runtime_manifest.py`
- Create: `tests/test_offline_bundle.py`
- Create: `tests/test_deployment_scripts.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: preflight da Task 6 e systemd da Task 8.
- Produces: bootstraps idempotentes, locks com hashes, `build_offline_bundle(...)`, `verify_offline_bundle(...)` e `validate_runtime_manifest(root: Path, platform: RuntimePlatformInfo) -> RuntimeManifestResult`.

- [ ] **Step 1: Escrever testes de manifesto, pacote offline, locks, shell seguro e package-data**

  Cobrir versoes exatas, SHA-256, assinaturas, rejeicao de payload ausente/adulterado, proibicao de download no instalador, quoting de caminhos com espaco, instalacao silenciosa simulada, segunda execucao idempotente, ausencia de segredo e inclusao de templates/fontes no wheel.

- [ ] **Step 2: Confirmar falha**

  Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_runtime_manifest.py tests/test_offline_bundle.py tests/test_deployment_scripts.py tests/test_project_guidance.py`
  Expected: FAIL por artefatos ausentes.

- [ ] **Step 3: Gerar locks, manifesto e montador dos pacotes offline**

  Incluir Python, LibreOffice, PostgreSQL, wheels, dependencias nativas e fontes redistribuiveis no bundle da plataforma. Calibri/Arial usam a instalacao Windows licenciada validada no setup; um payload licenciado e opcional, tudo-ou-nada. O montador falha fechado quando qualquer payload obrigatorio, assinatura ou hash estiver ausente e rejeita payload proprietario parcial ou sem autorizacao.

- [ ] **Step 4: Atualizar PowerShell e implementar Bash sem acesso de rede**

  Ambos consomem somente `vendor/`, criam venv, instalam pelo lock/wheelhouse, instalam LibreOffice e PostgreSQL silenciosamente, validam assets, banco e preflight, mas nao iniciam servidor, coleta ou agenda. O bootstrap Linux privilegiado recebe a opcao de linger no comando inicial e nao abre prompt durante a execucao.

- [ ] **Step 5: Validar sintaxe e comportamento simulado**

  Run Windows: `powershell.exe -NoProfile -File scripts\setup.ps1 -WhatIf`
  Run Linux CI: `bash -n scripts/setup.sh scripts/bootstrap_postgresql.sh scripts/run_web.sh scripts/run_monthly_orchestration.sh`
  Expected: nenhum efeito real em `-WhatIf`; Bash sem erro de sintaxe; nenhum instalador contem comando de download ou dependencia de internet.

- [ ] **Step 6: Executar testes**

  Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_runtime_manifest.py tests/test_offline_bundle.py tests/test_deployment_scripts.py tests/test_project_guidance.py`
  Expected: PASS.

- [ ] **Step 7: Commit**

  `git commit -m "build: add reproducible windows and linux setup"`

### Task 11: Adicionar exportacao e importacao operacional controladas

**Files:**
- Create: `src/tenable_reports/application/deployment_transfer.py`
- Create: `src/tenable_reports/infrastructure/postgresql_backup.py`
- Modify: `src/tenable_reports/cli.py`
- Create: `tests/test_deployment_transfer.py`
- Create: `tests/test_postgresql_backup.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: PostgreSQL, catalogo de documentos, configuracoes locais e raizes persistentes.
- Produces: `DeploymentTransferManifest`, `plan_deployment_export(...)`, `create_deployment_export(...)`, `plan_deployment_import(...)`, `apply_deployment_import(...)`; CLI `deployment-export` e `deployment-import --dry-run|--apply`.

- [ ] **Step 1: Escrever testes do inventario, hashes e recusas antes de qualquer escrita**

  Cobrir schema mais novo, arquivo adulterado, path traversal, falta de espaco, conflito de identidade, documento ausente, `MAIN` preservado e ausencia de segredos.

- [ ] **Step 2: Confirmar falha**

  Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_deployment_transfer.py tests/test_postgresql_backup.py tests/test_cli.py -k "deployment"`
  Expected: FAIL por comandos/servicos ausentes.

- [ ] **Step 3: Implementar exportacao com `pg_dump` e manifesto imutavel**

  O pacote fica fora do Git, usa nomes curtos, hashes SHA-256 e inclui somente configuracoes/artefatos autorizados. Segredos sao listados como `REPROVISION_REQUIRED`, nunca copiados.

- [ ] **Step 4: Implementar importacao em duas fases**

  `--dry-run` valida tudo; `--apply` exige confirmacao exata, restaura em staging, valida schema/hashes/`MAIN` e promove atomicamente. Falha reverte staging sem alterar instalacao atual.

- [ ] **Step 5: Executar testes**

  Run: `.\.venv\Scripts\python.exe -m pytest -q tests/test_deployment_transfer.py tests/test_postgresql_backup.py tests/test_report_registry_postgresql.py tests/test_cli.py -k "deployment or main"`
  Expected: PASS com runners e repositorios falsos; nenhum PostgreSQL real.

- [ ] **Step 6: Commit**

  `git commit -m "feat: add controlled deployment transfer"`

### Task 12: Homologar Windows e Linux e reconciliar a documentacao vigente

**Files:**
- Modify: `README.md`
- Modify: `CONTEXTO.md`
- Modify: `docs/20-arquitetura-e-fluxo-de-dados.md`
- Modify: `docs/22-guia-operacional.md`
- Modify: `docs/23-guia-de-desenvolvimento.md`
- Modify: `templates/corporate/README.md`
- Create: `docs/25-implantacao-windows-linux.md`
- Create: `artifacts` only outside Git for QA evidence

**Interfaces:**
- Consumes: todos os contratos e comandos das Tasks 1-11.
- Produces: matriz de homologacao, runbooks Windows/Linux, backup/restauracao e registro das limitacoes restantes.

- [ ] **Step 1: Atualizar documentacao sem reescrever historico**

  Descrever montagem e instalacao dos pacotes offline, requisitos de privilegio, excecao para materiais proprietarios licenciados, perfis tipograficos, preflight, systemd, locks, transferencia, rollback pela tag e proibicao de exposicao publica.

- [ ] **Step 2: Executar homologacao Windows completa**

  Run: `.\.venv\Scripts\python.exe tools\capture_visual_baseline.py --profile windows-calibri-v1 --soffice C:\Codex\LibreOfficePortable\App\libreoffice\program\soffice.exe --output-root .visual-qa\final-windows`
  Run: `.\.venv\Scripts\python.exe -m pytest -q`
  Run: `.\.venv\Scripts\python.exe tools\compare_visual_baseline.py --baseline tests\fixtures\visual_baselines\windows-calibri-v1\manifest.json --candidate-root .visual-qa\final-windows`
  Expected: suite verde e quatro documentos equivalentes ao baseline aprovado.

- [ ] **Step 3: Executar homologacao Linux limpa**

  Em Ubuntu Server 24.04 LTS limpo, executar `scripts/setup.sh`, `environment-status --json`, suite completa, quatro fixtures, render LibreOffice e testes do systemd com runner controlado. Expected: perfil `linux-carlito-v1`, sem fallback, corte, sobreposicao ou mudanca de dados.

- [ ] **Step 4: Inspecionar visualmente paginas criticas nas duas plataformas**

  Conferir capa, sumario, controle documental, tabelas largas, graficos mensais, TAG, Cloud, cabecalho, rodape e contracapa. Registrar apenas evidencias sanitizadas.

- [ ] **Step 5: Executar verificacao final do projeto**

  Run: `.\.venv\Scripts\python.exe -m pytest -q`
  Run: `.\.venv\Scripts\python.exe tools\validate_project_guidance.py --root .`
  Run: `.\.venv\Scripts\python.exe tools\audit_secret_leaks.py`
  Run: `git diff --check`
  Expected: todos com exit code 0 e zero vazamentos.

- [ ] **Step 6: Commit**

  `git commit -m "docs: publish cross-platform deployment runbook"`

## Gate final de integracao

- [ ] Revisar todos os commits contra a especificacao e confirmar que nenhum alterou regras editoriais ou dados.
- [ ] Confirmar que `git diff baseline/pre-portabilidade-windows-linux-2026-10-16...HEAD` contem somente o escopo aprovado.
- [ ] Repetir a suite completa na branch e, depois do merge, na `main`.
- [ ] Publicar `main` somente depois das homologacoes Windows e Linux registradas.
- [ ] Manter a tag de baseline no remoto; uma reversao usa `git revert` dos commits/merge, nunca `reset --hard` nem force-push.

## Registro de complementação em 2026-10-19

- [x] O código do bundle passou a ser copiado somente do índice Git e das raízes
  aprovadas, sem incluir arquivos ignorados, locais ou releases anteriores.
- [x] Índice vazio ou parcial, sparse checkout incompleto e arquivo rastreado
  permitido ausente agora bloqueiam a construção.
- [x] Assinaturas Windows foram fixadas pelo assunto e SHA-256 do certificado; a
  assinatura LibreOffice no Linux foi fixada pelo fingerprint TDF esperado.
- [x] Os instaladores Windows e Linux passaram a falhar fechados com
  `environment-status --require-ready` antes de anunciar conclusão.
- [x] A instalação Linux sem rede foi exercitada duas vezes em contêiner Ubuntu
  24.04 limpo e confirmou idempotência.
- [ ] Permanecem pendentes a instalação em Windows limpo e a homologação Linux em
  VM/servidor com `systemd`, timer real e inspeção visual dos quatro DOCX.
