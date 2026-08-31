# Controle durável de lotes, pausa, parada e retentativa — Especificação de design

**Data:** 2026-08-31  
**Status:** aprovado para revisão e planejamento  
**Projeto:** RelatorioTenableMensalv2

## 1. Objetivo

A geração iniciada por **Gerar todos** deve sobreviver ao reinício do servidor,
permitir acompanhamento por lote e oferecer ações seguras de pausa, parada,
retomada e retentativa. O analista deve poder distinguir:

- continuar o lote pendente;
- tentar somente clientes que falharam ou foram interrompidos;
- gerar novamente para todos os clientes selecionados.

Nenhuma dessas ações pode duplicar um cliente que já esteja executando ou na fila.
Checkpoints, UUIDs de export e chunks já persistidos continuam seguindo as regras
de recuperação VM e WAS existentes.

## 2. Problema atual

A interface mantém a fila em memória com um único worker. Cada cliente é iniciado
como um subprocesso independente, mas o estado web `QUEUED` ou `RUNNING` não é
durável. Reiniciar o servidor perde a fila ainda não executada e os vínculos usados
pelos botões de retentativa.

Há cancelamento confirmado de um export VM já falho, mas não existem:

- entidade de lote;
- pausa depois do cliente atual;
- parada controlada da geração;
- retomada de pendentes;
- retentativa coletiva somente dos erros;
- escolha explícita entre retry parcial e nova geração completa.

## 3. Decisões aprovadas

- PostgreSQL é a fonte de verdade dos lotes e dos trabalhos web.
- A execução continua sequencial; não será introduzido paralelismo.
- **Pausar após o atual** deixa o cliente corrente terminar e impede o início do
  próximo.
- **Parar lote** interrompe cooperativamente o cliente corrente, remove os
  pendentes da fila ativa e preserva os registros para retentativa.
- Parar o lote não cancela automaticamente o export remoto Tenable.
- O subprocesso interrompido preserva os checkpoints já gravados e termina com
  estado específico, diferente de falha técnica.
- **Retomar lote** continua os trabalhos que ainda estavam pendentes.
- **Tentar somente falhas e interrompidos** cria um novo lote derivado apenas com
  clientes elegíveis.
- **Gerar novamente para todos** cria um lote novo com todos os clientes
  selecionados e exige confirmação explícita.
- Clientes concluídos não entram na retentativa parcial.
- `COMPLETE_WITH_WARNINGS` não entra no retry geral; WAS e Cloud continuam usando
  suas retentativas específicas.
- Uma ação repetida deve ser idempotente e nunca criar trabalhos duplicados.
- Credenciais e dados de vulnerabilidades não são persistidos nas tabelas de fila.

## 4. Modelo de domínio

### 4.1. Lote

Um lote representa uma ação do usuário ou do agendador sobre um conjunto de
clientes e uma única configuração temporal. Campos mínimos:

- `batch_id`;
- origem `MANUAL_SINGLE`, `MANUAL_ALL` ou `AUTOMATIC_MONTHLY`;
- modo e período canônico `[início, fim)`;
- pedido sanitizado de geração;
- estado, contadores e timestamps;
- lote de origem, quando for retentativa;
- ação de derivação `RETRY_INCOMPLETE` ou `RERUN_ALL`;
- chave idempotente;
- ator local e motivo sanitizado para ações de controle.

Estados:

- `QUEUED`: criado, sem cliente iniciado;
- `RUNNING`: worker executando ou pronto para buscar o próximo;
- `PAUSE_REQUESTED`: pausa solicitada enquanto há cliente ativo;
- `PAUSED`: nenhum novo cliente pode iniciar;
- `STOP_REQUESTED`: parada cooperativa em andamento;
- `STOPPED`: ativo interrompido e pendentes retirados da fila ativa;
- `COMPLETE`: todos os trabalhos terminaram sem falha obrigatória;
- `COMPLETE_WITH_FAILURES`: não há trabalho ativo ou pendente, mas existe falha;
- `COMPLETE_WITH_WARNINGS`: todos terminaram e há somente alertas opcionais.

