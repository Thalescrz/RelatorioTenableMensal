# Coleta concorrente e renderização serial

**Status:** desenho aprovado em conversa e consolidado para implementação em
01/09/2026.

## 1. Contexto

O coletor já persiste chunks VM e WAS assim que ficam disponíveis, mantém
manifestos parciais retomáveis e aceita até 7.200 segundos de processamento
remoto. A fila durável da interface, porém, executa um cliente por vez e cada
trabalho ainda reúne coleta, normalização, renderização DOCX e publicação.

Essa combinação deixa a CPU ociosa enquanto um tenant aguarda a Tenable e impede
que os demais tenants iniciem seus próprios exports. A solução deve explorar a
independência entre tenants sem renderizar vários documentos ao mesmo tempo e sem
perder os contratos de UUID, período, WAS opcional, TAG, Cloud, histórico compacto
e publicação controlada.

## 2. Objetivos

1. Iniciar a coleta remota de todos os clientes selecionados, por padrão, sem um
   limite artificial de quatro clientes.
2. Baixar e persistir cada chunk assim que a Tenable o disponibilizar.
3. Separar a coleta remota da normalização, geração DOCX e publicação local.
4. Colocar cada cliente pronto em uma fila local serial, com apenas uma montagem
   de relatório ativa por vez.
5. Preservar UUIDs, chunks e checkpoints para que reinício, timeout ou retentativa
   não repitam trabalho remoto já concluído.
6. Exibir claramente, por lote e por cliente, a fase atual e o progresso remoto.
7. Aplicar o mesmo comportamento ao botão **Gerar todos** e à execução mensal
   automática do primeiro dia do mês.
8. Permitir que o operador exclua clientes da solicitação manual de **Gerar
   todos** antes de criar o lote.
9. Manter um catálogo simples de analistas e associar um responsável principal
   opcional a cada cliente.
10. Filtrar os cards e a seleção de clientes pelo analista responsável, incluindo
    clientes sem responsável.
11. Registrar resultado independente para VM, WAS e Cloud e permitir retentar
    somente componentes que falharam, preservando os componentes válidos.

## 3. Fora de escopo

- Alterar os cálculos, filtros temporais, textos, tabelas ou modelos DOCX.
- Alterar os tamanhos de chunk definidos nos perfis nesta entrega.
- Executar dois exports concorrentes do mesmo tipo para o mesmo cliente.
- Renderizar vários clientes em paralelo.
- Tratar `total_chunks > 0` como conclusão antes de `FINISHED`.
- Introduzir uma nova tecnologia de mensageria; PostgreSQL continua sendo a fonte
  durável da fila.
- Criar login, autenticação, autorização, permissões ou distribuição automática de
  clientes entre analistas.
- Inserir o nome do analista nos relatórios DOCX; o responsável é metadado
  operacional da interface nesta entrega.
- Refazer componentes concluídos por meio da ação **Tentar componentes com falha**;
  uma nova execução completa continua disponível como ação separada.

Este desenho substitui somente a decisão de execução estritamente sequencial das
seções 2 e 13 de `2026-08-31-controle-duravel-de-lotes-design.md` para novos lotes
`STAGED_V1`. Estados duráveis, idempotência, pausa, parada, retentativa e
preservação de exports definidos naquele documento continuam válidos.

## 4. Decisão arquitetural

Cada trabalho de cliente passa por duas fases duráveis:

1. **REMOTE_COLLECTION**: acessa Tenable VM, WAS e, quando habilitado, Cloud;
   persiste respostas brutas, chunks, catálogos e um checkpoint sanitizado. Não
   cria DOCX nem publica histórico `MAIN`.
2. **LOCAL_BUILD**: consome exclusivamente o checkpoint e os artefatos locais;
   normaliza, monta datasets, gera os documentos, persiste histórico compacto e
   publica o conjunto. Essa fase não abre novos exports VM/WAS.

Um pool remoto pode executar vários clientes. Um único worker local processa a
fase de montagem. A passagem entre fases é uma transação PostgreSQL: somente um
checkpoint completo e validado transforma o trabalho em `READY_FOR_BUILD`.

