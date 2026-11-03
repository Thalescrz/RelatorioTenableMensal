# Modo OWASP 2025 por cliente — Plano de Implementação

> **Registro de decisão:** este arquivo preserva o plano aprovado antes da
> implementação. Ele não comprova sozinho o estado atual nem a conclusão das
> etapas. Consulte o [contexto atual](../../../CONTEXTO.md) e o
> [índice da documentação](../../README.md).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permitir que cada cliente escolha permanentemente entre OWASP 2025 inclusivo e OWASP 2025 exclusivo, preservando o comportamento atual por padrão e oferecendo recuperação seletiva do WAS quando a análise exclusiva falhar.

**Architecture:** O perfil guarda um enum explícito. O modo inclusivo continua usando o catálogo local de plugins e não faz chamadas novas. O modo exclusivo usa um adaptador isolado e somente de leitura para a análise WAS da console, persiste checkpoints por categoria e um snapshot final imutável, e entrega ao domínio linhas já normalizadas. Uma falha exclusiva torna somente o componente WAS retentável; a publicação omite todo o conteúdo WAS daquela tentativa, preserva os outros componentes e permite recuperação com o modo permanente atualmente salvo no perfil.

**Tech Stack:** Python 3.14, dataclasses/`StrEnum`, cliente HTTP Tenable existente, JSON/JSONL e SHA-256, PostgreSQL/psycopg já existente para estados operacionais, JavaScript/HTML/CSS sem framework, `pytest`, `python-docx` e LibreOffice para QA visual.

**Spec:** [Modo OWASP 2025 por cliente](../specs/2026-11-03-modo-owasp-2025-por-cliente-design.md)

## Global Constraints

- Preservar `inclusive` como padrão para perfis antigos e para novos clientes.
- O modo escolhido é permanente no perfil; não existe override temporário de retry.
- O modo exclusivo não pode cair silenciosamente para o inclusivo.
- A falha de uma das dez categorias torna o WAS indisponível por inteiro naquela tentativa; dados parciais não entram no DOCX.
- VM, assets, TAG e Cloud concluídos não podem ser recolhidos em uma retentativa somente do WAS.
- A retentativa não inicia scans e reutiliza export, chunks e findings WAS íntegros sempre que válidos.
- Períodos continuam em `[início, fim)` no fuso do cliente; se `between` for inclusivo, enviar `fim - 1 ms`.
- A apresentação recebe tabelas prontas; não consulta a Tenable nem recalcula a seleção OWASP.
- A ordenação das linhas permanece VPR decrescente, seguida pelos critérios atuais de desempate.
- Credenciais, cookies, identificadores de ativos, nomes reais, IPs, hostnames e payloads sensíveis não podem aparecer em logs, testes, snapshots de contrato ou documentação.
- Não chamar API real nem iniciar coleta durante testes. O contrato HTTP será comprovado com transports falsos e fixtures sanitizadas.
- Não reinterpretar datasets históricos automaticamente.
- Não é necessária migration PostgreSQL: a preferência mora no perfil e a auditoria detalhada mora nos snapshots/manifestos; os estados operacionais continuam usando as estruturas já existentes.

## Review Focus

- Compatibilidade de perfil e serialização do enum.
- Paridade do payload exclusivo com os filtros aprovados e paginação completa.
- Separação estrita entre snapshot parcial de recuperação e snapshot final publicável.
- Ausência de conteúdo WAS quando a análise exclusiva falhar.
- Invariância dos artefatos VM/TAG/Cloud em retry seletivo.
- Substituição atômica da publicação incompleta sem documento ou conjunto duplicado.
- Auditoria do modo original e do modo efetivo sem reescrever a tentativa falha.
- Mensagens operacionais claras e sem detalhes internos no relatório do cliente.

---

### Task 1: Contrato de perfil e retrocompatibilidade

**Files:**
- Modify: `src/tenable_reports/config/profile.py`
- Modify: `tests/test_profile_environment.py`
- Modify: `orchestration/clients.example.json`

**Interfaces:**
- Produces: `Owasp2025Mode(StrEnum)` com `INCLUSIVE = "inclusive"` e `CONSOLE_EXCLUSIVE = "console_exclusive"`.
- Changes: `WasScope.owasp_2025_mode: Owasp2025Mode = Owasp2025Mode.INCLUSIVE`.
- Consumes: `scope.was.owasp_2025_mode` em JSON; campo ausente significa `inclusive`.

