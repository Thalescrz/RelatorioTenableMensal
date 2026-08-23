# Documentação, AGENTS e Skills do Projeto — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Criar uma camada documental atual e verificável para analistas, desenvolvedores e agentes, corrigindo documentos legados e adicionando instruções e skills específicas do projeto.

**Architecture:** A documentação consolidada passa a ser a porta de entrada e encaminha para contratos históricos sem duplicá-los. Instruções `AGENTS.md` são aplicadas por escopo; duas skills de referência em `.agents/skills` cobrem operação e validação de dados, com detalhes condicionais em `references/`.

**Tech Stack:** Markdown, Python 3.11+, `unittest`/pytest, Agent Skills (`SKILL.md` com YAML frontmatter), PowerShell no Windows.

**Spec:** `docs/superpowers/specs/2026-08-23-documentacao-agents-skills-design.md`

## Global Constraints

- Não alterar métricas, coleta, interface ou documentos Word.
- Não iniciar exports reais, servidor ou acesso a credenciais durante esta entrega.
- Não incorporar `.env`, chaves, senhas, hostname, IP, URI interna ou nome de pessoa.
- Preservar documentos históricos e evidências datadas; corrigir apenas fatos que contradizem a implementação atual.
- Código e testes executáveis têm precedência sobre a documentação.
- `Plugin Output` permanece opt-in e potencialmente sensível.
- TAGs nunca filtram a coleta nem os documentos gerais; cada comparativo usa a mesma TAG em períodos compatíveis.
- PostgreSQL é a fonte operacional do histórico; staging pesado é efêmero após publicação confirmada.
- Cloud Security e tradução permanecem não implementados.
- Preservar as alterações locais já existentes em `src/tenable_reports/cli.py` e `tests/test_cli.py`; todos os commits usam `git add --` seguido somente dos caminhos listados em cada tarefa.

---

### Task 1: Validador da documentação e das instruções

> **Revisão antes da execução:** as etapas originais desta Task 1 que testam presença
> ou conteúdo dos manuais foram substituídas pelo contrato abaixo. Prosa humana não
> será testada por busca textual; o teste cobre o comportamento de um validador real.

**Contrato de execução revisado:**

- Create: `tools/validate_project_guidance.py`
- Create: `tests/test_project_guidance.py`
- Produces: `validate_guidance(root: Path) -> tuple[GuidanceIssue, ...]` e uma CLI
  que retorna zero somente quando arquivos obrigatórios, links locais e frontmatter
  são válidos.

- [ ] **Step R1: Escrever testes falhando com árvores temporárias**

Os testes importam `validate_guidance` e verificam quatro comportamentos: arquivo
obrigatório ausente gera `MISSING_REQUIRED_FILE`; link local quebrado gera
`BROKEN_LOCAL_LINK`; frontmatter inválido gera `INVALID_SKILL_FRONTMATTER`; árvore
completa e válida retorna tupla vazia.

- [ ] **Step R2: Confirmar RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_project_guidance.py -q`

Expected: erro de importação porque o validador ainda não existe.

- [ ] **Step R3: Implementar o validador mínimo**

`tools/validate_project_guidance.py` define `GuidanceIssue(code, path, message)`,
aceita `--root` e valida a lista aprovada de documentos, `AGENTS.md`, skills e
referências. Links externos, `mailto:`, imagens e âncoras puras são ignorados; links
locais são resolvidos relativamente ao arquivo. Skills exigem `name` igual ao
diretório e `description` iniciada por `Use when`. Marcadores `TODO`, `TBD` e
`fill in` são recusados nos artefatos obrigatórios.

- [ ] **Step R4: Confirmar GREEN e o gap do repositório**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_project_guidance.py -q
.\.venv\Scripts\python.exe tools\validate_project_guidance.py --root .
```

Expected: testes passam; a CLI retorna `1` apenas porque manuais, `AGENTS.md` e
skills ainda serão criados nas tarefas seguintes.

- [ ] **Step R5: Commit do validador testado**