### 4.2. Trabalho do lote

Cada cliente selecionado possui um trabalho durável:

- `batch_job_id`, `batch_id` e `client_id`;
- posição original;
- estado e tentativa;
- `logical_job_id`, `run_id` e `retry_of_batch_job_id`;
- período e pedido sanitizado efetivamente usados;
- resumo dos componentes VM, WAS, TAG e Cloud;
- UUID, origem, estado remoto e contagem de chunks, sem conteúdo de finding;
- timestamps, código de saída e erro sanitizado;
- caminho relativo do checkpoint/control file quando existir.

Estados:

- `QUEUED`;
- `RUNNING`;
- `WAITING_WAS_DECISION`;
- `COMPLETE`;
- `COMPLETE_WITH_WARNINGS`;
- `FAILED`;
- `INTERRUPT_REQUESTED`;
- `INTERRUPTED`;
- `CANCELLED_BY_USER`.

`INTERRUPTED` significa que a aplicação parou cooperativamente e pode reaproveitar
checkpoint. `CANCELLED_BY_USER` representa um trabalho que ainda não havia
começado quando o lote foi parado.

## 5. Persistência PostgreSQL

Uma migration aditiva cria:

- `web_batches`;
- `web_batch_jobs`;
- `web_batch_events`.

`web_batch_events` mantém a trilha imutável de criação, início, progresso, pausa,
parada, retomada, derivação e conclusão. Atualizações de estado e eventos ocorrem na
mesma transação.

Restrições e índices garantem:

- uma chave idempotente única por ação;
- no máximo um trabalho ativo por cliente;
- unicidade de cliente dentro do lote;
- busca eficiente por lote, estado, cliente e criação;
- estados válidos por `CHECK`;
- nenhuma credencial em pedido, erro ou evento.

As tabelas existentes de `orchestration_runs`, `orchestration_clients` e
`report_runs` continuam registrando as execuções técnicas e publicações. O
`web_batch_job` apenas as referencia; não duplica métricas nem documentos.

Se PostgreSQL estiver indisponível, a interface pode continuar exibindo estado
local já carregado, mas bloqueia novas gerações e ações de controle. Não haverá
fallback silencioso para uma segunda fila apenas em memória.

## 6. Worker e recuperação após reinício

O worker deixa de usar `queue.Queue` como fonte de verdade. Ele reivindica
atomicamente o próximo `web_batch_job` elegível no PostgreSQL e mantém no máximo
um subprocesso ativo.

No início do servidor:

1. lotes terminais permanecem inalterados;
2. `PAUSED` e `STOPPED` permanecem parados;
3. trabalho que estava `RUNNING` vira `INTERRUPTED` se não houver confirmação
   segura do processo;
4. o lote correspondente entra em `PAUSED` com alerta de recuperação;
5. trabalhos `QUEUED` continuam preservados, mas só voltam a executar depois de
   **Retomar lote**;
6. nenhum export novo é criado durante a reconciliação.

Essa política evita que um reinício duplique um subprocesso ou um export ainda
ativo no backend Tenable.

## 7. Controle cooperativo do subprocesso

Cada trabalho recebe um arquivo de controle durável, sem credenciais, por exemplo:

`data/<modo>/control/web-batches/<batch_id>/<batch_job_id>.json`.

O arquivo contém identidade, ação solicitada, instante e versão. O subprocesso
verifica a solicitação:

- antes de cada componente;
- durante os ciclos de polling VM e WAS;
- entre páginas Cloud;
- antes da normalização, renderização e publicação.

Ao receber parada:

1. não inicia novas chamadas;
2. termina a escrita atômica em andamento;
3. preserva manifestos parciais e checkpoints;
4. não publica documentos incompletos;
5. retorna um código específico de interrupção;
6. o worker registra `INTERRUPTED`.

