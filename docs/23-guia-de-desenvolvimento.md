# Guia de desenvolvimento

## Preparação

```powershell
.\scripts\setup.ps1
$env:PYTHONPATH = (Join-Path $PWD 'src')
.\.venv\Scripts\python.exe -m pytest -q
```

Trabalhe em uma única branch ativa `codex/*` por ciclo, além de `main`. Não crie
branch ou worktree paralelo para a mesma entrega. Antes de editar, verifique o
estado do Git e preserve alterações do usuário.

## Organização e dependências

- `domain` contém regras puras e modelos; não acessa HTTP, PostgreSQL ou DOCX.
- `application` coordena casos de uso e depende de contratos explícitos.
- `infrastructure` implementa APIs, persistência e serialização.
- `presentation` transforma datasets aprovados em documentos.
- `webapp` expõe operação local sem duplicar regra de negócio.

Evite cálculos independentes em cada renderizador. Uma métrica deve ser definida no
dataset e reutilizada por Word, interface, histórico e testes.

## Fluxo de alteração

1. Escreva um teste que demonstre o comportamento ausente ou incorreto.
2. Execute-o e confirme que falha pelo motivo esperado.
3. Implemente a menor mudança suficiente.
4. Execute testes focados.
5. Execute a suíte completa e validações estruturais.
6. Para DOCX, renderize uma prova e faça inspeção visual.
7. Revise `git diff --check` e o estado do Git antes de concluir.

Não use uma chamada real à Tenable como teste unitário. Clientes HTTP devem ser
testados com respostas e chunks sanitizados em `tests/fixtures`.