**Files:**
- Create: `tests/test_project_guidance.py`
- Test: `tests/test_project_guidance.py`

**Interfaces:**
- Consumes: árvore do repositório e arquivos Markdown versionados.
- Produces: helpers locais `local_markdown_targets(path: Path) -> tuple[Path, ...]` e `skill_frontmatter(path: Path) -> dict[str, str]`, usados somente pelos testes deste plano.

- [ ] **Step 1: Escrever o teste de arquivos consolidados ausentes**

Criar `tests/test_project_guidance.py` com `unittest`. O primeiro teste deve exigir exatamente:

```python
CONSOLIDATED_DOCS = (
    "docs/README.md",
    "docs/19-visao-geral-e-objetivos.md",
    "docs/20-arquitetura-e-fluxo-de-dados.md",
    "docs/21-catalogo-de-dados-e-metricas.md",
    "docs/22-guia-operacional.md",
    "docs/23-guia-de-desenvolvimento.md",
)

def test_consolidated_documentation_exists(self) -> None:
    missing = [name for name in CONSOLIDATED_DOCS if not (ROOT / name).is_file()]
    self.assertEqual(missing, [])
```

- [ ] **Step 2: Executar o teste para confirmar RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_project_guidance.py::ProjectGuidanceTests::test_consolidated_documentation_exists -q
```

Expected: `FAIL`, listando os seis arquivos ainda inexistentes.

- [ ] **Step 3: Completar o validador de links locais**

Adicionar um extrator de links Markdown que ignore `http:`, `https:`, `mailto:`, imagens e âncoras puras. Resolver caminhos relativos ao documento e remover o fragmento `#...`. O teste deve percorrer somente `README.md`, os seis documentos consolidados, os quatro `AGENTS.md` e as duas árvores de skills quando existirem:

```python
def test_local_links_in_project_guidance_resolve(self) -> None:
    broken = []
    for source in guidance_files_that_exist():
        for target in local_markdown_targets(source):
            if not target.exists():
                broken.append(f"{source.relative_to(ROOT)} -> {target}")
    self.assertEqual(broken, [])
```

- [ ] **Step 4: Executar o arquivo de teste e preservar RED esperado**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_project_guidance.py -q`

Expected: somente o teste de presença falha; o validador em si importa e executa sem erro.

- [ ] **Step 5: Commit do teste em RED**

```powershell
git add -- tests/test_project_guidance.py
git commit -m "test: define project guidance contract"
```

---

### Task 2: Documentação consolidada e entrada do projeto

**Files:**
- Create: `docs/README.md`
- Create: `docs/19-visao-geral-e-objetivos.md`
- Create: `docs/20-arquitetura-e-fluxo-de-dados.md`
- Create: `docs/21-catalogo-de-dados-e-metricas.md`
- Create: `docs/22-guia-operacional.md`
- Create: `docs/23-guia-de-desenvolvimento.md`
- Modify: `README.md`
- Test: `tests/test_project_guidance.py`

**Interfaces:**
- Consumes: contratos atuais em `src/tenable_reports`, `clients/examples`, `orchestration/clients.example.json` e `docs/01` a `docs/18`.
- Produces: trilhas de leitura atuais e links estáveis usados por `AGENTS.md` e pelas skills.

- [ ] **Step 1: Criar o índice documental**

`docs/README.md` deve declarar a precedência da fonte de verdade e oferecer quatro trilhas: analista/operação, auditoria de números, desenvolvimento e histórico de decisões. Catalogar cada documento existente como `Atual`, `Contrato vigente`, `Evidência histórica` ou `Plano/Especificação`.

- [ ] **Step 2: Criar a visão geral**

`docs/19-visao-geral-e-objetivos.md` deve explicar:

- objetivo do produto e público;
- três classes de DOCX: base, customizado e por TAG;
- modos automático e manual;
- capacidades atuais VM, WAS, histórico PostgreSQL, TAGs, interface e orquestração;
- limites: Cloud Security, tradução e distribuição externa;
- princípios de privacidade e ausência tipada.

- [ ] **Step 3: Criar arquitetura e fluxo de dados**

`docs/20-arquitetura-e-fluxo-de-dados.md` deve usar a sequência:

```text
Perfil + credenciais referenciadas
  -> período e escopo geral
  -> exports Assets/VM/WAS + metadados de TAG
  -> snapshots compactados e manifestos
  -> normalização/reconciliação
  -> dataset geral + recortes locais por TAG
  -> histórico MAIN compatível
  -> DOCX + manifesto
  -> confirmação no PostgreSQL
  -> reciclagem do staging pesado