### 4.1. Componentes independentes

Cada trabalho mantém três componentes operacionais:

- `VM_CORE`: assets, findings VM, TAGs e documentos geral/customizado/por TAG;
- `WAS`: coleta WEB opcional e reparo dos documentos que usam suas seções;
- `CLOUD`: coleta, dataset, snapshot e documento Cloud Security.

Cada componente tem estado `PENDING`, `RUNNING`, `COMPLETE`,
`COMPLETE_WITH_WARNINGS`, `FAILED`, `INTERRUPTED` ou `SKIPPED`, além de etapa,
tentativa, retryable, código de falha e referências aos artefatos. O conjunto pode
ser publicado parcialmente quando ao menos um componente produziu documentos
válidos. O estado do conjunto deixa explícito quais componentes faltam.

Cloud é independente de VM. WAS depende de um checkpoint VM íntegro para reparar
documentos, mas uma falha WAS não invalida VM. A matriz de dependências impede uma
retentativa de solicitar apenas WAS quando não existe base VM reutilizável.

O comando legado `run-client` continua disponível para diagnóstico e
compatibilidade. A interface e a automação mensal usam os novos comandos
faseados; assim a mudança não quebra scripts operacionais existentes.

## 5. Concorrência remota

`remote_collection_workers = 0` significa modo automático: o lote disponibiliza
um worker por cliente selecionado e elegível. O limite defensivo é 64 clientes por
lote. Um valor explícito entre 1 e 64 reduz a concorrência quando o operador
precisar adequar rede, memória ou disco.

As regras são:

- no máximo um trabalho ativo por `client_id`, inclusive entre lotes;
- cada cliente usa seu próprio subprocesso para manter isolamento de `.env` e
  credenciais;
- `SELECT ... FOR UPDATE SKIP LOCKED` continua impedindo dupla reivindicação;
- o pool remoto não reivindica trabalhos de montagem;
- o worker local não reivindica trabalhos de coleta;
- falta de espaço mantém o trabalho aguardando, registra alerta sanitizado e não
  abre um export que não possa ser persistido;
- `429`, `5xx` e falhas transitórias seguem as políticas de backoff e retentativa
  existentes, sem converter erro em novo export silencioso.

O número real de workers remotos e o teto configurado aparecem no estado da
interface. A concorrência nunca é derivada de TAG: TAG continua sendo um recorte
local da coleta geral do respectivo cliente.

## 6. Estados duráveis

`WebBatchJob` ganha uma fase independente do status terminal:

- `REMOTE_QUEUED`
- `REMOTE_RUNNING`
- `REMOTE_WAITING_DECISION`
- `READY_FOR_BUILD`
- `BUILD_RUNNING`
- `TERMINAL`

Os status atuais (`QUEUED`, `RUNNING`, `FAILED`, `COMPLETE`,
`COMPLETE_WITH_WARNINGS`, `INTERRUPTED` e `CANCELLED_BY_USER`) permanecem para
compatibilidade e resumo do resultado. A fase responde *onde* o trabalho está; o
status responde *como* ele está.

A migração PostgreSQL adiciona, no mínimo:

- `phase`;
- `collection_checkpoint_path`;
- `remote_started_at` e `remote_ended_at`;
- `build_started_at`.

O `worker_id` atual identifica o reclamante da fase corrente. Os eventos
`JOB_STARTED`, `COLLECTION_READY` e `BUILD_STARTED` preservam o histórico dos
workers sem duplicar colunas.

Uma tabela operacional `report_component_attempts` registra `client_id`,
`source_run_id`, `component`, `status`, `stage`, `attempt_number`, `retryable`,
`failure_code`, mensagem sanitizada, checkpoint, referências de documentos e
datas. A unicidade é por execução, componente e tentativa; nenhum secret ou dado
de finding é persistido nessa tabela.

Nenhuma dessas colunas contém credencial. Caminhos precisam permanecer dentro da
raiz de dados configurada. Trabalhos antigos recebem fase `LEGACY`; se ainda
estiverem pendentes, continuam pelo executor sequencial antigo. Novos lotes usam o
modelo faseado.