## Comandos de verificação

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe tools\validate_project_guidance.py --root .
.\.venv\Scripts\python.exe tools\audit_secret_leaks.py
node --check src\tenable_reports\webapp\static\app.js
git diff --check
```

O validador de orientação verifica a presença dos guias, `AGENTS.md`, skills,
frontmatter e links locais. Ele valida estrutura, não redação exata.

## Regras de domínio que exigem regressão

- intervalo `[início, fim)` e fuso do cliente;
- `OPEN`/`REOPENED` por `last_found` e `FIXED` por `last_fixed`;
- ressurgidas por `resurfaced_at`;
- exclusão de severidade informativa;
- identidade e vínculo por UUID;
- indicador geral de exploração separado dos frameworks;
- coleta geral independente de TAG;
- comparativo da mesma TAG no tempo;
- WAS opcional sem bloquear VM;
- decisão manual WAS separada da retentativa única com fallback do lote e do
  automático mensal;
- retentativa WAS publicada sem repetir VM/assets/TAG/Cloud, com hash VM invariável
  e rollback de documentos/manifesto;
- `NOT_COLLECTED` WEB distinto de `NO_DATA` no dataset e no texto do DOCX;
- Cloud opcional, com falha, progresso e retentativa independentes;
- `VM_CORE`, `WAS` e `CLOUD` com tentativa, etapa e retryable independentes;
- retry seletivo sem repetir componente concluído e com rollback byte a byte do
  manifesto em falha;
- VPR Cloud zero distinto de ausência e fotografia atual distinta de histórico exato;
- `STAGED_V1` com coleta remota concorrente, montagem local única e compatibilidade
  `LEGACY`;
- checkpoint íntegro antes de `READY_FOR_BUILD` e build sem transporte HTTP;
- `MAIN` explícito e histórico compatível;
- descarte seguro apenas depois da publicação validada.

## Tenable VM, WAS e Cloud Security

Respeite o contrato assíncrono dos exports. Não interprete `total_chunks` como estado
final. Persista chunks conforme ficam disponíveis e preserve o manifesto parcial
para retentativa.

Qualquer nova lista de propriedades seletivas precisa ser validada contra payload
completo no tenant antes de virar padrão. O fallback seletivo é restrito às falhas
de contrato já previstas; não mascare autenticação, limite de taxa ou timeout.

Não inicie export real, servidor ou cancelamento sem necessidade e autorização.
Comandos reais devem exigir confirmação explícita.

No Cloud, consultas GraphQL obrigatórias e opcionais são separadas por contrato. O
probe mínimo deve ocorrer antes da coleta completa; token nunca entra em perfil,
argumento, log, manifesto ou resposta HTTP. Enriquecimento de descrição e correção
fica restrito aos candidatos dos rankings para evitar payload desnecessário.

Campos adicionais de versão corrigida devem permanecer em consultas opcionais
isoladas. Uma rejeição de schema a `FixedBy` marca somente essa fonte como
indisponível. Preserve a ocorrência consolidada por ativo/CVE usada nos totais e a
coleção paralela por ativo/CVE/software usada nas tabelas; não conte a mesma CVE
duas vezes nos indicadores gerais apenas porque ela afeta pacotes diferentes.

## PostgreSQL

Mudanças de esquema entram como nova migration numerada em
`src/tenable_reports/infrastructure/postgresql_migrations`. Nunca edite uma migration
que já possa ter sido aplicada. O código precisa tolerar atualização incremental e
o teste deve cobrir ordem, idempotência esperada e leitura/escrita afetada.

Segredos do banco ficam em `credentials/database.env`; exemplos só contêm nomes de
variáveis e valores fictícios.

Snapshots Cloud usam migration própria, compatibilidade por cliente, tenant, ambiente,
período e versão de métricas. A exclusão permanente remove o snapshot Cloud pelo
`run_id` dentro da mesma transação que apaga `report_runs`.

## Lotes duráveis

Mudanças na fila precisam preservar estes contratos:

- PostgreSQL é a única fonte dos lotes em produção; não existe fallback silencioso
  para memória;
- novos lotes usam `STAGED_V1`; linhas antigas permanecem `LEGACY` e são
  reivindicadas pelo worker compatível;
- o pool remoto reivindica somente `REMOTE_QUEUED`; o pool local, com exatamente
  um worker, reivindica somente `READY_FOR_BUILD`;
- capacidade remota automática é
  `max(1, min(elegíveis, max_clients_per_batch, 64))`; valor configurado positivo
  pode reduzi-la, nunca ampliar o limite;
- `COLLECTION_READY` valida e persiste o checkpoint na mesma transação que move
  `REMOTE_RUNNING` para `READY_FOR_BUILD`;
- reconciliação ocorre uma vez para todos os worker IDs: coleta abandonada volta a
  `REMOTE_QUEUED` e build abandonado volta a `READY_FOR_BUILD`;
- pausa bloqueia novos claims sem apagar checkpoints; retomada não altera
  `FAILED`, `INTERRUPTED` ou `CANCELLED_BY_USER`;
- parada sinaliza todos os jobs ativos, preserva export/chunks e limita o fallback
  a cada árvore de processo local;
- 900 segundos sem progresso emitem alerta; 36.000 segundos formam o orçamento
  total por UUID entre fila e processamento, preservado entre reinícios e
  retentativas, sem cancelamento automático remoto;
- **Tentar falhas/interrompidos** e **Gerar todos novamente** criam lotes derivados
  idempotentes e não reescrevem a origem;
- ações registram ator, motivo, chave idempotente, PID e eventos relevantes.

O job persiste `vm_export_uuid`, `vm_resume_manifest_path`,
`remote_export_started_at`, `remote_status_at` e `remote_progress_at`. Atualize
`remote_status_at` somente após resposta 200 do status e `remote_progress_at`
somente quando estado, contador ou chunk avançar. Erros 429/5xx/transporte podem
ser absorvidos pelo polling dentro do orçamento; 401 e demais falhas permanentes
devem sair imediatamente. Coalesce eventos idênticos e grave no máximo um
heartbeat a cada cinco minutos.

A derivação individual de retry deve selecionar exatamente um job e preservar o
UUID/manifesto. Se o UUID anterior não for reutilizável, registre
`TENABLE_EXPORT_RECOVERY_UNAVAILABLE` antes de iniciar seu substituto. Não use uma
falha de consulta transitória como evidência de expiração.

O snapshot HTTP pode expor fase, timestamps e `checkpoint_ready`; nunca serialize
`collection_checkpoint_path`. Teste estado de domínio, claims por fase,
repositório em memória, SQL PostgreSQL, 20 clientes concorrentes, build máximo 1,
subprocesso local, rotas HTTP e JavaScript. Para recuperação, cubra `--dry-run`,
schema/hash inválido, transação e rollback. Não use coleta real.

## Componentes e seleção do lote

`report_component_attempts` registra somente metadados sanitizados de
`VM_CORE`, `WAS` e `CLOUD`. A tentativa mais recente define disponibilidade e
retry. `FAILED`/`INTERRUPTED` exigem `failure_code` seguro; componentes
concluídos não podem ser selecionados em `failed_only`.

`component_retry.py` trata handlers e publisher como fronteiras. Crie staging
filho único, valide todos os caminhos dentro dele e só altere o manifesto no
publisher. Qualquer falha restaura os bytes anteriores, remove apenas o staging
novo e nunca persiste `str(exc)`. WAS sem VM reutilizável deve falhar com
`MISSING_VM_CHECKPOINT_FOR_WAS`; Cloud não depende de VM.

O servidor valida confirmação, conjunto excluído, enum e subconjunto retentável
antes de chamar o executor. Sem `component_retry_enqueuer`, somente o caminho
compatível Cloud pode executar; VM/WAS retornam indisponibilidade explícita.

Em **Gerar todos**, o navegador envia IDs explícitos, mas a regra pura do servidor
revalida vazio, duplicatas, desconhecidos e inativos. Persista
`selected_client_ids`, `excluded_client_ids`, filtro e fotografia do analista
sem deixar referências mutáveis ao cadastro. Analista é metadado, não autorização.

## Documentos Word

Preserve o conteúdo editorial dos modelos aprovados. Não introduza parágrafos,
tabelas ou títulos novos sem decisão explícita de produto. Datas e identificadores
dinâmicos devem ser substituídos sem destruir formatação de runs, cabeçalhos,
rodapés, imagens e quebras de seção.

Rótulos integrais de severidade/faixa em tabelas destacadas usam a paleta aprovada:
`CRITICAL`, `HIGH`, `MEDIUM` e `LOW`. A classificação deve ser estrita;
texto livre que apenas contém “crítico” não recebe cor. Cubra idade, faixas CVSS,
eixos CVSS×VPR e rating VPR, além de builders compartilhados.

Tradução de descrição usa `translate_semantic_text`: parágrafo, sentença e limite
de palavra, com CVE/URL/versão inteiros quando couberem. Use tradutor injetado e
cache, nunca rede em teste. Falha de um chunk preserva somente a fonte daquele
chunk e não bloqueia os demais nem o DOCX. `translator=None` preserva texto,
inclusive conteúdo já em português.

Depois de alterar apresentação:

1. gere um DOCX com fixture determinística;
2. confirme estrutura por teste;
3. renderize com LibreOffice;
4. inspecione páginas críticas, tabelas, cortes e campos vazios; no Cloud, confirme
   também os blocos por imagem e as colunas `Software` e `Fixed by` da seção 3.5;
5. mantenha a prova fora do Git quando contiver dados reais.

Para o relatório Cloud padrão sanitizado:

```powershell
.\.venv\Scripts\python.exe scripts\render_cloud_report_fixture.py `
  --output-root artifacts\cloud-prototype --qa
```