```

Documentar as fronteiras `config`, `infrastructure`, `application`, `domain`, `presentation` e `webapp`, e o comportamento de falhas opcionais.

- [ ] **Step 4: Criar o catálogo de dados e métricas**

`docs/21-catalogo-de-dados-e-metricas.md` deve conter tabelas separadas para:

- `NormalizedAsset`: identidade, nomes, IPs, OS, rede, scans, timestamps, ACR/AES;
- `NormalizedFinding`: identidade, ativo, plugin, CVE/referências, descrição/solução, CVSS/VPR, patch, porta, estado, datas, exploitabilidade, frameworks e Output opcional;
- `NormalizedWasFinding`: aplicação/URI, plugin, OWASP, evidência HTTP, estado/datas e scores;
- metadados de TAG: UUIDs de categoria/valor e UUIDs de ativos;
- métricas derivadas, rankings, qualidade, histórico compacto e proveniência das tabelas;
- dados não coletados atualmente: Cloud Security e tradução efetiva.

Para cada métrica importante, declarar fonte, população, grão, data e ausência. Registrar `OPEN/REOPENED -> Last Seen`, `FIXED -> Last Fixed` e ressurgidas com `REOPENED + resurfaced_at`.

- [ ] **Step 5: Criar o guia operacional**

`docs/22-guia-operacional.md` deve priorizar a interface e incluir CLI como manutenção:

- setup, PostgreSQL e `scripts/run_web.ps1`;
- cadastro/edição, teste de conexão e busca de TAGs;
- períodos automático, móvel, últimos dias e intervalo explícito;
- geração individual e carteira;
- estratégia VM, chunks, propriedades seletivas e validação;
- progresso, timeout, retomada, cancelamento seguro e retentativa;
- `MAIN`, exclusão/restauração, backfill e limpeza;
- diagnóstico por classe de erro e comando de encerramento do servidor somente após identificar o PID da aplicação.

Marcar visualmente quais comandos iniciam API real.

- [ ] **Step 6: Criar o guia de desenvolvimento**

`docs/23-guia-de-desenvolvimento.md` deve mapear módulos e testes correspondentes, explicar como adicionar fonte, métrica, módulo editorial, perfil e migration, e exigir TDD, fixtures sanitizadas, reconciliação, ausência tipada e renderização/QA quando DOCX mudar.

- [ ] **Step 7: Enxugar e atualizar o README raiz**

Manter visão, início rápido e comando do painel; substituir o catálogo extenso por links aos seis documentos atuais. Não remover alertas de credenciais, a decisão dos três tipos de documento nem os limites Cloud/tradução/distribuição.

- [ ] **Step 8: Executar os testes de documentação**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_project_guidance.py -q`

Expected: teste de presença passa; qualquer link local quebrado falha com origem e destino.

- [ ] **Step 9: Commit da documentação consolidada**

```powershell
git add -- README.md docs/README.md docs/19-visao-geral-e-objetivos.md docs/20-arquitetura-e-fluxo-de-dados.md docs/21-catalogo-de-dados-e-metricas.md docs/22-guia-operacional.md docs/23-guia-de-desenvolvimento.md
git commit -m "docs: add current project handbook"
```

---

### Task 3: Correções factuais nos documentos existentes