Se o processo não responder dentro do período de graça, o servidor termina somente
a árvore de processos pertencente ao `batch_job_id` confirmado. Esse fallback é
auditado. O export remoto não é cancelado; na retentativa, a aplicação consulta o
UUID preservado e decide entre retomar ou criar outro conforme o estado remoto.

## 8. Semântica das ações

### 8.1. Pausar após o atual

- Disponível em lote `RUNNING`.
- Muda o lote para `PAUSE_REQUESTED`.
- O trabalho corrente termina normalmente.
- Antes de reivindicar o próximo, o worker muda o lote para `PAUSED`.
- Não altera trabalhos `QUEUED`.

### 8.2. Retomar lote

- Disponível em `PAUSED`.
- Muda o lote para `RUNNING`.
- O worker continua pela posição pendente mais antiga.
- Não repete concluídos, falhos ou interrompidos.

### 8.3. Parar lote

- Disponível em `QUEUED`, `RUNNING`, `PAUSE_REQUESTED` ou `PAUSED`.
- Exige confirmação contendo o identificador curto do lote.
- Trabalho ativo recebe `INTERRUPT_REQUESTED` e depois `INTERRUPTED`.
- Pendentes viram `CANCELLED_BY_USER`.
- O lote termina em `STOPPED`.
- Nenhum export remoto é cancelado implicitamente.

### 8.4. Tentar somente falhas e interrompidos

Cria novo lote derivado contendo apenas:

- `FAILED`;
- `INTERRUPTED`;
- `CANCELLED_BY_USER`.

O pedido original e o período são copiados. Para cada cliente, checkpoints e UUIDs
compatíveis são descobertos pelas regras atuais. Export terminal ou expirado gera
novo export; export ativo ou `FINISHED` recuperável é retomado.

### 8.5. Gerar novamente para todos

Cria um lote independente para todos os clientes selecionados. Usa os valores
atuais do formulário e exige confirmação, pois pode abrir novos exports mesmo para
clientes já concluídos.

A ação é recusada enquanto qualquer cliente selecionado estiver `RUNNING` ou
`QUEUED` em outro lote. O analista deve aguardar, pausar ou parar o lote anterior.

## 9. Interface web

A página geral apresenta um card do lote ativo com:

- período e origem;
- progresso `finalizados/total`;
- contadores de concluídos, falhos, interrompidos e pendentes;
- cliente e componente atuais;
- **Pausar após o atual**, **Parar lote** ou **Retomar lote**, conforme o estado.

Ao pressionar **Gerar todos** quando existir lote anterior comparável, a interface
mostra duas ações:

1. **Tentar somente falhas e interrompidos** — lista a quantidade e não inclui
   concluídos;
2. **Gerar novamente para todos** — explica que uma nova coleta integral poderá ser
   criada.

Se o lote ainda estiver ativo, o diálogo não permite duplicação e oferece somente
as ações de controle cabíveis.

Os cards de cliente continuam exibindo seu próprio progresso e alerta. O estado do
lote não substitui as retentativas específicas WAS ou Cloud.

## 10. Contratos HTTP

Rotas propostas:

- `POST /api/batches` — cria lote;
- `POST /api/batches/{batch_id}/pause`;
- `POST /api/batches/{batch_id}/resume`;
- `POST /api/batches/{batch_id}/stop`;
- `POST /api/batches/{batch_id}/retry-incomplete`;
- `POST /api/batches/{batch_id}/rerun-all`;
- `GET /api/batches/{batch_id}`.

Cada mutação recebe uma chave idempotente e valida estado atual, ator, confirmação
quando necessária e ausência de conflito por cliente. Respostas `409` explicam
qual lote ou cliente impede a ação.

`/api/state` continua entregando uma visão agregada para a interface, agora
derivada do repositório durável.

