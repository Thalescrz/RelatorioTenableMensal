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

## 3. Fora de escopo

- Alterar os cálculos, filtros temporais, textos, tabelas ou modelos DOCX.
- Alterar os tamanhos de chunk definidos nos perfis nesta entrega.
- Executar dois exports concorrentes do mesmo tipo para o mesmo cliente.
- Renderizar vários clientes em paralelo.
- Tratar `total_chunks > 0` como conclusão antes de `FINISHED`.
- Introduzir uma nova tecnologia de mensageria; PostgreSQL continua sendo a fonte
  durável da fila.

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

Em **Gerar todos**, o padrão é concorrência automática. A configuração avançada
permite reduzir o número de coletores remotos, mas a fila local permanece em um.
A automação mensal usa a mesma política sem interação.

## 13. Configuração

Novas opções, sem secrets:

- `remote_collection_workers`: inteiro de 0 a 64; padrão `0` (todos os clientes
  selecionados, respeitando o teto de 64);
- `local_build_workers`: fixo em `1` nesta versão;
- `remote_processing_timeout_seconds`: padrão `7200`;
- `remote_progress_warning_seconds`: padrão `900`, apenas alerta;
- `max_clients_per_batch`: padrão `64`.

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
- pausa, parada, retry incompleto e rerun completo;
- estados e contadores novos nos endpoints e na interface;
- migração PostgreSQL e compatibilidade de lotes `LEGACY`;
- execução mensal e botão **Gerar todos** usando `STAGED_V1`;
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