> **Revisão antes da execução:** os Steps 1 e 2 originais desta tarefa, baseados em
> busca de frases, não serão executados. A revisão factual será feita confrontando
> cada afirmação com código, testes e documentação consolidada; o validador da Task 1
> continuará cobrindo links e integridade estrutural. As contradições já observadas
> — WAS/Interface tratados como futuros, retenção pesada permanente e comparativo de
> TAG atribuído ao DOCX geral — constituem a baseline que esta tarefa precisa
> eliminar.

**Files:**
- Modify: `docs/02-catalogo-apis-tenable.md`
- Modify: `docs/05-historico-regras-criticas-e-traducao.md`
- Modify: `docs/12-perfis-e-variacoes-fase7.md`
- Modify: `docs/13-was-fase8.md`
- Modify: `docs/14-historico-e-tendencias-fase9.md`
- Modify: `docs/15-orquestracao-e-distribuicao-fase10.md`
- Modify: `docs/16-postgresql-migracao-e-operacao.md`
- Modify: `docs/17-interface-web-mvp.md`
- Modify: `docs/18-main-retentativas-inteligencia-operacao.md`
- Test: `tests/test_project_guidance.py`

**Interfaces:**
- Consumes: implementação atual e documentação consolidada da Task 2.
- Produces: documentos de fase sem contradições operacionais com o produto atual.

- [ ] **Step 1: Adicionar testes semânticos em RED**

Adicionar casos que falham enquanto persistirem as afirmações antigas:

```python
def test_was_document_does_not_describe_completed_platform_features_as_future(self):
    text = read("docs/13-was-fase8.md")
    self.assertNotIn("o histórico persistente, o agendamento e a interface permanecem", text)

def test_postgresql_document_describes_heavy_staging_as_ephemeral(self):
    text = read("docs/16-postgresql-migracao-e-operacao.md")
    self.assertIn("staging", text.casefold())
    self.assertIn("efêmero", text.casefold())

def test_tag_comparison_is_assigned_to_tag_document(self):
    text = read("docs/14-historico-e-tendencias-fase9.md")
    self.assertIn("relatório da própria TAG", text)
```

- [ ] **Step 2: Executar os testes para confirmar RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_project_guidance.py -q`

Expected: os três testes novos falham pelas afirmações antigas.

- [ ] **Step 3: Corrigir APIs e propriedades seletivas**

Atualizar `docs/02-catalogo-apis-tenable.md` com revisão em 2026-08-23, modo padrão completo, ativação seletiva somente após validação por cliente, fallback único para 400/contrato incompleto, persistência incremental de chunks, retomada, fila/processamento separados e cancelamento automático restrito a job criado pela execução atual sem progresso.

- [ ] **Step 4: Corrigir histórico e PostgreSQL**

Em `docs/05` e `docs/16-postgresql`, substituir SQLite recomendado por PostgreSQL operacional; documentar SQLite apenas para compatibilidade/importação. Explicar `MAIN` imediato, fingerprints compactos, agregados e recortes por UUID de TAG. Remover a exigência de arquivar raws bem-sucedidos; backup preserva banco e DOCX publicados.

- [ ] **Step 5: Corrigir perfis, WAS e TAGs**

Em `docs/12`, substituir “Gate seguinte” por “Evolução posterior” e registrar WAS integrado. Em `docs/13`, afirmar que TAGs não filtram VM/WAS gerais e que o comparativo está no documento da TAG. Remover histórico, agendamento e interface da lista de pendências.

- [ ] **Step 6: Corrigir histórico, orquestração e retenção**

Em `docs/14`, mover comparativo temporal da TAG para o relatório da TAG. Em `docs/15`, incluir os documentos por TAG no fluxo e substituir retenção por limpeza após publicação/histórico confirmados. Em `docs/18`, remover horizontes de 60/90/395 dias para staging bem-sucedido e apontar os sete dias apenas para staging com falha; DOCX só são excluídos explicitamente.

- [ ] **Step 7: Atualizar o catálogo da interface**

Em `docs/17`, acrescentar teste de todos, edição individual, `MAIN`, exclusão/restauração, backfill, armazenamento, filtros de validação, configuração de export VM, validação seletiva, progresso por UUID e ação confirmada de cancelar export/tentar novamente.

- [ ] **Step 8: Executar testes e conferir diff**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_project_guidance.py -q
git diff --check -- docs tests/test_project_guidance.py
```