- [x] **Step 1: Escrever testes falhando para default, parsing, serialização e valor inválido**

  Cobrir perfil legado sem o campo, os dois valores válidos e mensagem de erro para qualquer outro valor. Confirmar também que a seleção é preservada quando WAS está temporariamente desabilitado.

- [x] **Step 2: Rodar os testes e confirmar RED**

  Run: `.\.venv\Scripts\python.exe -m pytest tests\test_profile_environment.py -q`

  Expected: FAIL porque `WasScope` ainda não expõe `owasp_2025_mode`.

- [x] **Step 3: Implementar o enum e o parsing compatível**

  Usar uma função de parsing que rejeite strings desconhecidas e aplique o default somente quando o campo estiver ausente/vazio. Não acoplar a validade do valor ao checkbox `enabled`, para que desabilitar/reabilitar WAS não apague a preferência.

- [x] **Step 4: Atualizar o perfil de exemplo sem incluir dados reais**

  Registrar `"owasp_2025_mode": "inclusive"` no escopo WAS do exemplo.

- [x] **Step 5: Rodar testes focados e confirmar GREEN**

  Run: `.\.venv\Scripts\python.exe -m pytest tests\test_profile_environment.py -q`

- [x] **Step 6: Commit pequeno**

  Run: `git add src/tenable_reports/config/profile.py tests/test_profile_environment.py orchestration/clients.example.json`

  Run: `git commit -m "feat: add per-client OWASP 2025 mode"`

---

### Task 2: Edição permanente no painel de clientes

**Files:**
- Modify: `src/tenable_reports/webapp/static/index.html`
- Modify: `src/tenable_reports/webapp/static/app.js`
- Modify: `src/tenable_reports/webapp/static/app.css`
- Modify: `src/tenable_reports/webapp/server.py`
- Modify: `tests/test_webapp.py`
- Create: `tests/test_owasp_mode_ui.js`

**Interfaces:**
- API de clientes passa a retornar e aceitar `was_owasp_2025_mode`.
- Formulário usa `name="was_owasp_2025_mode"` com valores `inclusive` e `console_exclusive`.
- O seletor fica visualmente associado ao WAS; quando WAS está desabilitado, fica desabilitado na UI sem apagar o valor salvo.

- [x] **Step 1: Escrever testes falhando de create/list/update/reopen**

  Verificar: criação sem campo usa inclusivo; criação exclusiva persiste; edição retorna o valor; desabilitar WAS preserva a seleção; reabrir o formulário mostra o valor salvo; payload inválido retorna erro controlado.

- [x] **Step 2: Rodar testes e confirmar RED**

  Run: `.\.venv\Scripts\python.exe -m pytest tests\test_webapp.py -q -k "client and owasp"`

  Run: `node --test tests/test_owasp_mode_ui.js`

- [x] **Step 3: Implementar leitura e gravação no servidor**

  Alterar `list_clients`, `add_client` e `update_client` para mapear o campo do formulário para `scope.was.owasp_2025_mode`. Validar pelo mesmo parser do perfil antes da gravação atômica.

- [x] **Step 4: Implementar o seletor explícito no frontend**

  Rótulos:

  - `Inclusivo — qualquer associação com a categoria`;
  - `Exclusivo — mesma seleção da console Tenable`.

  O JavaScript deve definir inclusivo no reset, restaurar o valor na edição, enviar o valor e alternar apenas o estado visual `disabled` quando WAS for ligado/desligado.

- [x] **Step 5: Rodar testes focados e confirmar GREEN**

  Run: `.\.venv\Scripts\python.exe -m pytest tests\test_webapp.py -q -k "client and owasp"`

  Run: `node --test tests/test_owasp_mode_ui.js`

- [x] **Step 6: Commit pequeno**

  Run: `git add src/tenable_reports/webapp/static/index.html src/tenable_reports/webapp/static/app.js src/tenable_reports/webapp/static/app.css src/tenable_reports/webapp/server.py tests/test_webapp.py tests/test_owasp_mode_ui.js`

  Run: `git commit -m "feat: manage OWASP mode per client"`

---

### Task 3: Adaptador somente de leitura para a análise exclusiva

