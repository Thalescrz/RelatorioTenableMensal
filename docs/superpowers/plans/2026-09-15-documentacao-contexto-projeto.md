# Consolidação da Documentação e Contexto do Projeto Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Criar `CONTEXTO.md`, reconciliar toda a documentação versionada com o comportamento atual e distinguir fontes vigentes de registros históricos sem apagar decisões passadas.

**Architecture:** `CONTEXTO.md` será o mapa de retomada, enquanto `DESIGN.md` e `docs/19` a `docs/23` continuarão como contratos detalhados por assunto. O validador percorrerá todos os arquivos Markdown documentais para verificar links, mas aplicará regras de rascunho somente às fontes vigentes; documentos históricos receberão um aviso uniforme e manterão o corpo original.

**Tech Stack:** Markdown, Python 3.12, `unittest`/`pytest`, PowerShell, Git.

**Spec:** `docs/superpowers/specs/2026-09-15-documentacao-contexto-projeto-design.md`

## Global Constraints

- Não alterar comportamento da aplicação, modelos de dados ou formato dos DOCX.
- Não executar coleta real, migração, publicação de relatório ou modificação de banco.
- Não registrar clientes, credenciais, e-mails, UUIDs, hostnames, IPs ou estados operacionais transitórios.
- Código e testes prevalecem sobre descrições documentais divergentes.
- `CONTEXTO.md` resume e direciona; não duplica integralmente os guias detalhados.
- O corpo de `docs/01` a `docs/18` e dos registros anteriores em `docs/superpowers` deve ser preservado.
- Planos históricos não podem ser declarados concluídos sem evidência em código, teste ou commit.
- Exemplos versionados usam somente nomes e identificadores fictícios.
- Toda edição deve respeitar `AGENTS.md` da raiz e as instruções específicas do diretório.

---

### Task 1: Criar o contexto e proteger sua integridade no validador

**Files:**
- Create: `CONTEXTO.md`
- Modify: `tools/validate_project_guidance.py`
- Modify: `tests/test_project_guidance.py`

**Interfaces:**
- Consumes: `validate_guidance(root: Path) -> tuple[GuidanceIssue, ...]` e a lista `REQUIRED_GUIDANCE_FILES`.
- Produces: `_iter_markdown_files(root: Path) -> tuple[Path, ...]`, `CONTEXTO.md` obrigatório e validação de links em todo Markdown documental.

- [ ] **Step 1: Acrescentar testes que expressem os três novos contratos**

Em `tests/test_project_guidance.py`, acrescente `CONTEXTO.md` a `REQUIRED_FILES` e implemente:

```python
def test_requires_project_context_document(self) -> None:
    self.write_minimum_valid_tree()
    (self.root / "CONTEXTO.md").unlink()

    issues = validate_guidance(self.root)

    self.assertIn(
        ("MISSING_REQUIRED_FILE", "CONTEXTO.md"),
        {(item.code, item.path) for item in issues},
    )

def test_reports_broken_link_in_historical_markdown(self) -> None:
    self.write_minimum_valid_tree()
    historical = self.root / "docs/01-historico.md"
    historical.write_text("[Ausente](referencia-ausente.md)\n", encoding="utf-8")

    issues = validate_guidance(self.root)

    self.assertIn(
        ("BROKEN_LOCAL_LINK", "docs/01-historico.md"),
        {(item.code, item.path) for item in issues},
    )

def test_ignores_runtime_markdown_directories(self) -> None:
    self.write_minimum_valid_tree()
    runtime = self.root / ".tmp/session/nota.md"
    runtime.parent.mkdir(parents=True)
    runtime.write_text("[Ausente](arquivo.md)\n", encoding="utf-8")

    self.assertEqual(validate_guidance(self.root), ())
```

- [ ] **Step 2: Executar os testes e confirmar a falha contratual**

Run:

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp .tmp\pytest\context-red tests\test_project_guidance.py
```

Expected: falhas em `test_requires_project_context_document` e `test_reports_broken_link_in_historical_markdown`, porque o contexto ainda não é obrigatório e arquivos históricos ainda não são percorridos.

- [ ] **Step 3: Implementar a descoberta segura de Markdown**

Em `tools/validate_project_guidance.py`, acrescente `CONTEXTO.md` a `REQUIRED_GUIDANCE_FILES` e implemente:

```python
IGNORED_MARKDOWN_PARTS = frozenset(
    {".git", ".venv", ".tmp", "data", "credentials", "node_modules"}
)