Expected: PASS e nenhuma advertência de whitespace.

- [ ] **Step 9: Commit das correções factuais**

```powershell
git add -- docs/02-catalogo-apis-tenable.md docs/05-historico-regras-criticas-e-traducao.md docs/12-perfis-e-variacoes-fase7.md docs/13-was-fase8.md docs/14-historico-e-tendencias-fase9.md docs/15-orquestracao-e-distribuicao-fase10.md docs/16-postgresql-migracao-e-operacao.md docs/17-interface-web-mvp.md docs/18-main-retentativas-inteligencia-operacao.md tests/test_project_guidance.py
git commit -m "docs: align phase guides with current system"
```

---

### Task 4: Instruções AGENTS por escopo

**Files:**
- Create: `AGENTS.md`
- Create: `src/tenable_reports/AGENTS.md`
- Create: `tests/AGENTS.md`
- Create: `clients/AGENTS.md`
- Test: `tests/test_project_guidance.py`

**Interfaces:**
- Consumes: `docs/20-arquitetura-e-fluxo-de-dados.md`, `docs/21-catalogo-de-dados-e-metricas.md` e `docs/23-guia-de-desenvolvimento.md`.
- Produces: instruções hierárquicas carregadas conforme o arquivo alterado.

- [ ] **Step 1: Escrever teste de presença em RED**

```python
AGENT_FILES = ("AGENTS.md", "src/tenable_reports/AGENTS.md", "tests/AGENTS.md", "clients/AGENTS.md")

def test_scoped_agent_instructions_exist(self):
    self.assertEqual([name for name in AGENT_FILES if not (ROOT / name).is_file()], [])
```

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_project_guidance.py::ProjectGuidanceTests::test_scoped_agent_instructions_exist -q`

Expected: FAIL listando quatro arquivos.

- [ ] **Step 2: Criar `AGENTS.md` raiz**

Incluir: objetivo, fonte de verdade, comandos de setup/teste, mapa do repositório, invariantes temporais/VM/WAS/TAG/MAIN/retention, segurança, autorização para API real, TDD, validação proporcional e links aos guias atuais.

- [ ] **Step 3: Criar instruções de código**

`src/tenable_reports/AGENTS.md` deve definir dependências permitidas, pureza do domínio, isolamento HTTP, casos de uso na aplicação, apresentação sem API e web sem regras métricas. Mudanças de schema exigem compatibilidade e migration/versionamento.

- [ ] **Step 4: Criar instruções de testes**

`tests/AGENTS.md` deve exigir RED-GREEN-REFACTOR, fixtures sanitizadas, nenhum segredo/API/servidor real, relógio e IDs determinísticos, testes focados antes da suíte completa e QA DOCX quando apresentação mudar.

- [ ] **Step 5: Criar instruções de perfis**

`clients/AGENTS.md` deve exigir IDs estáveis, nenhum segredo, módulos enumerados, `include_info_severity=false` por padrão, `Output` opt-in, TAGs em `report.tag_reports` e validação offline antes de coleta.

- [ ] **Step 6: Validar e commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_project_guidance.py -q
git add -- AGENTS.md src/tenable_reports/AGENTS.md tests/AGENTS.md clients/AGENTS.md tests/test_project_guidance.py
git commit -m "docs: add scoped agent instructions"
```

---

### Task 5: Skill `operating-tenable-reports`

> **Revisão antes da execução:** o teste automatizado desta skill cobre frontmatter,
> nome e referências por meio do validador; ele não procura respostas literais na
> prosa. A baseline falhou por contradições entre os runbooks atuais sobre retenção,
> recursos concluídos e destino do comparativo por TAG. Depois da criação, os três
> cenários do Step 5 serão respondidos usando somente a skill e conferidos contra o
> código/documentação atual. Um forward-test com subagente não será iniciado porque
> esta execução não recebeu autorização para delegação.