## 11. Compatibilidade com o lote operacional atual

A fotografia local criada antes desta especificação pode ser importada uma única
vez. O importador:

1. valida schema, período e clientes sem ler credenciais;
2. cruza `run_id` com PostgreSQL e manifestos de orquestração;
3. consulta somente UUIDs já conhecidos quando necessário;
4. registra concluídos, falhos, interrompidos e não iniciados;
5. não cria, retoma ou cancela exports;
6. usa hash do arquivo como chave idempotente.

UUID `CANCELLED` permanece apenas como auditoria e exige novo export. Um chunk
isolado de job não `FINISHED` nunca é promovido como coleta válida.

## 12. Segurança, armazenamento e auditoria

- Secrets permanecem somente nos arquivos locais já ignorados pelo Git.
- Mensagens e eventos não contêm hostname, IP, pessoa, e-mail ou conteúdo de
  finding.
- Arquivos de controle são pequenos e entram na política de retenção depois do
  lote terminal.
- Checkpoints recuperáveis não são removidos enquanto houver trabalho
  `INTERRUPTED` elegível a retry.
- Parada, retomada e derivação registram ator, instante, lote, trabalho e razão.
- Somente o processo correspondente ao trabalho confirmado pode receber sinal de
  interrupção.

## 13. Falhas e concorrência

- Duplo clique em qualquer ação retorna o mesmo resultado idempotente.
- Dois workers não reivindicam o mesmo trabalho.
- Um cliente não executa simultaneamente em dois lotes.
- Falha ao gravar a transição no PostgreSQL impede iniciar o subprocesso.
- Falha após iniciar, mas antes de registrar PID, pausa o lote para reconciliação.
- Parada durante publicação não remove a última publicação válida.
- Retentativa não reutiliza chunks de UUID diferente.
- Reinício nunca transforma trabalho interrompido em concluído.
- Pausa não ocupa worker e não bloqueia a visualização de outros lotes.

## 14. Testes e critérios de aceite

### Persistência e estados

- lote e trabalhos sobrevivem a reinício;
- transições inválidas são rejeitadas;
- reivindicação concorrente retorna um único trabalho;
- pedido e eventos não contêm secrets;
- importação da fotografia atual é idempotente.

### Pausa e parada

- pausa durante cliente ativo aguarda sua conclusão;
- pausa sem ativo entra imediatamente em `PAUSED`;
- retomada executa somente pendentes;
- parada marca ativo como `INTERRUPTED` e pendentes como
  `CANCELLED_BY_USER`;
- parada não chama cancelamento Tenable;
- fallback termina somente a árvore do trabalho confirmado.

### Retentativa

- retry parcial inclui apenas `FAILED`, `INTERRUPTED` e
  `CANCELLED_BY_USER`;
- concluídos e `COMPLETE_WITH_WARNINGS` ficam fora;
- UUID ativo ou recuperável é retomado;
- UUID cancelado, ausente ou expirado cria novo export;
- cliente ocupado impede lote duplicado;
- rerun integral exige confirmação e cria lote independente.

### Interface

- card mostra estado e contadores do lote;
- botões aparecem somente nos estados válidos;
- **Gerar todos** apresenta as duas escolhas quando aplicável;
- conflito mostra lote e cliente sem expor dados sensíveis;
- atualização da página ou reinício do servidor preserva o estado exibido.

### Regressão

- geração individual continua funcionando;
- fila permanece sequencial;
- política manual/automática de WAS não muda;
- relatório geral continua sem filtro de TAG;
- publicação, `MAIN`, retenção e exclusão de conjuntos mantêm os contratos atuais.

## 15. Fora de escopo

- paralelismo entre clientes;
- suspensão do processo pelo sistema operacional;
- cancelamento remoto automático ao parar lote;
- alteração de métricas ou conteúdo dos DOCX;
- retentativa geral automática sem limite;
- edição manual de tabelas PostgreSQL pela interface.