def _iter_markdown_files(root: Path) -> tuple[Path, ...]:
    files = (
        path
        for path in root.rglob("*.md")
        if path.is_file()
        and not IGNORED_MARKDOWN_PARTS.intersection(path.relative_to(root).parts)
    )
    return tuple(sorted(files, key=lambda path: path.relative_to(root).as_posix()))
```

Mantenha a verificação de arquivo obrigatório e marcador de rascunho no laço de `REQUIRED_GUIDANCE_FILES`. Remova dali a chamada duplicada a `_validate_local_links` e, após esse laço, valide links uma única vez:

```python
for path in _iter_markdown_files(root):
    text = path.read_text(encoding="utf-8")
    issues.extend(_validate_local_links(root, path, text))
```

- [ ] **Step 4: Criar `CONTEXTO.md` com o contrato consolidado**

Use exatamente esta ordem de seções e derive cada afirmação dos arquivos indicados:

```markdown
# Contexto do projeto

**Atualizado em:** 2026-09-15
**Base verificada:** commit da branch no momento da revisão
**Natureza:** resumo versionado; estado operacional transitório fica fora deste arquivo

## Finalidade
## Fontes de verdade e precedência
## Estado consolidado
## Relatórios entregues
## Arquitetura resumida
## Módulos VM, WAS e Cloud
## Controle de documento
## Períodos, identidade e métricas
## Lotes, checkpoints e retentativas
## PostgreSQL, MAIN e armazenamento
## Interface web
## Segurança e privacidade
## Ambiente e comandos seguros
## Fluxo Git
## Mapa da documentação
## Limites confirmados
## Como manter este contexto
```

Conteúdo obrigatório:

- quatro entregáveis: geral, customizado, TAG e Cloud padrão;
- TAG como recorte local do dataset VM geral por UUID;
- WAS e Cloud opcionais, independentes e incapazes de invalidar VM concluído;
- padrão global de Preparação e Versionamento, distribuição global seguida por adicionais do cliente;
- período interno `[início, fim)`, `last_found`, `last_fixed` e exclusão de `Informational`;
- coleta remota concorrente, montagem local serial e checkpoints preservados;
- PostgreSQL como autoridade de histórico, documentos, tentativas e `MAIN`;
- atualização adaptativa do dashboard, cartões reconciliados, módulos visíveis e corte de leitura de alertas;
- interface local sem autenticação multiusuário e não destinada à exposição pública;
- fluxo Git com `main` e uma branch `codex/*` por ciclo;
- comandos de instalação, interface e quatro verificações mínimas do `AGENTS.md`;
- links para `README.md`, `DESIGN.md`, `docs/README.md` e `docs/19` a `docs/23`.

- [ ] **Step 5: Executar a suíte do validador**

Run:

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp .tmp\pytest\context-green tests\test_project_guidance.py
.\.venv\Scripts\python.exe tools\validate_project_guidance.py --root .
```

Expected: todos os testes passam e o validador retorna JSON com `status` igual a `ok`.

- [ ] **Step 6: Commit**

```powershell
git add CONTEXTO.md tools/validate_project_guidance.py tests/test_project_guidance.py
git commit -m "docs: criar contexto consolidado do projeto"
```

---

### Task 2: Reconciliar as fontes vigentes de entrada e produto

**Files:**
- Modify: `README.md`
- Modify: `DESIGN.md`
- Modify: `docs/README.md`
- Modify: `docs/19-visao-geral-e-objetivos.md`

**Interfaces:**
- Consumes: `CONTEXTO.md` e a hierarquia definida na especificação.
- Produces: caminho de leitura inequívoco para novos operadores e desenvolvedores.

- [ ] **Step 1: Atualizar o início de `README.md`**

Após o primeiro parágrafo, adicione uma chamada curta para `[CONTEXTO.md](CONTEXTO.md)` como melhor ponto de retomada. Preserve o início rápido e reduza apenas explicações repetidas integralmente no contexto; mantenha links para os guias detalhados.

- [ ] **Step 2: Atualizar `DESIGN.md`**

Altere o status para `arquitetura vigente em 2026-09-15` e acrescente às decisões estruturais:

- controle documental global para Preparação, Versionamento e distribuição comum;
- destinatários adicionais por cliente anexados depois da distribuição global;
- atualização visual do dashboard por reconciliação, sem recriar cartões inalterados;
- alertas reconhecidos por corte temporal sem apagar jobs, checkpoints ou histórico.

Inclua `CONTEXTO.md` na seção final de referências, mantendo `DESIGN.md` como contrato estrutural, não como manual operacional.

- [ ] **Step 3: Transformar `docs/README.md` no índice completo**

Adicione uma seção inicial `Ordem de leitura` com:

1. `../CONTEXTO.md` para retomada;
2. `../README.md` para início rápido;
3. `../DESIGN.md` para invariantes;
4. `19` a `23` para contratos vigentes;
5. `01` a `18` e `superpowers` para histórico.

Liste também `AGENTS.md`, skills locais e `templates/corporate/README.md` em uma seção `Instruções especializadas`.

- [ ] **Step 4: Reconciliar `docs/19-visao-geral-e-objetivos.md`**

Confirme os quatro entregáveis e atualize `Princípios de negócio`, `Limites atuais` e `Critério de sucesso` com:

- módulos ativos visíveis por cliente;
- padrões documentais globais e distribuição adicional por cliente;
- alertas reconhecíveis sem exclusão do histórico;
- interface exclusivamente local;
- ausência de distribuição remota automática.

- [ ] **Step 5: Validar links e termos de produto**

Run:

```powershell
.\.venv\Scripts\python.exe tools\validate_project_guidance.py --root .
rg -n "CONTEXTO.md|quatro|distribuição|interface local|alertas" README.md DESIGN.md docs\README.md docs\19-visao-geral-e-objetivos.md
git diff --check
```

Expected: validador aprovado, referências ao contexto presentes e nenhum erro de whitespace.

- [ ] **Step 6: Commit**

```powershell
git add README.md DESIGN.md docs/README.md docs/19-visao-geral-e-objetivos.md
git commit -m "docs: reconciliar visao atual do produto"
```

---

### Task 3: Atualizar arquitetura e catálogo de dados

**Files:**
- Modify: `docs/20-arquitetura-e-fluxo-de-dados.md`
- Modify: `docs/21-catalogo-de-dados-e-metricas.md`

**Interfaces:**
- Consumes: contratos em `src/tenable_reports`, migrations em `src/tenable_reports/infrastructure/postgresql/migrations` e rotas em `src/tenable_reports/webapp/server.py`.
- Produces: descrição técnica vigente de fluxo, persistência, controle documental e estado de interface.

- [ ] **Step 1: Confirmar os contratos executáveis antes da edição**

Run:

```powershell
rg -n "document-control|distribution|alerts_read_before|STAGED_V1|REMOTE_RUNNING|BUILD_RUNNING" src tests
rg -n "CREATE TABLE|ALTER TABLE" src\tenable_reports\infrastructure\postgresql\migrations
```

Expected: localizar a persistência de controle documental, o corte de alertas e os estados duráveis citados nos guias.

- [ ] **Step 2: Atualizar `docs/20-arquitetura-e-fluxo-de-dados.md`**

Acrescente ou reconcilie três fluxos explícitos:

```text
Admin -> padrão global de documento -> renderizadores Geral/Cloud
Cliente -> destinatários adicionais -> linhas posteriores da Lista de Distribuição

/api/state -> coordenador single-flight -> reconciliação dos cartões
                              \-> polling rápido com trabalho / lento em repouso

Marcar alertas como lidos -> alerts_read_before local
                          -> filtra apresentação
                          -> não altera PostgreSQL, job ou checkpoint
```

Mantenha a coleta geral independente de TAG e a separação entre coletas remotas e montagem local.

- [ ] **Step 3: Atualizar `docs/21-catalogo-de-dados-e-metricas.md`**

Inclua um bloco `Dados de configuração e apresentação` distinguindo:

- configuração global de controle de documento;
- destinatários adicionais armazenados no perfil do cliente;
- `alerts_read_before` como preferência local da interface, não métrica de domínio;
- módulos ativos como capacidades do perfil, não resultado de coleta;
- status apresentado como projeção visual que não reescreve o status persistido.

Preserve integralmente as regras VM/WAS/Cloud, período, severidade, TAG e histórico.

- [ ] **Step 4: Executar os testes documentais relacionados**

Run:

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp .tmp\pytest\architecture-docs tests\test_project_guidance.py
.\.venv\Scripts\python.exe tools\validate_project_guidance.py --root .
git diff --check
```

Expected: testes e validador aprovados.

- [ ] **Step 5: Commit**

```powershell
git add docs/20-arquitetura-e-fluxo-de-dados.md docs/21-catalogo-de-dados-e-metricas.md
git commit -m "docs: atualizar arquitetura e contratos de dados"
```

---

### Task 4: Reconciliar operação, desenvolvimento e instruções especializadas

**Files:**
- Modify: `docs/22-guia-operacional.md`
- Modify: `docs/23-guia-de-desenvolvimento.md`
- Modify: `AGENTS.md`
- Modify: `clients/AGENTS.md`
- Modify: `src/tenable_reports/AGENTS.md`
- Modify: `tests/AGENTS.md`
- Modify: `.agents/skills/operating-tenable-reports/SKILL.md`
- Modify: `.agents/skills/operating-tenable-reports/references/runbook.md`
- Modify: `.agents/skills/validating-tenable-report-data/SKILL.md`
- Modify: `.agents/skills/validating-tenable-report-data/references/data-contract.md`
- Modify: `templates/corporate/README.md`

**Interfaces:**
- Consumes: rotas da webapp, opções da CLI, contratos editoriais e `CONTEXTO.md`.
- Produces: procedimentos coerentes para operador, desenvolvedor e agentes automatizados.

- [ ] **Step 1: Validar comandos sem executar operação real**

Run:

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
.\.venv\Scripts\python.exe -m tenable_reports --help
.\.venv\Scripts\python.exe -m tenable_reports run-client --help
.\.venv\Scripts\python.exe -m tenable_reports run-monthly-batch --help
.\.venv\Scripts\python.exe -m tenable_reports import-web-batch-recovery --help
```

Expected: ajuda exibida com exit code zero e nenhuma chamada externa.

- [ ] **Step 2: Atualizar o guia operacional**

Em `docs/22-guia-operacional.md`, confirme e organize:

- Admin como único local de edição dos padrões globais de Preparação,
  Versionamento e Lista de Distribuição;
- Gerenciar clientes contendo apenas adicionais individuais da distribuição;
- cartões exibindo módulos `VM`, `WAS` e `CLOUD`, sem IDs técnicos;
- atualização adaptativa e preservação de foco/rolagem;
- `Marcar todos como lidos`, ocultação de alertas e recuperações WAS anteriores ao
  corte, e reaparecimento de eventos novos;
- início e encerramento do servidor local;
- comandos CLI confirmados no passo anterior.

- [ ] **Step 3: Atualizar o guia de desenvolvimento**

Em `docs/23-guia-de-desenvolvimento.md`, documente:

- `CONTEXTO.md` como artefato obrigatório;
- responsabilidade de `dashboard_refresh.js`, `client_card.js` e
  `dashboard_alerts.js`;
- persistência ignorada de `orchestration/dashboard-alerts.json`;
- validação de todos os links Markdown e escopo limitado dos marcadores de
  rascunho;
- checklist de atualização conjunta entre contexto e guia afetado.

- [ ] **Step 4: Reconciliar `AGENTS.md` e instruções por diretório**

Inclua `CONTEXTO.md` entre as fontes vigentes no `AGENTS.md` raiz. Nos três
`AGENTS.md` específicos, acrescente apenas o link e a responsabilidade pertinente:

- `clients/AGENTS.md`: personalização por cliente nunca substitui o padrão global;
- `src/tenable_reports/AGENTS.md`: mudança comportamental atualiza contexto e guia;
- `tests/AGENTS.md`: fixtures documentais continuam sanitizadas e usam base temp no workspace.

- [ ] **Step 5: Corrigir skills, runbooks e contrato de dados**

Na skill operacional, substitua a restrição desatualizada sobre Cloud por:

```markdown
- Não apresente o Cloud como reconstrução histórica do período: ele representa a fotografia preservada no instante da coleta.
```

No runbook, acrescente referências ao contexto, controle documental, cartões,
leitura de alertas e encerramento da interface. Na skill de validação e em
`data-contract.md`, alinhe fotografia Cloud, ausência de dados e prevalência dos
contratos vigentes sem mudar as regras métricas.

- [ ] **Step 6: Atualizar o contrato do template corporativo**

Em `templates/corporate/README.md`, referencie `CONTEXTO.md` e explicite que Geral e
Cloud recebem as tabelas globais de Preparação e Versionamento e a distribuição
global seguida pelos destinatários adicionais do cliente.

- [ ] **Step 7: Validar instruções especializadas**

Run:

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp .tmp\pytest\operations-docs tests\test_project_guidance.py
.\.venv\Scripts\python.exe tools\validate_project_guidance.py --root .
git diff --check
```

Expected: frontmatter das skills, links e contratos documentais aprovados.

- [ ] **Step 8: Commit**

```powershell
git add docs/22-guia-operacional.md docs/23-guia-de-desenvolvimento.md AGENTS.md clients/AGENTS.md src/tenable_reports/AGENTS.md tests/AGENTS.md .agents/skills templates/corporate/README.md
git commit -m "docs: alinhar operacao e desenvolvimento"
```

---

### Task 5: Identificar `docs/01` a `docs/18` como referências históricas

**Files:**
- Modify: `docs/01-analise-e-arquitetura.md`
- Modify: `docs/02-catalogo-apis-tenable.md`
- Modify: `docs/03-protocolo-analise-docx.md`
- Modify: `docs/04-matriz-e-contrato-dos-relatorios.md`
- Modify: `docs/05-historico-regras-criticas-e-traducao.md`
- Modify: `docs/06-modelo-normalizado-fase3.md`
- Modify: `docs/07-dataset-mensal-fase4.md`
- Modify: `docs/08-template-word-fase5.md`
- Modify: `docs/09-relatorio-base-completo-fase6.md`
- Modify: `docs/10-escopo-tags-e-comparativo-por-rede.md`
- Modify: `docs/11-catalogo-visual-e-tabelas-customizadas.md`
- Modify: `docs/12-perfis-e-variacoes-fase7.md`
- Modify: `docs/13-was-fase8.md`
- Modify: `docs/14-historico-e-tendencias-fase9.md`
- Modify: `docs/15-orquestracao-e-distribuicao-fase10.md`
- Modify: `docs/16-armazenamento-e-reciclagem.md`
- Modify: `docs/16-postgresql-migracao-e-operacao.md`
- Modify: `docs/17-interface-web-mvp.md`
- Modify: `docs/18-main-retentativas-inteligencia-operacao.md`

**Interfaces:**
- Consumes: `CONTEXTO.md` e `docs/README.md`.
- Produces: aviso histórico uniforme sem alteração do contrato original registrado.

- [ ] **Step 1: Inserir o aviso uniforme após o primeiro título**

Use exatamente este bloco nos 19 arquivos:

```markdown
> **Referência histórica:** este documento registra uma etapa da evolução do projeto
> e pode conter decisões posteriormente ampliadas ou substituídas. Consulte o
> [contexto atual](../CONTEXTO.md) e o [índice da documentação](README.md) antes de
> usá-lo como orientação vigente.
```

- [ ] **Step 2: Confirmar que somente o cabeçalho foi acrescentado**

Run:

```powershell
git diff -- docs/01-analise-e-arquitetura.md docs/02-catalogo-apis-tenable.md docs/03-protocolo-analise-docx.md docs/04-matriz-e-contrato-dos-relatorios.md docs/05-historico-regras-criticas-e-traducao.md docs/06-modelo-normalizado-fase3.md docs/07-dataset-mensal-fase4.md docs/08-template-word-fase5.md docs/09-relatorio-base-completo-fase6.md docs/10-escopo-tags-e-comparativo-por-rede.md docs/11-catalogo-visual-e-tabelas-customizadas.md docs/12-perfis-e-variacoes-fase7.md docs/13-was-fase8.md docs/14-historico-e-tendencias-fase9.md docs/15-orquestracao-e-distribuicao-fase10.md docs/16-armazenamento-e-reciclagem.md docs/16-postgresql-migracao-e-operacao.md docs/17-interface-web-mvp.md docs/18-main-retentativas-inteligencia-operacao.md
```

Expected: quatro linhas de aviso e uma linha em branco adicionadas após cada `#`, sem alteração do restante do corpo.

- [ ] **Step 3: Validar links históricos**

Run:

```powershell
.\.venv\Scripts\python.exe tools\validate_project_guidance.py --root .
git diff --check
```

Expected: links relativos para contexto e índice aprovados.

- [ ] **Step 4: Commit**

```powershell
git add docs/01-analise-e-arquitetura.md docs/02-catalogo-apis-tenable.md docs/03-protocolo-analise-docx.md docs/04-matriz-e-contrato-dos-relatorios.md docs/05-historico-regras-criticas-e-traducao.md docs/06-modelo-normalizado-fase3.md docs/07-dataset-mensal-fase4.md docs/08-template-word-fase5.md docs/09-relatorio-base-completo-fase6.md docs/10-escopo-tags-e-comparativo-por-rede.md docs/11-catalogo-visual-e-tabelas-customizadas.md docs/12-perfis-e-variacoes-fase7.md docs/13-was-fase8.md docs/14-historico-e-tendencias-fase9.md docs/15-orquestracao-e-distribuicao-fase10.md docs/16-armazenamento-e-reciclagem.md docs/16-postgresql-migracao-e-operacao.md docs/17-interface-web-mvp.md docs/18-main-retentativas-inteligencia-operacao.md
git commit -m "docs: sinalizar referencias historicas"
```

---

### Task 6: Identificar especificações e planos anteriores como registros de decisão

**Files:**
- Modify: todos os arquivos já existentes antes de 2026-09-15 em `docs/superpowers/specs/*.md`
- Modify: todos os arquivos já existentes antes de 2026-09-15 em `docs/superpowers/plans/*.md`
- Preserve: `docs/superpowers/specs/2026-09-15-documentacao-contexto-projeto-design.md`
- Preserve: `docs/superpowers/plans/2026-09-15-documentacao-contexto-projeto.md`

**Interfaces:**
- Consumes: `CONTEXTO.md` e `docs/README.md`.
- Produces: banner uniforme que impede planos antigos de serem interpretados como estado atual.

- [ ] **Step 1: Inserir o aviso após o primeiro título de cada registro anterior**

Use exatamente:

```markdown
> **Registro de decisão:** este arquivo preserva o desenho ou plano considerado no
> momento da entrega. Ele não comprova sozinho o estado atual nem a conclusão de
> cada etapa. Consulte o [contexto atual](../../../CONTEXTO.md) e o
> [índice da documentação](../../README.md).
```

Não altere checkboxes, comandos, critérios ou conclusões existentes.

- [ ] **Step 2: Confirmar cobertura e preservação**

Run:

```powershell
$previousSpecs = @(Get-ChildItem docs\superpowers\specs -Filter '*.md' | Where-Object Name -NotLike '2026-09-15-*')
$previousPlans = @(Get-ChildItem docs\superpowers\plans -Filter '*.md' | Where-Object Name -NotLike '2026-09-15-*')
$marked = @(Select-String -Path ($previousSpecs.FullName + $previousPlans.FullName) -Pattern '^> \*\*Registro de decisão:\*\*')
[pscustomobject]@{expected=$previousSpecs.Count + $previousPlans.Count; marked=$marked.Count}
```

Expected: `expected` igual a `marked`, uma ocorrência por arquivo.

- [ ] **Step 3: Validar links e diff**

Run:

```powershell
.\.venv\Scripts\python.exe tools\validate_project_guidance.py --root .
git diff --check
```

Expected: validador aprovado e nenhum erro de whitespace.

- [ ] **Step 4: Commit**

```powershell
git add docs/superpowers/specs docs/superpowers/plans
git commit -m "docs: classificar registros de decisao anteriores"
```

---

### Task 7: Reconciliar o inventário e executar a verificação integral

**Files:**
- Modify: qualquer fonte vigente que ainda apresente link ou afirmação divergente, limitada aos arquivos listados nas Tasks 1 a 4.
- Test: `tests/test_project_guidance.py`

**Interfaces:**
- Consumes: todos os artefatos produzidos nas Tasks 1 a 6.
- Produces: árvore documental coerente, sanitizada e validada.

- [ ] **Step 1: Conferir a cobertura do inventário Markdown**

Run:

```powershell
$all = @(Get-ChildItem -Recurse -Filter '*.md' | Where-Object FullName -NotMatch '\\(?:\.git|\.venv|\.tmp|data|credentials|node_modules)\\')
$historical = @($all | Where-Object FullName -Match '\\docs\\(?:0[1-9]|1[0-8])-|\\docs\\superpowers\\(?:specs|plans)\\2026-(?:08|09-0)')
[pscustomobject]@{all_markdown=$all.Count; historical_markdown=$historical.Count}
```

Expected: todo Markdown documental aparece no inventário; arquivos temporários e de dados ficam fora.

- [ ] **Step 2: Validar a ajuda da CLI documentada**

Run:

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
.\.venv\Scripts\python.exe -m tenable_reports --help
.\.venv\Scripts\python.exe -m tenable_reports run-client --help
.\.venv\Scripts\python.exe -m tenable_reports run-monthly-batch --help
.\.venv\Scripts\python.exe -m tenable_reports import-web-batch-recovery --help
```

Expected: todos os comandos retornam exit code zero sem iniciar coleta.

- [ ] **Step 3: Executar testes e validadores completos**

Run:

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp .tmp\pytest\documentation-full tests
.\.venv\Scripts\python.exe tools\validate_project_guidance.py --root .
.\.venv\Scripts\python.exe tools\audit_secret_leaks.py
git diff --check
```

Expected: suíte completa aprovada, guia com `status: ok`, `leaks=0` e diff sem erros.

- [ ] **Step 4: Revisar a preservação histórica**

Run:

```powershell
git diff --word-diff=porcelain 277750f -- docs/01-analise-e-arquitetura.md docs/02-catalogo-apis-tenable.md docs/03-protocolo-analise-docx.md docs/04-matriz-e-contrato-dos-relatorios.md docs/05-historico-regras-criticas-e-traducao.md docs/06-modelo-normalizado-fase3.md docs/07-dataset-mensal-fase4.md docs/08-template-word-fase5.md docs/09-relatorio-base-completo-fase6.md docs/10-escopo-tags-e-comparativo-por-rede.md docs/11-catalogo-visual-e-tabelas-customizadas.md docs/12-perfis-e-variacoes-fase7.md docs/13-was-fase8.md docs/14-historico-e-tendencias-fase9.md docs/15-orquestracao-e-distribuicao-fase10.md docs/16-armazenamento-e-reciclagem.md docs/16-postgresql-migracao-e-operacao.md docs/17-interface-web-mvp.md docs/18-main-retentativas-inteligencia-operacao.md docs/superpowers/specs docs/superpowers/plans
```

Expected: documentos anteriores recebem somente os avisos aprovados; a especificação e este plano de 2026-09-15 permanecem como registros atuais do ciclo.

- [ ] **Step 5: Fazer a revisão final do contexto**

Confirme manualmente que `CONTEXTO.md`:

- não contém valores de clientes ou estado transitório;
- não promete capacidades ausentes;
- diferencia fotografia Cloud de reconstrução histórica;
- aponta para cada guia vigente;
- registra a data e o commit-base reais usados na revisão;
- explica quando deve ser atualizado.

- [ ] **Step 6: Registrar correções finais, se houver**

Se a reconciliação dos passos anteriores exigir ajustes nas fontes vigentes, aplique-os e execute novamente o Step 3. Depois:

```powershell
git add CONTEXTO.md README.md DESIGN.md docs/README.md docs/19-visao-geral-e-objetivos.md docs/20-arquitetura-e-fluxo-de-dados.md docs/21-catalogo-de-dados-e-metricas.md docs/22-guia-operacional.md docs/23-guia-de-desenvolvimento.md AGENTS.md clients/AGENTS.md src/tenable_reports/AGENTS.md tests/AGENTS.md .agents/skills templates/corporate/README.md tools/validate_project_guidance.py tests/test_project_guidance.py
git commit -m "docs: finalizar reconciliacao documental"
```

Se não houver ajuste adicional, não crie commit vazio.

- [ ] **Step 7: Entregar o estado da branch**

Run:

```powershell
git status --short
git log --oneline --decorate main..HEAD
```

Expected: worktree limpo e commits documentais pequenos, todos na branch `codex/atualizar-documentacao-contexto`.