**Files:**
- Create: `.agents/skills/operating-tenable-reports/SKILL.md`
- Create: `.agents/skills/operating-tenable-reports/references/runbook.md`
- Modify: `tests/test_project_guidance.py`
- Test: `tests/test_project_guidance.py`

**Interfaces:**
- Consumes: `docs/22-guia-operacional.md` e autorizações explícitas do usuário.
- Produces: skill de referência para operação segura e diagnóstico.

- [ ] **Step 1: Escrever cenário estrutural/aplicativo em RED**

O teste deve exigir frontmatter com nome exato, descrição iniciada por `Use when`, referência válida ao runbook e cinco decisões recuperáveis: período automático, período manual padrão, coleta real requer autorização, TAG não filtra geral e cancelamento automático não alcança job reutilizado.

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_project_guidance.py -k operating_tenable_reports -q`

Expected: FAIL porque a skill não existe.

- [ ] **Step 2: Criar o `SKILL.md` mínimo**

Usar frontmatter:

```yaml
---
name: operating-tenable-reports
description: Use when operating the Tenable monthly reporting project, including client setup, report generation, export monitoring, retries, MAIN selection, or storage cleanup.
---
```

O corpo deve conter visão, roteamento por tarefa, invariantes de segurança, quick reference, erros comuns e link para `references/runbook.md`. Manter menos de 500 palavras.

- [ ] **Step 3: Criar a referência operacional**

O runbook deve encaminhar à interface primeiro e cobrir setup, cliente, períodos, relatórios, TAGs, exports, falhas, `MAIN`, retenção e comandos de diagnóstico sem credenciais.

- [ ] **Step 4: Executar validação GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_project_guidance.py -k operating_tenable_reports -q
python C:\Users\Thales\.codex\skills\.system\skill-creator\scripts\quick_validate.py .agents\skills\operating-tenable-reports
```

Expected: PASS nos dois comandos. Se o caminho global do validador não existir, localizar `quick_validate.py` dentro da skill `skill-creator` carregada e registrar o caminho efetivo no resultado.

- [ ] **Step 5: Executar cenários de recuperação**

Usar a skill para responder offline, conferindo contra o runbook:

1. “Qual período uma execução automática em 1º de agosto usa?”
2. “Posso cancelar automaticamente um UUID retomado?”
3. “Selecionar TAG Rede:Matriz deve filtrar o relatório-base?”

Expected: mês anterior completo; não; não.

- [ ] **Step 6: Commit da primeira skill**

```powershell
git add -- .agents/skills/operating-tenable-reports tests/test_project_guidance.py
git commit -m "docs: add Tenable report operations skill"
```

---

### Task 6: Skill `validating-tenable-report-data`

**Files:**
> **Revisão antes da execução:** o teste automatizado desta skill cobre frontmatter,
> nome e referências por meio do validador; ele não fixa respostas por busca textual.
> A baseline é a dificuldade já observada em distinguir filtros de não mitigadas,
> mitigadas e ressurgidas e em separar `Exploitable` geral dos flags de frameworks.
> Depois da criação, os três cenários do Step 5 serão respondidos usando somente a
> skill e conferidos contra as regras de domínio. Um forward-test com subagente não
> será iniciado porque esta execução não recebeu autorização para delegação.

- Create: `.agents/skills/validating-tenable-report-data/SKILL.md`
- Create: `.agents/skills/validating-tenable-report-data/references/data-contract.md`
- Modify: `tests/test_project_guidance.py`
- Test: `tests/test_project_guidance.py`

**Interfaces:**
- Consumes: `docs/21-catalogo-de-dados-e-metricas.md`, `docs/07-dataset-mensal-fase4.md` e proveniência do dataset.
- Produces: skill de referência para auditoria de tabelas e filtros Tenable.

- [ ] **Step 1: Escrever cenário estrutural/aplicativo em RED**