## 7. Checkpoint da coleta

O checkpoint é JSON versionado, escrito de forma atômica e contém apenas dados
sanitizados necessários para reconstrução:

- `schema_version`, `client_id`, `tenant_id`, `run_id` e `logical_job_id`;
- modo, origem, tentativa e período `[início, fim)` no fuso do cliente;
- caminhos absolutos validados para manifests raw, dados normalizados intermediários
  e snapshots Cloud;
- UUID, origem, estado e chunks persistidos de cada export;
- estratégia VM, resultado do modo seletivo e fontes de coleta;
- TAGs selecionadas e referências aos artefatos por TAG;
- estado WAS e eventual alerta já sanitizado;
- hashes dos arquivos necessários;
- aviso de Cloud e estado de replay/coleta, quando habilitado.

Access key, secret key, token Cloud, cabeçalhos HTTP, hostname, IP, pessoa e e-mail
não são gravados no checkpoint. Antes da transição para `READY_FOR_BUILD`, todos
os caminhos e hashes obrigatórios são validados.

## 8. Fluxo remoto

1. O lote cria um trabalho `REMOTE_QUEUED` por cliente.
2. Um worker remoto reivindica o trabalho e registra `REMOTE_RUNNING`.
3. O subprocesso abre ou retoma os exports, grava o manifesto parcial antes do
   primeiro chunk e persiste chunks disponíveis imediatamente.
4. VM só termina com estado remoto `FINISHED` e tratamento de todos os chunks.
5. WAS manual pode ir para `WAITING_WAS_DECISION`. Em lote ou automático, repete
   apenas WAS uma vez e, se falhar novamente, continua sem WAS com alerta.
6. Cloud habilitado coleta a fotografia atual e persiste o snapshot bruto nessa
   fase. Falha Cloud preserva as regras opcionais atuais e não invalida VM.
7. O subprocesso fecha um checkpoint íntegro e retorna `COLLECTION_READY`.
8. O repositório valida o checkpoint e muda atomicamente para
   `READY_FOR_BUILD`.

Se a coleta falhar, o trabalho termina ou aguarda decisão conforme a classificação
existente. Nenhum DOCX é criado durante essa fase.

## 9. Fluxo local

1. O único worker local reivindica o trabalho mais antigo em
   `READY_FOR_BUILD` e registra `BUILD_RUNNING`.
2. Valida cliente, período, hashes e raiz de armazenamento do checkpoint.
3. Normaliza os dados brutos e monta os datasets geral, customizado, TAG e Cloud.
4. Gera os documentos configurados.
5. Valida a publicação, persiste o histórico compacto, define `MAIN` conforme as
   regras atuais e só então aplica a limpeza de temporários pesados.
6. Marca o trabalho como `COMPLETE` ou `COMPLETE_WITH_WARNINGS`.

Uma retentativa de `LOCAL_BUILD` reutiliza o mesmo checkpoint e nunca abre export
VM/WAS. Arquivo parcial de documento é escrito em staging e somente substitui o
destino após sucesso, preservando idempotência.

## 9.1. Retentativa seletiva por componente

A ação **Tentar componentes com falha** cria uma tentativa vinculada ao conjunto
original e seleciona somente componentes `FAILED` ou `INTERRUPTED` marcados como
retryable. Também existe uma janela explícita para escolher entre os componentes
elegíveis; componentes concluídos ficam desabilitados nessa ação.

O retry recomeça na primeira etapa não validada:

- raw e dataset Cloud íntegros: repete apenas renderização, validação e publicação;
- coleta Cloud incompleta: retoma o checkpoint/paginação Cloud;
- VM com UUID/chunks válidos: retoma o export, sem criar operação duplicada;
- VM raw completo: normaliza e gera os documentos dependentes sem API;
- WAS falho com VM válido: repete WAS e repara atomicamente apenas os documentos
  que contêm as seções WEB.

