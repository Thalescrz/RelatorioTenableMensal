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
- fallback de escopo de TAG grande pelo Asset Export v1 somente quando o Workbench
  declarar população acima de 5.000 ativos; outros erros não podem disparar coleta
  adicional silenciosa;
- WAS opcional sem bloquear VM;
- recuperação independente de VM/WAS/Cloud em duas janelas automáticas e terceira
  condicional, sem Janela 4 e sem reiniciar prazo na substituição;
- retentativa WAS publicada sem repetir VM/assets/TAG/Cloud, com hash VM invariável
  e rollback de documentos/manifesto, tanto para origem manual quanto automática;
- `NOT_COLLECTED` WEB distinto de `NO_DATA` no dataset e no texto do DOCX;
- Cloud opcional, com falha, progresso e retentativa independentes;
- `VM_CORE`, `WAS` e `CLOUD` com tentativa, etapa e retryable independentes;
- retry seletivo sem repetir componente concluído e com rollback byte a byte do
  manifesto em falha;
- VPR Cloud zero distinto de ausência e fotografia atual distinta de histórico exato;
- `STAGED_V1` com coleta remota concorrente, montagem local única e compatibilidade
  `LEGACY`;
- família de lotes contada uma vez por cliente e mensal idempotente por competência;
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

O export WAS não é obrigado a trazer `plugin.family`. Resolva famílias somente por
Plugin ID no endpoint paginado `/was/v2/plugins`, persista o resultado isolado por
cliente e tenant e trate essa consulta como enriquecimento em melhor esforço. Não
use OWASP como substituto. Cubra por teste os dois esquemas da tabela: com a coluna
**Família** quando houver ao menos uma correspondência real e sem ela quando não
houver nenhuma.

Não inicie export real, servidor ou cancelamento sem necessidade e autorização.

## Mensal e Agendador do Windows

O serviço mensal deve permanecer testável com aplicação/repositório injetados. A
CLI headless e a interface chamam o mesmo `STAGED_V1`; não crie um segundo
orquestrador. Teste competência no fuso, chave idempotente, família existente e
clientes ativos com credenciais prontas.

O adaptador do Agendador usa lista de argumentos, `shell=False`, timeout curto e
compara launcher/configuração antes de declarar sincronização. Salvar ou validar a
política nunca invoca `schtasks.exe` nem cria lote. Aplicar, ativar e desativar
exigem confirmação e devem ser testados com runner falso.
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

Os valores aceitos pelos constraints devem acompanhar integralmente os enums do
domínio. Em particular, `web_batches.requested_action` aceita pausa, retomada,
parada, retentativa de incompletos e nova execução de todos; a inclusão de uma ação
nova exige migration própria e teste que percorra todo o enum `BatchAction`.

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
- a tentativa mais recente dos componentes é consolidada por job/cliente, sem
  barreira global do lote; `reference_at` é auditoria volátil e não participa da
  identidade compartilhada da janela `[start_at, end_at)`;
- `REMOTE_COMPONENTS_CONSOLIDATING` usa chave idempotente por job, e uma segunda
  chamada depois de `READY_FOR_BUILD`, `BUILD_RUNNING` ou `TERMINAL` não regrava
  checkpoint nem evento;
- reconciliação ocorre uma vez para todos os worker IDs: coleta ou build abandonado
  vira `INTERRUPTED/TERMINAL`, o lote fica pausado e nenhum worker o reivindica no
  startup;
- a consulta de claim de componentes exige o job pai em `RUNNING/REMOTE_RUNNING`,
  impedindo componentes pendentes de lote pausado ou terminal de chamar a API;
- ao derivar explicitamente uma retentativa cujos componentes já são publicáveis,
  restaure os checkpoints no novo job e execute apenas a consolidação local; uma
  exceção deve virar `CHECKPOINT_COMPONENT_INCOMPLETE`, nunca ser engolida deixando
  o job em `REMOTE_RUNNING`;
- no mesmo `run_id`, reutilize coleta completa de assets/VM/TAG somente após validar
  identidade, consulta, configuração de TAG, chunks e hashes; divergência deve
  preservar a imutabilidade do artefato;
- na descoberta de TAG, preserve o caminho Workbench para escopos enumeráveis. O
  fallback Asset Export v1 deve filtrar por `tag.<categoria>`, deduplicar UUIDs,
  persistir `scope_source`, UUID/origem/chunks, publicar o snapshot por substituição
  atômica e propagar cancelamento; nunca repita o export de findings VM;