**Files:**
- Create: `src/tenable_reports/infrastructure/tenable_was/owasp_analysis.py`
- Create: `tests/test_was_owasp_analysis.py`
- Modify: `src/tenable_reports/infrastructure/tenable_was/__init__.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class Owasp2025PluginGroup:
    category: str
    plugin_id: int
    name: str | None
    family: str | None
    severity: str
    vpr_score: float | None
    instances: int


class TenableWasOwaspAnalysisClient:
    def __init__(self, client: TenableWasClient) -> None: ...

    def list_category_plugins(
        self,
        *,
        category: str,
        start_at: datetime,
        end_at: datetime,
        page_size: int = 200,
    ) -> tuple[Owasp2025PluginGroup, ...]: ...
```

- [x] **Step 1: Escrever testes falhando do payload exato e da normalização**

  Com transport falso, afirmar que o `POST /api/v3/findings/vulnerabilities/webapp/analysis` contém somente filtros aprovados: estado não corrigido, severidade não informativa, risco não aceito, igualdade exata de `owasp_2025`, `last_seen` e agrupamento por `definition.id`. Testar `A01 -> A1` apenas no contrato remoto e manter `A01` no domínio.

- [x] **Step 2: Escrever testes falhando de paginação e respostas inválidas**

  Cobrir múltiplas páginas, página vazia final, cursor/`next`, valores ausentes, VPR nulo, contagem inválida e HTTP transitório herdado do cliente base. A resposta inválida deve falhar; nunca deve virar lista vazia.

- [x] **Step 3: Rodar testes e confirmar RED**

  Run: `.\.venv\Scripts\python.exe -m pytest tests\test_was_owasp_analysis.py -q`

- [x] **Step 4: Implementar construção sanitizada do contrato e paginação completa**

  Converter limites para UTC. Enviar o fim como último milissegundo anterior a `end_at`. Não registrar corpo de resposta nem filtros que contenham identificadores reais. Encapsular a rota não pública neste único módulo.

- [x] **Step 5: Implementar ordenação determinística**

  Ordenar localmente por VPR decrescente, severidade, instâncias decrescentes e Plugin ID. Tratar VPR ausente depois de valores numéricos, sem inventar zero no snapshot.

- [x] **Step 6: Rodar testes focados e confirmar GREEN**

  Run: `.\.venv\Scripts\python.exe -m pytest tests\test_was_owasp_analysis.py tests\test_was_client.py -q`

- [x] **Step 7: Commit pequeno**

  Run: `git add src/tenable_reports/infrastructure/tenable_was/owasp_analysis.py src/tenable_reports/infrastructure/tenable_was/__init__.py tests/test_was_owasp_analysis.py`

  Run: `git commit -m "feat: query exclusive OWASP 2025 findings"`

---

### Task 4: Checkpoints por categoria e snapshot final imutável

**Files:**
- Create: `src/tenable_reports/application/was_owasp_2025.py`
- Create: `tests/test_was_owasp_collection.py`
- Modify: `src/tenable_reports/application/period_collection.py`
- Modify: `tests/test_staged_execution.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class Owasp2025CollectionAttempt:
    status: str
    snapshot_path: Path | None
    completed_categories: tuple[str, ...]
    warnings: tuple[Mapping[str, Any], ...]
    failure: WasFailureDetails | None


def collect_exclusive_owasp_2025_snapshot(
    *,
    client: TenableWasOwaspAnalysisClient,
    profile: ClientProfile,
    period: ReportingPeriod,
    output_root: str | Path,
    run_id: str,
    progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
) -> Owasp2025CollectionAttempt: ...
```

- [x] **Step 1: Escrever testes falhando para dez categorias e snapshot completo**

  Afirmar caminhos sob `recovery/<client>/<run>/was-owasp-2025/` para checkpoints e `snapshots/<client>/<run>/tenable_was_owasp_2025.snapshot.json` somente quando A01–A10 estiverem completas. O snapshot final registra modo, fonte, período/fuso, cobertura, contagens, SHA-256 do contrato sanitizado e grupos normalizados.

- [x] **Step 2: Escrever testes falhando de retomada parcial**

  Simular sucesso A01–A04 e falha A05. Na segunda chamada, A01–A04 devem ser validadas por hash e reutilizadas; somente A05–A10 podem ser consultadas. Checkpoint incompatível com cliente, período ou contrato deve ser rejeitado.

- [x] **Step 3: Escrever testes falhando para modo inclusivo**

  Confirmar que `collect_was_period` não instancia nem chama o adaptador de análise quando `profile.was_scope.owasp_2025_mode` é inclusivo.