Documentos válidos do conjunto original não são copiados nem substituídos até que
os novos documentos passem pela validação. Sucesso adiciona ou troca somente as
referências afetadas no mesmo manifesto de publicação e mantém a identidade do
conjunto/`MAIN`. Falha da retentativa remove o staging novo e conserva hashes,
arquivos e referências anteriores.

Cada exceção registra uma etapa sanitizada entre `COLLECTION`, `DATASET`,
`RENDER`, `DOCUMENT_VALIDATION`, `SNAPSHOT_PUBLICATION` e
`REPORT_PUBLICATION`. O alerta amigável continua visível, mas deixa de descartar o
código e a etapa necessários ao diagnóstico.

## 10. Timeout, ausência de progresso e cancelamento

O limite total padrão de processamento remoto é 7.200 segundos. A ausência de
chunks gera alertas intermediários, mas não cancela automaticamente em 900 ou
1.800 segundos. Ao atingir 7.200 segundos:

- job criado pela execução atual, sem progresso remoto nem chunk local: pode ser
  cancelado automaticamente e fica retryable;
- job criado pela execução atual com algum progresso: não é cancelado; UUID,
  chunks e checkpoint parcial são preservados;
- job reutilizado ou fornecido: nunca é cancelado automaticamente;
- em todos os casos, uma nova tentativa consulta primeiro o UUID preservado.

`FINISHED` com chunks persistidos entra em `READY_FOR_BUILD`. `PROCESSING`,
`QUEUED` ou equivalente continua remoto. `CANCELLED`, `FAILED`, `ERROR`, `404` ou
conteúdo expirado permite novo export somente depois da validação já existente.

## 11. Pausa, parada e reinício

- **Pausar lote** impede novas reivindicações remotas e locais. Trabalho remoto já
  ativo chega ao próximo checkpoint cooperativo antes de parar.
- **Parar lote** solicita interrupção cooperativa aos subprocessos ativos,
  preserva UUIDs/chunks e cancela trabalhos ainda não iniciados.
- Ao reiniciar o servidor, jobs `REMOTE_RUNNING` ou `BUILD_RUNNING` abandonados
  são reconciliados. Coleta retoma pelo UUID/manifest; montagem retoma pelo
  checkpoint sem API.
- `Tentar falhas/interrompidos` cria tentativas ligadas ao trabalho anterior e
  conserva a fase mais avançada validada.
- `Executar todos novamente` cria um novo lote completo.

## 12. Interface

O card do lote mostra:

- coletando remotamente;
- aguardando a Tenable;
- prontos para montagem;
- montando localmente;
- concluídos, avisos, falhas e interrompidos;
- concorrência remota efetiva e fila local.

O card do cliente mantém UUID, origem e `chunks concluídos/total`, acrescentando a
fase atual. `0/0` é apresentado como “aguardando a Tenable informar chunks”, sem
sugerir progresso inexistente.

Quando um conjunto termina parcial, a interface mostra os estados de `VM`, `WAS`
e `Cloud` separadamente. O botão **Tentar componentes com falha** informa quais
serão executados. No detalhe do conjunto, **Selecionar componentes** permite
confirmar apenas os componentes falhos elegíveis, por exemplo **Tentar somente
Cloud**. A ação **Gerar relatório** permanece uma nova execução completa.

Em **Gerar todos**, o padrão é concorrência automática. A configuração avançada
permite reduzir o número de coletores remotos, mas a fila local permanece em um.
A automação mensal usa a mesma política sem interação.

### 12.1. Seleção do Gerar todos

O botão **Gerar todos** abre uma janela de seleção antes de criar o lote. Todos os
clientes ativos e elegíveis começam marcados. O operador pode:

- pesquisar pelo nome ou identificador do cliente;
- filtrar por analista responsável;
- filtrar por **Sem responsável**;
- marcar ou desmarcar individualmente;
- selecionar ou limpar somente os resultados visíveis;
- confirmar em **Gerar N clientes**.

Nenhum lote é criado quando a seleção fica vazia. O backend recebe e valida a lista
explícita de `selected_client_ids`; não confia apenas em exclusões calculadas no
navegador. O lote persiste a fotografia de `selected_client_ids`,
`excluded_client_ids` e do filtro usado para auditoria. Alterações futuras no
cadastro não modificam um lote já criado.