- antes de reconstruir, consulte a publicação do `run_id`; documento válido em
  `READY_FOR_CONTROLLED_DISTRIBUTION` conclui o build idempotentemente, sem nova
  persistência de dataset, histórico ou arquivos;
- registro `READY` sem linhas de documento não é suficiente: valide o manifesto,
  os DOCX, o dataset, tamanhos, SHA-256 e confinamento em `data`; depois repare as
  linhas de publicação idempotentemente. Classifique `MemoryError` como recurso
  local esgotado e retentável;
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

Uma falha Cloud posterior à escrita do dataset precisa manter um artefato
`cloud_dataset` com SHA-256, capabilities e versão do conector no checkpoint de
falha. O retry remoto usa `CloudResumeContext(SNAPSHOT_PUBLICATION)`, proíbe o
coletor ao vivo e, com `render_documents=False`, não renderiza DOCX. Se o dataset
vier de uma tentativa anterior, copie-o para o workspace exclusivo da tentativa
atual antes de persistir o novo checkpoint. Cubra também o formato legado sem
artefato explícito, aceitando apenas o caminho determinístico validado.

Metadados lidos do checkpoint podem conter `MappingProxyType` aninhado. Converta a
árvore para estruturas mutáveis simples antes da montagem; não use `deepcopy` em
proxies imutáveis. Ao persistir a recuperação, compare com a tentativa mais recente:
não duplique estados idênticos e registre uma nova `attempt_number` quando o estado
mudar. A publicação compacta de um retry usa uma identidade de revisão própria para
preservar a imutabilidade do snapshot parcial já existente.

Uma recuperação WAS publicada deve usar o snapshot compacto sempre que o registro
de recuperação estiver em `RETRY_AVAILABLE`, independentemente de a origem ser
manual ou mensal automática. Cubra por teste a ausência do staging pesado, a
proibição de chamadas VM/Cloud e a invariância do hash VM. Backups de substituição
atômica precisam permanecer no mesmo diretório do destino, mas com nome curto
baseado em identificador de transação e hash do caminho, para evitar `MAX_PATH` no
Windows.

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

O template `templates/corporate/base-v1.docx` preserva a capa e a contracapa do
documento oficial, mas substitui cliente, período, cabeçalho interno e metadados por
valores controlados. Não reconstrua essas páginas com elementos aproximados. O
conteúdo geral, customizado, por TAG ou Cloud deve ser inserido entre as duas páginas
oficiais. A página 2 contém um campo nativo `TOC \\o "1-3" \\h \\z` com atualização
automática habilitada. `SUMÁRIO` usa `TOC Heading`, fica fora da própria lista, e
seções/subseções usam `Heading 1` a `Heading 3` com números explícitos. `Heading 4`
pode estruturar detalhes internos, mas não participa do sumário.

`cloud-base-v1.docx` é somente referência histórica. O gerador Cloud atual usa o
mesmo shell `base-v1.docx` e publica um único modelo completo.

Para atualizar documentos antigos, mantenha separadas composição e publicação. O
planejador deve cruzar os manifestos `READY_FOR_CONTROLLED_DISTRIBUTION` com as
referências `MAIN` do PostgreSQL, rejeitar caminhos externos, duplicados ou
inexistentes e nunca percorrer DOCX soltos ou conjuntos não-MAIN. Antes da troca,
valide pacote, seções, conteúdo técnico, tabelas, gráficos e imagens. A
substituição usa `refresh_publication_documents_atomically`, atualiza hashes no
manifesto e no PostgreSQL no mesmo commit lógico, registra
`OFFICIAL_REPORT_SHELL_V3` e preserva flags de negócio como `MAIN`.

Rótulos integrais de severidade/faixa em tabelas destacadas usam a paleta aprovada:
`CRITICAL`, `HIGH`, `MEDIUM` e `LOW`. A classificação deve ser estrita;
texto livre que apenas contém “crítico” não recebe cor. Cubra idade, faixas CVSS,
eixos CVSS×VPR e rating VPR, além de builders compartilhados.

O relatório Cloud padrão usa Calibri e a mesma paleta estrutural do relatório geral.
Testes estruturais devem inspecionar estilos e formatação direta, inclusive runs de
cabeçalho e rodapé herdados do template, para impedir regressão para Arial ou Times.