O manifesto deve registrar um único documento, o hash do dataset e as seções
renderizadas ou omitidas. O QA registra PDF, páginas e contact sheet antes da
inspeção.

## Interface web

Rotas novas precisam de teste do servidor e do JavaScript que as consome. Mostre
erros de forma acionável, associe-os ao cliente e não retorne secrets ao navegador.
Fase e timestamps podem ser retornados; checkpoint é representado somente por
`checkpoint_ready`, nunca por caminho local.
O formulário Cloud devolve apenas `cloud_token_saved`; um token vazio em edição
preserva o valor local existente. VM e Cloud possuem resultados de teste separados.
Operações destrutivas, como exclusão ou cancelamento de export, exigem alvo
explícito e confirmação proporcional ao risco. Para conjuntos de relatórios, teste
prévia, frase digitada, substituição obrigatória de `MAIN`, bloqueio por job ativo,
validação da raiz `data`, rollback do estágio físico e remoção transacional dos
registros PostgreSQL.

O navegador mantém no máximo uma chamada `/api/state` ativa. Uma mutação libera o
controle depois da confirmação do POST e agenda, sem aguardar, uma única atualização
posterior. O estado usa consultas em massa de jobs/eventos e cache curto somente
para a varredura transitória de disco. O resumo não inclui o histórico detalhado:
`GET /api/batches/<id>` carrega os clientes do lote sob demanda.

Downloads agregados devem ser montados sob `data/.downloads`, aceitar somente
documentos registrados dentro da raiz `data`, usar nomes de componentes
sanitizados e remover o ZIP temporário em bloco `finally` após a transmissão. O
download mensal consulta a referência `MAIN` por cliente e período; o download de
um conjunto preserva a identidade do `run_id` selecionado. Cubra seleção,
estrutura interna, omissões, caminhos inseguros e limpeza temporária com testes.
Erros de montagem precisam voltar à interface antes do início do streaming. Use
token curto, de uso único e com expiração; não materialize o ZIP inteiro na memória
do navegador.

## Documentação e instruções

Atualize os guias vigentes quando o comportamento mudar. Registros de fase podem
receber uma nota de estado atual, mas decisões históricas não devem ser apagadas.

As regras gerais para agentes ficam em [AGENTS.md](../AGENTS.md); pastas com riscos
específicos possuem um arquivo próprio. Skills do projeto vivem em `.agents/skills`
e devem ser referências curtas, com `name` igual à pasta, descrição iniciada por
`Use when` e links válidos para seus materiais de apoio.