Clientes inativos não são elegíveis. Um conflito com outro lote ativo continua
sendo informado antes da confirmação e não é escondido pelo filtro visual. A
automação mensal permanece não interativa e seleciona todos os clientes ativos
configurados para execução automática.

### 12.2. Analistas responsáveis

O menu de administração oferece um catálogo simples de analistas com:

- `analyst_id` estável e gerado automaticamente;
- `display_name` obrigatório e único sem diferenciar maiúsculas/minúsculas;
- estado ativo/inativo;
- datas de criação e atualização.

Não há conta, senha ou permissão associada. Desativar um analista impede novas
atribuições, mas preserva clientes já associados e o histórico. Exclusão física é
recusada enquanto houver cliente associado; a ação normal é desativar.

O perfil do cliente ganha `responsible_analyst_id`, opcional e limitado a um
analista principal. A interface exibe o nome no card e permite alterá-lo em
**Gerenciar clientes**. O valor precisa existir no catálogo; um identificador
desconhecido é rejeitado. Clientes sem associação aparecem como **Sem
responsável**.

O painel principal recebe um filtro por analista que combina com a busca textual.
Esse filtro altera somente os cards exibidos. Ele não filtra vulnerabilidades,
ativos, TAGs, relatórios ou um lote que já tenha sido confirmado.

### 12.3. Persistência do catálogo

O catálogo fica em `orchestration/analysts.json`, arquivo local sem secrets e
gerenciado atomicamente pela mesma camada de configuração dos clientes. O perfil
JSON de cada cliente guarda apenas `responsible_analyst_id`. PostgreSQL registra a
fotografia do responsável no payload sanitizado do lote para auditoria, sem se
tornar a fonte de verdade do cadastro.

Gravações usam substituição atômica e validação de esquema. Nomes são tratados
como dado pessoal operacional: podem aparecer na interface e na auditoria do lote,
mas não em logs técnicos, checkpoints de export ou documentos gerados.

## 13. Configuração

Novas opções, sem secrets:

- `remote_collection_workers`: inteiro de 0 a 64; padrão `0` (todos os clientes
  selecionados, respeitando o teto de 64);
- `local_build_workers`: fixo em `1` nesta versão;
- `remote_processing_timeout_seconds`: padrão `7200`;
- `remote_progress_warning_seconds`: padrão `900`, apenas alerta;
- `max_clients_per_batch`: padrão `64`.

Novos campos de configuração, também sem secrets:

- `analyst_id`, `display_name` e `active` no catálogo de analistas;
- `responsible_analyst_id` opcional no perfil do cliente;
- `selected_client_ids`, `excluded_client_ids` e fotografia do responsável nas
  opções persistidas de cada lote manual.
- `selected_components` e `source_run_id` em tentativas seletivas;
- estágio e resultado sanitizados por componente em
  `report_component_attempts`.

Perfis de cliente continuam controlando propriedades seletivas e tamanhos de
chunk. O servidor não altera esses valores automaticamente.

## 14. Segurança e armazenamento

- Segredos permanecem apenas nos `.env` locais ignorados pelo Git.
- Eventos, checkpoints e erros passam por sanitização antes do PostgreSQL.
- Subprocessos de clientes diferentes nunca compartilham ambiente carregado.
- Caminhos de checkpoint são resolvidos e precisam estar sob a raiz de dados.
- A reserva de disco considera todos os coletores que podem gravar ao mesmo tempo.
- Dados brutos só são removidos após publicação validada e histórico compacto.
- Falha de um tenant não interrompe outros clientes do lote.

## 15. Compatibilidade e implantação

1. A migração é aplicada antes de iniciar os novos workers.
2. `run-client` e lotes `LEGACY` continuam sequenciais.
3. Novos lotes da interface e da automação recebem `execution_model=STAGED_V1`.
4. A interface expõe a fase somente quando o backend já suporta a migração.
5. O recurso não inicia automaticamente lotes antigos, UUIDs importados ou
   retentativas; somente ações já enfileiradas e autorizadas são executadas.