Tradução de descrição usa `translate_semantic_text`: parágrafo, sentença e limite
de palavra, com CVE/URL/versão inteiros quando couberem. A CLI deve criar um único
`GoogleTextTranslator` lazy por execução e reutilizá-lo no geral, nas TAGs e no
Cloud, garantindo cache comum e destino `pt-BR` (`pt` no provedor). Use tradutor
falso injetado e cache nos testes, nunca rede. Falha de um chunk preserva somente a
fonte daquele chunk, inclui aviso editorial e não bloqueia os demais nem o DOCX.
`translator=None` continua sendo o modo explícito de preservação do texto. Não
encaminhe Plugin Output nem identificadores/evidências de ativos ao tradutor.
O adaptador HTTP usa a rota `clients5.google.com/translate_a/t`, timeout explícito
e até três tentativas para `429` e `5xx`. Nunca propague a URL completa, o texto ou
a exceção bruta do transporte para logs e respostas operacionais.
A resposta real pode chegar como `[[texto_traduzido, idioma_origem]]`; extraia
recursivamente apenas o primeiro campo textual e rejeite payload vazio ou
desconhecido. O reparo de documentos legados deve usar `ast.literal_eval`, nunca
`eval`, atuar exclusivamente após os rótulos editoriais autorizados e ser
idempotente. Depois do reparo, republique de forma atômica e atualize manifesto e
catálogo PostgreSQL antes de liberar o ZIP.

Depois de alterar apresentação:

1. gere um DOCX com fixture determinística;
2. confirme estrutura por teste;
3. renderize com LibreOffice;
4. inspecione páginas críticas, tabelas, cortes e campos vazios; no Cloud, confirme
   também os blocos por imagem e as colunas `Software` e `Fixed by` da seção 3.5;
5. mantenha a prova fora do Git quando contiver dados reais.

O controle de documento é renderizado exclusivamente por
`presentation/document_control.py`; os renderizadores Geral e Cloud não devem
recriar essas tabelas localmente. A fonte global é o arquivo local ignorado
`orchestration/document-control.json`, enquanto o perfil do cliente guarda apenas
`document_control.additional_distribution_recipients`. Ao alterar esse contrato,
teste parser, API, composição global + adicional, deduplicação por e-mail, ordem
das linhas e igualdade estrutural entre os dois DOCX. Use `load_client_profile`
quando precisar apenas analisar um JSON de forma determinística e
`load_operational_client_profile` nos fluxos de produção que devem incorporar a
lista global local. O modo `mask_sensitive` deve esvaziar os três campos da lista
de distribuição em todos os renderizadores e retentativas.

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
um conjunto preserva a identidade do `run_id` selecionado. Quando houver filtro por
analista responsável, resolva no servidor os clientes atualmente vinculados,
rejeite identificador inexistente e mantenha a mesma regra `MAIN`. Cubra seleção,
estrutura interna, omissões, caminhos inseguros e limpeza temporária com testes.
Erros de montagem precisam voltar à interface antes do início do streaming. Use
token curto, de uso único e com expiração; não materialize o ZIP inteiro na memória
do navegador.

A preparação do ZIP é assíncrona: `POST /api/report-archives/prepare` responde com
HTTP 202 e um `status_url`; o frontend consulta
`GET /api/report-archives/preparations/<id>` até `READY` ou `FAILED`. O callback do
montador publica progresso monotônico nas fases `SELECTING_REPORTS`,
`SCANNING_DOCUMENTS`, `BUILDING_ARCHIVE` e `FINALIZING`. Somente o estado `READY`
expõe o token de download. Estados de preparação são efêmeros, protegidos por lock
e removidos depois do TTL; falhas inesperadas nunca devolvem detalhes internos ao
navegador. A barra representa a seleção e a criação do pacote no servidor, não os
bytes já entregues ao gerenciador de downloads do navegador.

## Documentação e instruções

Atualize os guias vigentes quando o comportamento mudar. Registros de fase podem
receber uma nota de estado atual, mas decisões históricas não devem ser apagadas.

As regras gerais para agentes ficam em [AGENTS.md](../AGENTS.md); pastas com riscos
específicos possuem um arquivo próprio. Skills do projeto vivem em `.agents/skills`
e devem ser referências curtas, com `name` igual à pasta, descrição iniciada por
`Use when` e links válidos para seus materiais de apoio.