- [x] **Step 4: Rodar testes e confirmar RED**

  Run: `.\.venv\Scripts\python.exe -m pytest tests\test_was_owasp_collection.py tests\test_staged_execution.py -q -k "owasp or was"`

- [x] **Step 5: Implementar persistência atômica e validação de checkpoint**

  Cada categoria deve ser gravada de forma atômica com metadados mínimos e sem dados de ativos. A falha gera `WasFailureDetails` com código `WAS_OWASP_2025_EXCLUSIVE_UNAVAILABLE`, `retryable=True`, categoria falha e mensagem operacional sanitizada.

- [x] **Step 6: Integrar após a normalização WAS**

  Executar a análise somente depois que o export/chunks/findings normalizados estiverem íntegros. Qualquer falha exclusiva retorna WAS incompleto, preserva os artefatos válidos e não cria o snapshot final completo.

- [x] **Step 7: Rodar testes focados e confirmar GREEN**

  Run: `.\.venv\Scripts\python.exe -m pytest tests\test_was_owasp_collection.py tests\test_staged_execution.py -q -k "owasp or was"`

- [x] **Step 8: Commit pequeno**

  Run: `git add src/tenable_reports/application/was_owasp_2025.py src/tenable_reports/application/period_collection.py tests/test_was_owasp_collection.py tests/test_staged_execution.py`

  Run: `git commit -m "feat: persist recoverable OWASP analysis"`

---

### Task 5: Dataset, proveniência e tabelas OWASP selecionadas

**Files:**
- Modify: `src/tenable_reports/application/report_dataset.py`
- Modify: `src/tenable_reports/domain/was.py`
- Modify: `src/tenable_reports/domain/report_dataset.py`
- Modify: `tests/test_report_dataset_loader.py`
- Modify: `tests/test_was_normalization.py`
- Modify: `tests/test_report_dataset.py`
- Modify: `tests/test_full_base_report_docx.py`

**Interfaces:**
- `load_report_dataset_inputs(..., include_was: bool = True) -> ReportDatasetInputs`.
- `build_report_dataset_from_snapshot(..., include_was: bool = True) -> Path`.
- `build_was_report_data(..., owasp_2025_mode: Owasp2025Mode, exclusive_owasp_rows: Mapping[str, Sequence[Mapping[str, Any]]] | None = None) -> Mapping[str, Any]`.
- Dataset inclui `was.owasp_selection` com `requested_mode`, `effective_mode`, `source`, `coverage`, período/fuso e contagens por categoria.

- [x] **Step 1: Escrever testes falhando para seleção inclusiva e exclusiva**

  Inclusivo mantém um plugin em todas as categorias informadas pelo catálogo. Exclusivo usa somente os grupos do snapshot, de forma que um plugin multiclasse não apareça em A01 se a consulta A01 não o retornou.

- [x] **Step 2: Escrever testes falhando para ordenação e proveniência**

  As dez tabelas devem ser ordenadas por VPR decrescente e critérios atuais de desempate. `table_provenance` deve declarar `plugin_catalog_inclusive` ou `console_analysis_exclusive` sem expor payload remoto.

- [x] **Step 3: Escrever teste falhando para indisponibilidade do WAS**

  Com `include_was=False`, mesmo que `was-findings` e snapshots antigos existam, o dataset não pode publicar seções WAS nem afirmar ausência de vulnerabilidades. Isso impede que artefatos preservados para retry vazem para a tentativa falha.

- [x] **Step 4: Rodar testes e confirmar RED**

  Run: `.\.venv\Scripts\python.exe -m pytest tests\test_report_dataset_loader.py tests\test_was_normalization.py tests\test_report_dataset.py tests\test_full_base_report_docx.py -q -k "owasp or include_was or was_unavailable"`

- [x] **Step 5: Implementar carregamento condicionado e montagem por fonte**

  O domínio deve aceitar linhas exclusivas prontas e falhar se o modo exclusivo não tiver snapshot completo. O renderer continua apenas apresentando `was.owasp_top10` e não deve ganhar lógica de consulta ou classificação.

- [x] **Step 6: Rodar testes focados e confirmar GREEN**

  Run: `.\.venv\Scripts\python.exe -m pytest tests\test_report_dataset_loader.py tests\test_was_normalization.py tests\test_report_dataset.py tests\test_full_base_report_docx.py -q -k "owasp or include_was or was_unavailable"`