6. O primeiro teste real será feito em clientes escolhidos pelo analista, sem
   habilitar todos os tenants automaticamente durante a implantação.
7. Clientes existentes são migrados com `responsible_analyst_id = null`; nenhum
   responsável é inferido pelo nome ou histórico.

## 16. Testes obrigatórios

- transições válidas e inválidas entre fases;
- reivindicação concorrente de clientes diferentes e exclusão do mesmo cliente;
- concorrência automática com mais de vinte clientes e teto de 64;
- somente um `BUILD_RUNNING` por instância;
- chunk persistido antes de `FINISHED` e retomado sem novo download;
- timeout de 7.200 segundos com e sem progresso;
- job reutilizado nunca cancelado automaticamente;
- reinício durante coleta e durante montagem;
- WAS manual, WAS automático e continuação sem WAS;
- checkpoint adulterado, fora da raiz ou com hash inválido;
- falha Cloud sem perda dos dados VM;
- retry local sem chamada à API;
- status independente de VM, WAS e Cloud, inclusive publicação parcial;
- Cloud com raw/dataset íntegros retomando diretamente em renderização;
- Cloud sem checkpoint retomando apenas sua coleta;
- WAS reutilizando VM e reparando somente documentos dependentes;
- seleção recusada quando a dependência do componente não está disponível;
- substituição atômica no mesmo conjunto e preservação dos documentos válidos;
- falha na retentativa preservando manifesto, hashes e `MAIN` anteriores;
- persistência de `failure_code` e etapa sanitizada sem vazar a exceção;
- pausa, parada, retry incompleto e rerun completo;
- estados e contadores novos nos endpoints e na interface;
- migração PostgreSQL e compatibilidade de lotes `LEGACY`;
- execução mensal e botão **Gerar todos** usando `STAGED_V1`;
- CRUD do catálogo, unicidade de nome, desativação e bloqueio de exclusão em uso;
- vínculo opcional do responsável e rejeição de identificador desconhecido;
- modal com todos marcados, exclusão individual, seleção visível e seleção vazia;
- persistência exata dos clientes incluídos e excluídos no lote;
- filtro do painel e do modal por analista e **Sem responsável**;
- combinação do filtro com busca textual sem alterar o conteúdo dos relatórios;
- suíte completa, auditoria de secrets e validação dos guias.

## 17. Critérios de aceite

1. Um lote com vinte clientes pode mostrar vinte coletas remotas ativas, desde que
   não exista conflito por cliente e haja recursos mínimos.
2. Em nenhum momento existem duas montagens locais simultâneas.
3. Um cliente pronto começa a montagem sem esperar todos os demais terminarem a
   coleta.
4. Reiniciar a aplicação não perde UUID nem chunk e não repete coleta concluída.
5. Após duas horas sem conclusão, o operador recebe estado retryable e evidência
   suficiente para retomar; cancelamento automático respeita origem e progresso.
6. Relatórios, métricas, períodos, `MAIN`, TAG e regras WAS permanecem iguais aos
   contratos atuais.
7. A interface distingue espera Tenable, coleta, fila local e montagem.
8. Nenhuma coleta real é iniciada como parte dos testes automatizados.
9. Antes de **Gerar todos**, o operador visualiza os clientes elegíveis e pode
   excluir qualquer subconjunto da solicitação.
10. Cada cliente pode ter zero ou um analista responsável, escolhido de um
    catálogo administrável sem contas ou permissões.
11. O painel e a janela de geração filtram por responsável e por **Sem
    responsável**, mantendo busca e seleção consistentes.
12. Um conjunto parcial oferece retentativa somente dos componentes falhos e
    retryable, sem repetir os componentes concluídos.
13. O TRT8 equivalente ao caso diagnosticado, com dataset Cloud íntegro e falha de
    renderização, retoma em `RENDER` e preserva VM/WAS já publicados.
14. Se VM/WAS falhar e Cloud terminar, o documento Cloud permanece disponível e a
    nova tentativa não consulta Cloud novamente.