O teste deve exigir nome/descrição, referência válida e decisões recuperáveis: `OPEN/REOPENED` usa `Last Seen`, `FIXED` usa `Last Fixed`, `Exploitable` usa `plugin.exploit_available`, framework é segregado e Top 5 VM agrupa por plugin.

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_project_guidance.py -k validating_tenable_report_data -q`

Expected: FAIL porque a segunda skill não existe.

- [ ] **Step 2: Criar o `SKILL.md` mínimo**

Usar frontmatter:

```yaml
---
name: validating-tenable-report-data
description: Use when checking Tenable report counts, reproducing platform filters, investigating empty tables, or auditing VM, WAS, TAG, exploitability, and historical comparisons.
---
```

O corpo deve definir a sequência de auditoria: tabela → proveniência → população/grão → estado/data → período → agrupamento/ranking → ausência/qualidade. Incluir quick reference, erros comuns e link ao contrato detalhado. Manter menos de 500 palavras.

- [ ] **Step 3: Criar a referência de dados**

`data-contract.md` deve mapear overview, Top ativos, Top 5 VM, mitigadas, ressurgidas, aging, OS, CVSS/VPR, frameworks, WAS/OWASP, TAGs e histórico para tela, filtros, data, grão e regra local.

- [ ] **Step 4: Executar validação GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_project_guidance.py -k validating_tenable_report_data -q
python C:\Users\Thales\.codex\skills\.system\skill-creator\scripts\quick_validate.py .agents\skills\validating-tenable-report-data
```

Expected: PASS nos dois comandos, usando o caminho efetivamente localizado para o validador se necessário.

- [ ] **Step 5: Executar cenários de recuperação**

1. “Qual filtro valida mitigadas de julho?” → `State=Fixed`, `Last Fixed` em julho.
2. “Framework Metasploit pode aumentar o total Exploitable geral?” → não; o geral usa o indicador direto.
3. “Sem predecessor, a tabela comparativa deve usar zero?” → não; indisponibilidade/lacuna.

- [ ] **Step 6: Commit da segunda skill**

```powershell
git add -- .agents/skills/validating-tenable-report-data tests/test_project_guidance.py
git commit -m "docs: add Tenable report data validation skill"
```

---

### Task 7: Verificação final e entrega

**Files:**
- Verify: todos os arquivos criados/modificados nas Tasks 1 a 6.

**Interfaces:**
- Consumes: documentação, instruções, skills e testes completos.
- Produces: evidência de que a entrega é consistente e offline.

- [ ] **Step 1: Procurar placeholders e segredos acidentais**

```powershell
rg -n -i "TODO|TBD|fill in|example secret|accessKey=.+|secretKey=.+" README.md docs AGENTS.md src/tenable_reports/AGENTS.md tests/AGENTS.md clients/AGENTS.md .agents/skills
.\.venv\Scripts\python.exe tools\audit_secret_leaks.py
```

Expected: nenhum placeholder nos arquivos novos; auditoria não encontra segredo versionado.

- [ ] **Step 2: Validar links e skills**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_project_guidance.py -q
python C:\Users\Thales\.codex\skills\.system\skill-creator\scripts\quick_validate.py .agents\skills\operating-tenable-reports
python C:\Users\Thales\.codex\skills\.system\skill-creator\scripts\quick_validate.py .agents\skills\validating-tenable-report-data
```

Expected: PASS.

- [ ] **Step 3: Executar a suíte completa**

Run: `.\.venv\Scripts\python.exe -m pytest -q`

Expected: todos os testes e subtestes passam, sem API real.

- [ ] **Step 4: Revisar Git e whitespace**

```powershell
git diff --check
git status --short --branch
git log -8 --oneline
```

Expected: somente alterações deliberadas; as correções anteriores de `cli.py` e `test_cli.py` continuam preservadas se ainda não tiverem sido commitadas separadamente.

- [ ] **Step 5: Entregar o índice de navegação**

Na resposta final, apontar `README.md`, `docs/README.md`, os quatro `AGENTS.md`, as duas skills e os testes executados. Informar explicitamente que nenhuma coleta real, servidor, credencial, merge ou push foi executado nesta entrega.