- [x] **Step 7: Commit pequeno**

  Run: `git add src/tenable_reports/application/report_dataset.py src/tenable_reports/domain/was.py src/tenable_reports/domain/report_dataset.py tests/test_report_dataset_loader.py tests/test_was_normalization.py tests/test_report_dataset.py tests/test_full_base_report_docx.py`

  Run: `git commit -m "feat: build OWASP tables from selected source"`

---

### Task 6: Publicação parcial, snapshot compacto e manifesto auditável

**Files:**
- Modify: `src/tenable_reports/application/compact_publication.py`
- Modify: `src/tenable_reports/application/publishing.py`
- Modify: `src/tenable_reports/cli.py`
- Modify: `tests/test_compact_publication.py`
- Modify: `tests/test_publishing.py`
- Modify: `tests/test_runtime_manifest.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- `prepare_compact_run_snapshot(..., include_was: bool = True) -> CompactFindingSnapshot`.
- `publish_compact_run_snapshot(..., include_was: bool = True) -> CompactFindingSnapshot`.
- `create_publication_manifest(..., component_metadata: Mapping[str, Any] | None = None) -> Path`.
- Metadados WAS incluem `owasp_2025.original_mode`, `effective_mode`, `source`, `coverage`, `snapshot_sha256` e `recovery_attempt`.

- [x] **Step 1: Escrever testes falhando para publicação parcial sem WAS**

  Simular análise exclusiva falha após normalização WAS. O dataset e snapshot compacto publicados devem omitir WAS, enquanto os arquivos de recuperação permanecem no disco. Documentos VM/TAG/Cloud válidos continuam publicáveis e o conjunto fica parcial.

- [x] **Step 2: Escrever testes falhando do manifesto**

  A tentativa original registra `original_mode=console_exclusive`, `effective_mode=console_exclusive`, cobertura incompleta e hash do contrato. Nenhum segredo, corpo de resposta ou identificador de ativo pode aparecer.

- [x] **Step 3: Rodar testes e confirmar RED**

  Run: `.\.venv\Scripts\python.exe -m pytest tests\test_compact_publication.py tests\test_publishing.py tests\test_runtime_manifest.py tests\test_cli.py -q -k "owasp or partial_was or include_was"`

- [x] **Step 4: Implementar o gate único de disponibilidade WAS**

  `_assemble_period_from_existing` deve receber o estado do componente e encaminhar `include_was=False` quando a análise exclusiva não estiver completa. Usar o mesmo valor no dataset, compact snapshot e documentos para evitar divergência.

  Em publicações completas, o snapshot compacto também preserva `was_collected`
  e o snapshot exclusivo A01–A10. Replay usa `include_was=True` para estados
  `COMPLETE` e `REPLAYED`, inclusive quando a população WAS é vazia.

- [x] **Step 5: Implementar metadados extensíveis no manifesto**

  Aceitar somente mappings serializáveis e sanitizados. Preservar schema existente quando o argumento não for fornecido e manter compatibilidade de leitura com manifestos antigos.

- [x] **Step 6: Rodar testes focados e confirmar GREEN**

  Run: `.\.venv\Scripts\python.exe -m pytest tests\test_compact_publication.py tests\test_publishing.py tests\test_runtime_manifest.py tests\test_cli.py -q -k "owasp or partial_was or include_was"`

- [x] **Step 7: Commit pequeno**

  Run: `git add src/tenable_reports/application/compact_publication.py src/tenable_reports/application/publishing.py src/tenable_reports/cli.py tests/test_compact_publication.py tests/test_publishing.py tests/test_runtime_manifest.py tests/test_cli.py`

  Run: `git commit -m "feat: publish partial runs without failed WAS"`

---

### Task 7: Retentativa seletiva com o modo permanente atual

**Files:**
- Modify: `src/tenable_reports/application/was_recovery.py`
- Modify: `src/tenable_reports/application/component_retry.py`
- Modify: `src/tenable_reports/application/component_status_recording.py`
- Modify: `src/tenable_reports/cli.py`
- Modify: `src/tenable_reports/webapp/server.py`
- Modify: `src/tenable_reports/webapp/static/app.js`
- Modify: `tests/test_was_recovery.py`
- Modify: `tests/test_component_retry.py`
- Modify: `tests/test_component_status_recording.py`
- Modify: `tests/test_staged_execution.py`
- Modify: `tests/test_webapp.py`
- Create: `tests/test_was_owasp_retry_ui.js`

**Interfaces:**
- `WasRecoveryCheckpoint` ganha campos opcionais compatíveis `owasp_2025_original_mode` e `owasp_2025_effective_mode`.
- Estado do componente WAS expõe modo atual do perfil e código de falha sanitizado.
- A confirmação da ação mostra `Tentar somente WAS novamente` e o modo que será usado.

- [x] **Step 1: Escrever teste falhando da primeira retomada exclusiva**

  Com A01–A04 já válidas e falha A05, o retry deve reler o perfil ainda exclusivo, reutilizar export/chunks/findings e A01–A04, consultar somente categorias pendentes, regenerar somente o relatório-base e não chamar coletores VM/TAG/Cloud.

- [x] **Step 2: Escrever teste falhando da troca permanente para inclusivo**

  Após a tentativa exclusiva falhar, editar o cliente para inclusivo e executar retry WAS. A tentativa deve reler o perfil, não chamar a análise exclusiva, reutilizar findings WAS, reconstruir tabelas inclusivas, substituir atomicamente a publicação incompleta da mesma linhagem e manter a preferência inclusiva no perfil.

- [x] **Step 3: Escrever teste falhando de idempotência e invariância**

  Comparar hashes/referências dos datasets e documentos VM/TAG/Cloud antes e depois do retry. Confirmar um único conjunto lógico, aumento do número da tentativa e ausência de export WAS novo quando o checkpoint ainda é válido.

- [x] **Step 4: Escrever testes falhando do alerta e confirmação no painel**

  O painel deve marcar WAS como falho/retentável, não marcar o cliente inteiro como falha terminal, mostrar o modo atual e acionar somente `component=was`. Não exibir mensagem interna da API.

- [x] **Step 5: Rodar testes e confirmar RED**

  Run: `.\.venv\Scripts\python.exe -m pytest tests\test_was_recovery.py tests\test_component_retry.py tests\test_component_status_recording.py tests\test_staged_execution.py tests\test_webapp.py -q -k "owasp or retry_was"`

  Run: `node --test tests/test_was_owasp_retry_ui.js`

- [x] **Step 6: Implementar leitura tardia do perfil e decisão de retomada**

  `command_resume_was` deve carregar o perfil atual imediatamente antes de decidir a fonte OWASP. Se o modo mudou para inclusivo, ignorar checkpoints exclusivos para formação das tabelas sem apagá-los; se segue exclusivo, validar e retomar as categorias pendentes.

- [x] **Step 7: Implementar substituição atômica sem duplicação**

  Reusar o mecanismo existente de documentos recuperados. Preservar os identificadores da linhagem e a evidência da tentativa falha; registrar o novo modo efetivo somente na nova tentativa/manifesto.

- [x] **Step 8: Implementar alerta e confirmação operacional**

  Mensagem sugerida: `O componente WAS não foi concluído. Tente somente o WAS novamente após validar o modo OWASP 2025 do cliente.` A confirmação inclui o rótulo do modo salvo.

- [x] **Step 9: Rodar testes focados e confirmar GREEN**

  Run: `.\.venv\Scripts\python.exe -m pytest tests\test_was_recovery.py tests\test_component_retry.py tests\test_component_status_recording.py tests\test_staged_execution.py tests\test_webapp.py -q -k "owasp or retry_was"`

  Run: `node --test tests/test_was_owasp_retry_ui.js`

- [x] **Step 10: Commit pequeno**

  Run: `git add src/tenable_reports/application/was_recovery.py src/tenable_reports/application/component_retry.py src/tenable_reports/application/component_status_recording.py src/tenable_reports/cli.py src/tenable_reports/webapp/server.py src/tenable_reports/webapp/static/app.js tests/test_was_recovery.py tests/test_component_retry.py tests/test_component_status_recording.py tests/test_staged_execution.py tests/test_webapp.py tests/test_was_owasp_retry_ui.js`

  Run: `git commit -m "feat: retry WAS with current OWASP mode"`

---

### Task 8: Documentação, DOCX sanitizado e verificação integral

**Files:**
- Modify: `CONTEXTO.md`
- Modify: `docs/02-catalogo-apis-tenable.md`
- Modify: `docs/21-catalogo-de-dados-e-metricas.md`
- Modify: `docs/22-guia-operacional.md`
- Modify: `docs/23-guia-de-desenvolvimento.md`
- Modify: `docs/README.md`
- Modify: `docs/superpowers/plans/2026-11-03-modo-owasp-2025-por-cliente.md`
- Keep: `docs/superpowers/specs/2026-11-03-modo-owasp-2025-por-cliente-design.md`

**Interfaces:**
- Documentar configuração, contrato interno isolado, fonte/proveniência, falha parcial, retry somente WAS, troca permanente de modo e limites de suporte.
- O plano marca cada checkbox apenas após evidência verificável.

- [x] **Step 1: Atualizar a documentação vigente**

  `CONTEXTO.md` registra o estado final. O catálogo de APIs diferencia claramente a rota interna de análise do contrato público WAS v2. O catálogo de dados descreve `was.owasp_selection`. O guia operacional explica como alterar permanentemente o modo e retentar somente WAS. O guia de desenvolvimento descreve adapter, checkpoints, testes e sanitização.

- [x] **Step 2: Gerar fixtures DOCX sanitizadas nos dois modos**

  Usar os mesmos findings sintéticos, incluindo plugin multiclasse, para comprovar a diferença entre inclusivo e exclusivo. Não usar cliente, hostname, IP ou ativo real.

- [x] **Step 3: Renderizar com o LibreOffice homologado e inspecionar visualmente**

  Run: `.\.venv\Scripts\python.exe .\scripts\prepare_docx_qa_render.py <inclusivo.docx> <inclusivo-static.docx>`

  Run: `.\.venv\Scripts\python.exe .\scripts\prepare_docx_qa_render.py <exclusivo.docx> <exclusivo-static.docx>`

  Run: `C:\Codex\LibreOfficePortable\App\libreoffice\program\soffice.com --headless --convert-to pdf --outdir <qa-dir> <inclusivo-static.docx> <exclusivo-static.docx>`

  Expected: mesmas seções e estilo; diferença apenas nas linhas aprovadas; tabelas sem cortes, sobreposição ou desalinhamento.

- [x] **Step 4: Rodar toda a suíte Python**

  Run: `$env:PYTHONPATH = (Join-Path $PWD 'src'); .\.venv\Scripts\python.exe -m pytest -q`

- [x] **Step 5: Rodar toda a suíte JavaScript**

  Run: `Get-ChildItem tests -Filter '*.js' | ForEach-Object { node --test $_.FullName }`

- [x] **Step 6: Validar orientação, segredos e diff**

  Run: `.\.venv\Scripts\python.exe tools\validate_project_guidance.py --root .`

  Run: `.\.venv\Scripts\python.exe tools\audit_secret_leaks.py`

  Run: `git diff --check`

- [x] **Step 7: Revisar segurança e regressão**

  Confirmar que não há chamada remota em modo inclusivo, que testes não usam credenciais, que falhas não viram zero, que os demais componentes não são repetidos e que nenhum dado exclusivo parcial chega ao cliente.

- [x] **Step 8: Commit documental e de encerramento**

  Run: `git add CONTEXTO.md docs/02-catalogo-apis-tenable.md docs/21-catalogo-de-dados-e-metricas.md docs/22-guia-operacional.md docs/23-guia-de-desenvolvimento.md docs/README.md docs/superpowers/specs/2026-11-03-modo-owasp-2025-por-cliente-design.md docs/superpowers/plans/2026-11-03-modo-owasp-2025-por-cliente.md`

  Run: `git commit -m "docs: document per-client OWASP modes"`

---

## Critérios de aceite finais

- Perfil antigo e novo cliente usam inclusivo sem chamada extra.
- Cliente exclusivo produz A01–A10 a partir da análise exclusiva paginada e persistida.
- As linhas são ordenadas por VPR decrescente.
- Falha de qualquer categoria exclusiva falha somente o WAS e mantém os outros documentos.
- O painel oferece retry somente WAS e informa o modo permanente atual.
- Retry exclusivo reaproveita categorias completas e artefatos WAS válidos.
- Alterar permanentemente para inclusivo e retentar não consulta a análise exclusiva.
- VM, TAG e Cloud permanecem idênticos durante a recuperação WAS.
- Dataset e manifesto registram modo original, modo efetivo, fonte, cobertura e hash sanitizado.
- Não existem conjuntos ou documentos duplicados após a recuperação.
- Suítes Python/JavaScript, validação documental, auditoria de segredos, `git diff --check` e QA LibreOffice passam.
