# Coleta mensal durável com retentativa automática em três janelas

**Status:** desenho aprovado em conversa e consolidado para revisão em
04/09/2026.

## 1. Contexto

O botão **Gerar todos** já cria lotes `STAGED_V1` com coleta remota concorrente,
checkpoint por cliente e montagem local serial. A tarefa mensal ainda chama o
orquestrador legado por `scripts/run_monthly_orchestration.ps1`, usa a configuração
`max_parallel` do arquivo da carteira e possui uma política de retentativa diferente.
Consequentemente, executar manualmente e executar no primeiro dia do mês não oferece
as mesmas garantias de paralelismo, recuperação de UUID e visibilidade.

O teto remoto atual de 36.000 segundos é um orçamento único por UUID. Depois dele,
uma falha retentável exige que o analista derive outro lote. O novo contrato deve
automatizar uma segunda janela para todo componente retentável e permitir uma
terceira janela somente quando a segunda precisou criar uma nova operação remota.

Tenable VM e WAS usam exports assíncronos identificados por UUID, status e chunks.
Cloud Security usa GraphQL paginado e deve preservar cursor, páginas e dataset em
vez de simular um UUID. Chunks e páginas persistidos nunca podem ser descartados só
porque uma janela terminou.

Este documento complementa e, nos pontos de agendamento, retentativa, timeout e
filtros de lote, substitui o desenho
`2026-09-01-coleta-concorrente-renderizacao-serial-design.md`. Períodos, métricas,
modelos DOCX, TAGs, histórico compacto e regras editoriais permanecem inalterados.

## 2. Objetivos

1. Fazer **Gerar todos** manual e automático mensal usarem o mesmo coordenador
   durável `STAGED_V1`.
2. Iniciar por padrão a coleta remota de todos os clientes elegíveis em paralelo,
   sem limite artificial menor que a capacidade segura calculada.
3. Acompanhar `VM_CORE`, `WAS` e `CLOUD` de forma independente e concorrente para
   que um componente lento não impeça os outros de progredir.
4. Dar a cada componente uma primeira janela de até 10 horas e uma segunda janela
   automática de até 10 horas.
5. Liberar uma terceira janela automática de até 10 horas somente quando a segunda
   janela criou uma nova operação por identificador inválido, expirado ou
   irrecuperável.
6. Consultar e reaproveitar UUID, cursor, manifesto, chunks, páginas e datasets
   antes de iniciar qualquer operação substituta.
7. Depois das janelas automáticas aplicáveis, preservar tudo e oferecer
   retentativa manual seletiva por componente.
8. Mostrar logs sanitizados suficientes para o analista entender cada falha,
   substituição e decisão automática.
9. Transformar os indicadores numéricos do lote em filtros clicáveis da situação
   efetiva de seus clientes.
10. Contabilizar cada cliente uma única vez, agregando lote original,
    retentativas automáticas e retentativas manuais.
11. Permitir configurar e verificar o agendamento mensal no painel **Admin**, sem
    depender de edição manual de scripts para a rotina normal.

## 3. Fora de escopo

- Alterar a janela mensal `[início, fim)` ou os campos usados por `OPEN`,
  `REOPENED` e `FIXED`.
- Alterar cálculos, textos, tabelas, gráficos ou ordem dos documentos.
- Tornar a renderização DOCX paralela.
- Cancelar automaticamente exports remotos ainda válidos.
- Tratar `PROCESSING`, `QUEUED` ou ausência momentânea de chunks como identificador
  inválido.
- Repetir um componente já concluído durante retry automático ou manual.
- Criar novas operações após erro de autenticação, autorização, perfil ou consulta
  inválida.
- Permitir uma quarta janela automática.
- Manter indefinidamente uma cadeia automática de UUIDs substitutos.
- Permitir recorrências arbitrárias: nesta versão, o dia de execução permanece
  fixo no primeiro dia do mês.
- Iniciar uma coleta ao apenas salvar ou sincronizar o agendamento.
- Executar coleta real durante testes automatizados ou durante a implantação.

## 4. Decisão arquitetural

Um `DurableBatchCoordinator` passa a ser a única implementação de lotes novos. A
interface e o processo mensal apenas criam solicitações com origens diferentes:

- `MANUAL_GENERATE_ALL`: seleção e período confirmados pelo analista;
- `AUTOMATIC_MONTHLY`: todos os clientes ativos e o mês-calendário anterior;
- `AUTOMATIC_RECOVERY`: janela 2 ou 3 derivada sem interação;
- `MANUAL_RECOVERY`: retry explícito depois do esgotamento automático.

O coordenador persiste o lote no PostgreSQL, reivindica componentes remotos,
promove clientes prontos para a fila local e reconcilia processos abandonados. A
tarefa mensal deve funcionar sem a interface aberta: um comando headless cria o
lote idempotente, inicia os mesmos workers duráveis e permanece até o estado
terminal. Se a interface estiver ativa ao mesmo tempo, locks e leases no banco
impedem dupla reivindicação e múltiplas montagens locais.

O agendamento mensal deixa de executar diretamente o fluxo legado monolítico. O
comando `run-client` continua disponível para diagnóstico e compatibilidade, mas
não é a fonte de comportamento do botão ou do agendamento.

## 5. Unidade operacional

Cada cliente de uma família de lote possui três componentes possíveis:

- `VM_CORE`: inventário, findings VM, TAGs, normalização e documentos gerais,
  customizado e por TAG;
- `WAS`: findings WEB e reparo das seções WEB dos documentos dependentes;
- `CLOUD`: consultas GraphQL, dataset, snapshot e documento Cloud Security.

Componente desabilitado ou comprovadamente não contratado assume
`NOT_APPLICABLE`. Resultado remoto vazio, obtido com resposta válida e paginação
concluída, assume `COMPLETE`, não falha. `NOT_APPLICABLE` conta como resolvido para
o estado integral do cliente.

Os componentes remotos de um mesmo cliente podem progredir simultaneamente. A
dependência de WAS sobre o dataset VM vale somente para reparar/renderizar o
documento; não impede que seu export remoto seja iniciado e persistido enquanto VM
processa.

## 6. Modelo de janelas

Uma janela é um orçamento de acompanhamento de um componente, não o timeout de uma
única requisição HTTP. Polls e downloads continuam curtos e usam backoff.

### 6.1. Janela automática 1

- Duração máxima: 36.000 segundos.
- Inicia na primeira observação durável da operação do componente.
- VM/WAS gravam UUID e manifesto antes de esperar chunks.
- Cloud grava a origem, a consulta, a fonte corrente e o cursor depois de cada
  página validada.
- Erros transitórios de rede, `429` e `5xx` permanecem dentro da mesma janela.
- Falha terminal retentável pode antecipar a Janela 2; não é necessário esperar as
  10 horas completas.
- Componente concluído fica congelado e não participa das janelas seguintes.

### 6.2. Janela automática 2

- Duração máxima: 36.000 segundos.
- É criada somente para componentes incompletos e retentáveis.
- Consulta primeiro o identificador/checkpoint persistido.
- Se a operação anterior estiver válida, continua a mesma operação e baixa apenas
  conteúdo ausente.
- Se estiver inválida, expirada ou irrecuperável, registra a evidência sanitizada,
  inicia somente uma operação substituta para o componente e marca
  `replacement_created_in_window_2=true`.
- `PROCESSING` ou `QUEUED` com resposta válida não autoriza substituição.
- Timeout sem operação substituta leva diretamente ao retry manual.
- Timeout depois da criação de operação substituta libera a Janela 3.

### 6.3. Janela automática 3 condicional

- Duração máxima: 36.000 segundos.
- Só existe quando `replacement_created_in_window_2=true` para aquele componente.
- Consulta primeiro a operação criada na Janela 2 e preserva tudo que já foi
  baixado.
- Se esse identificador também estiver irrecuperável, pode criar uma última
  operação dentro do tempo restante da Janela 3.
- Criar outro identificador não reinicia a Janela 3 nem permite uma Janela 4.
- Sucesso conclui o componente; nova falha ou timeout produz
  `AUTOMATIC_RETRY_EXHAUSTED` e libera ação manual.

### 6.4. Retentativa manual

Depois do esgotamento automático, o analista seleciona lote, cliente e componentes
retentáveis. Cada ação manual cria uma janela explícita de até 10 horas, consulta
primeiro o estado preservado e segue as mesmas regras de substituição. Ela não
reabre janelas automáticas nem refaz componentes completos.

## 7. Máquina de estados por componente

Estados efetivos:

- `PENDING`;
- `RUNNING_WINDOW_1`;
- `RUNNING_WINDOW_2`;
- `RUNNING_WINDOW_3`;
- `COMPLETE`;
- `COMPLETE_WITH_WARNINGS`;
- `NOT_APPLICABLE`;
- `WAITING_MANUAL_RETRY`;
- `NON_RETRYABLE_FAILURE`;
- `INTERRUPTED`.

Transições centrais:

```text
PENDING
  -> RUNNING_WINDOW_1
      -> COMPLETE | NOT_APPLICABLE
      -> RUNNING_WINDOW_2
          -> COMPLETE | NOT_APPLICABLE
          -> RUNNING_WINDOW_3       somente se houve substituição na janela 2
          -> WAITING_MANUAL_RETRY   sem substituição ou falha definitiva do retry
              -> COMPLETE           após ação manual bem-sucedida
              -> WAITING_MANUAL_RETRY

Qualquer janela
  -> NON_RETRYABLE_FAILURE          autenticação, permissão, perfil ou contrato
  -> INTERRUPTED                    parada cooperativa local
```

O status do componente e o número da janela precisam sobreviver ao reinício. O
relógio usa timestamps UTC persistidos; reiniciar processo ou computador não
reinicia orçamento.

## 8. Identificadores e recuperação

### 8.1. VM e WAS

O identificador é o UUID do export. A recuperação consulta status e classifica:

- `QUEUED`/`PROCESSING`: continuar a operação;
- `FINISHED`: baixar chunks ausentes e validar o total;
- `CANCELLED`/`FAILED`/`ERROR`/`ABORTED`: operação irrecuperável;
- HTTP 404: UUID ou chunks expirados/ausentes;
- HTTP 401/403: falha não retentável até correção de credencial/permissão;
- HTTP 429/5xx/rede: falha transitória dentro da janela.

Cada chunk anunciado é persistido imediatamente de forma idempotente. O fingerprint
da consulta impede que um manifesto de outro cliente, tenant, período ou filtro
seja reutilizado. Uma substituição registra UUID anterior, novo UUID, motivo,
janela e consulta sem guardar payload sensível.

### 8.2. Cloud Security

Cloud não possui UUID de export. A unidade recuperável contém fonte GraphQL,
fingerprint da consulta, cursor, páginas concluídas, hashes e dataset parcial.

- Cursor e checkpoint válidos: continuar de `pageInfo.endCursor`;
- Página final e dataset válido: concluir sem nova API;
- Cursor rejeitado ou checkpoint incompatível: reiniciar somente a fonte afetada;
- Páginas preservadas são mescladas de forma idempotente por identidade canônica;
- Token inválido ou campo GraphQL não suportado: falha não retentável;
- Rate limit, `5xx` e rede: retry dentro da janela.

Reiniciar uma fonte desde a primeira página na Janela 2 conta como nova operação e
torna o componente elegível para a Janela 3 se voltar a expirar.

## 9. Classificação de falhas

| Situação | Retentativa automática | Nova operação |
|---|---:|---:|
| `PROCESSING`/`QUEUED` válido | Sim | Não |
| Chunk/página ainda indisponível | Sim | Não |
| Rede, `429` ou `5xx` | Sim, com backoff | Não imediatamente |
| UUID 404/expirado | Sim | Sim, somente o componente |
| Export terminal cancelado/falho | Sim | Sim, somente o componente |
| Cursor Cloud rejeitado | Sim | Reinicia somente a fonte |
| 401/403 ou token inválido | Não | Não |
| Perfil/filtro/consulta inválida | Não | Não |
| WAS sem licença confirmada | Não | `NOT_APPLICABLE` |
| Resultado válido com zero registros | Não | `COMPLETE` |
| Disco insuficiente | Aguarda ação local | Não abre operação remota |

Uma resposta 200 apenas confirma comunicação. Progresso real exige mudança de
estado, contador, novo chunk, nova página ou conclusão validada.

## 10. Concorrência

O padrão de ambos os modos é concorrente. A capacidade de clientes é:

```text
min(clientes elegíveis, 64, capacidade segura do PostgreSQL)
```

Não há limite padrão de quatro. Para que vinte ou mais clientes possam esperar a
Tenable simultaneamente:

- nenhuma conexão PostgreSQL fica aberta durante espera HTTP;
- leituras e gravações usam pool com leases curtos;
- claims continuam com `FOR UPDATE SKIP LOCKED`;
- não pode existir export equivalente duplicado para o mesmo tenant e fingerprint;
- componentes de tenants distintos não bloqueiam uns aos outros;
- um limitador por tenant controla rajadas de polls e downloads;
- a reserva de disco considera todos os chunks concorrentes;
- a montagem local mantém exatamente um worker global.

Um cliente entra na fila de montagem assim que todos os componentes habilitados
estiverem em estado terminal. Ele não espera os demais clientes do lote.

## 11. Família de lotes e idempotência

O lote inicial define um `root_batch_id`. Janela 2, Janela 3 e retries manuais são
tentativas descendentes da mesma família. A interface calcula o estado efetivo a
partir da tentativa mais recente de cada componente por `client_id`.

Contagens usam clientes únicos, nunca número de jobs ou tentativas. Uma falha antiga
deixa de contar como falha quando existe retry ativo ou sucesso posterior.

Para o mensal automático, a chave idempotente combina carteira, competência e modo:

```text
automatic-monthly:<orchestration_id>:<AAAA-MM>
```

Executar a tarefa duas vezes não cria outra família. Se a família já estiver ativa,
o processo apenas assume ou acompanha seus workers. Se estiver terminal, retorna o
resultado persistido sem iniciar nova coleta.

## 12. Agendamento mensal e configuração administrativa

Hoje o automático mensal não é configurável no painel: o modal **Admin** trata
somente o backfill de referências históricas. A instalação é externa, por
`scripts/install_monthly_task.ps1`, com horário padrão `06:00`, e a execução chama
`scripts/run_monthly_orchestration.ps1`. A entrega deve incorporar essa operação ao
painel sem ocultar que configuração da aplicação e tarefa do Windows são estados
distintos.

### 12.1. Execução headless

`scripts/run_monthly_orchestration.ps1` passa a chamar um comando headless que:

1. calcula o mês anterior no fuso configurado;
2. cria ou localiza a família idempotente;
3. inicia os mesmos pools remotos e o worker local usados pela interface;
4. aplica as janelas automáticas sem interação;
5. permanece até o lote ficar terminal ou até uma parada cooperativa;
6. grava resumo operacional e código de saída coerente.

O horário padrão recomendado é `00:05` do primeiro dia. Sem Janela 3, o pior caso
automático termina por volta de `20:05` do dia 1. Um componente elegível à Janela 3
pode terminar por volta de `06:05` do dia 2. Iniciar às `06:00` deslocaria o pior
caso da terceira janela para aproximadamente `12:00` do dia 2.

As configurações passam a declarar:

- `automatic_window_seconds = 36000`;
- `automatic_base_windows = 2`;
- `automatic_replacement_window = true`;
- `manual_retry_window_seconds = 36000`;
- `remote_collection_workers = 0` para capacidade automática;
- `local_build_workers = 1` obrigatório;
- `monthly_schedule.local_start_time = 00:05` na política e no instalador da
  tarefa.

### 12.2. Tela **Automação mensal** no painel Admin

O modal **Admin** passa a ter duas áreas navegáveis: **Automação mensal** e
**Referências históricas**. A nova área apresenta:

- situação da política salva: ativa ou inativa;
- situação real da tarefa do Windows: `NÃO INSTALADA`, `SINCRONIZADA`,
  `DIVERGENTE`, `DESABILITADA` ou `ERRO`;
- dia fixo **1º de cada mês**;
- horário local do computador, com padrão `00:05`;
- nome técnico fixo da tarefa, somente para conferência;
- fuso horário local detectado no host, somente para conferência;
- próxima execução calculada e última execução conhecida;
- competência que seria processada e quantidade de clientes elegíveis;
- resumo somente leitura da política de recuperação: duas janelas comuns de até
  10 horas e terceira janela condicional;
- montagem local serial e capacidade remota automática.

Os clientes elegíveis são os clientes ativos da carteira. A seleção de módulos
continua no perfil individual: WAS e Cloud só participam quando habilitados para o
cliente. Esta versão não cria uma segunda lista de inclusão mensal que possa
divergir silenciosamente da carteira.

As ações disponíveis são:

1. **Salvar configuração**: valida e grava somente a política declarativa;
2. **Validar sem executar**: calcula competência, clientes, chave idempotente,
   próxima execução e divergências, sem API Tenable e sem criar lote;
3. **Aplicar no Agendador do Windows**: após confirmação explícita, cria ou atualiza
   a tarefa com o script oficial;
4. **Ativar/Desativar automação**: após confirmação, sincroniza o estado da tarefa
   sem apagar histórico nem lotes.

Salvar a configuração nunca instala a tarefa, nunca altera uma tarefa sem
confirmação e nunca inicia **Gerar todos**. Se o processo não tiver privilégio para
alterar o Agendador, a interface preserva a configuração, mostra erro acionável e
fornece o comando equivalente para execução administrativa. A aplicação não pede,
armazena nem registra senha administrativa.

### 12.3. Fonte de configuração e sincronização

A política não secreta fica no mesmo arquivo da carteira, em um bloco versionado e
escrito atomicamente pelo `DashboardConfigStore`:

```json
{
  "monthly_schedule": {
    "enabled": false,
    "day_of_month": 1,
    "local_start_time": "00:05",
    "task_name": "Relatorios Tenable - Mensal"
  }
}
```

`day_of_month` aceita somente `1` nesta versão. O horário da tarefa segue o fuso
local do Windows; o intervalo de cada relatório continua calculado no fuso do
perfil do cliente. O nome técnico da tarefa também é fixo nesta versão; o comando
aplicado usa apenas caminhos resolvidos dentro do projeto e argumentos permitidos.

A consulta do estado é somente leitura e compara configuração declarada, comando,
horário e estado retornados pelo Agendador. Uma tarefa existente com o mesmo nome,
mas comando diferente, é marcada `DIVERGENTE` e não é sobrescrita sem confirmação.
Falha ao consultar o Agendador não impede o painel nem a geração manual.

O PostgreSQL permanece fonte do histórico operacional. A tela cruza a tarefa do
Windows com a família `AUTOMATIC_MONTHLY` mais recente para exibir última execução,
resultado e próxima ação; não considera o código de saída do Agendador suficiente
para afirmar que todos os relatórios foram concluídos.

## 13. Publicação e MAIN

Componentes independentes já válidos podem publicar seus documentos sem serem
descartados por outra falha. O conjunto assume `PARTIALLY_COMPLETE` enquanto falta
um componente habilitado e retentável.

- VM ausente não impede a preservação de um documento Cloud concluído;
- WAS ausente permite publicar VM e Cloud, com ausência WEB explícita;
- Cloud ausente permite publicar VM/WAS e mantém o documento Cloud pendente;
- retry posterior substitui ou acrescenta somente documentos afetados, após
  validação atômica;
- conjunto parcial nunca vira `MAIN` automaticamente;
- conjunto integral automático vira `MAIN` conforme as regras existentes;
- completar todos os componentes em uma recuperação valida o conjunto antes de sua
  promoção.

`NOT_APPLICABLE` não torna o conjunto parcial. Um módulo habilitado com credencial
inválida permanece falho até correção consciente.

## 14. Auditoria e logs

Cada transição gera evento sanitizado com:

- cliente, componente e família de lote;
- janela e tentativa;
- horário inicial, deadline e término;
- tipo e identificador remoto permitido;
- origem `created`, `provided`, `resumed` ou `replacement`;
- estado remoto, chunks/páginas concluídos e total conhecido;
- último progresso real e última comunicação válida;
- código da falha, retryable e etapa;
- motivo de substituição e identificador substituto;
- decisão automática e ação disponível.

UUIDs podem aparecer na interface operacional. Access key, secret key, token Cloud,
cabeçalhos, payloads de finding, hostname, IP, pessoa e e-mail não entram nesses
eventos.

Exemplo esperado:

```text
VM · Janela 1/3 · UUID A · PROCESSING · 2/6 · timeout de janela
VM · Janela 2/3 · UUID A · 404 · operação irrecuperável
VM · Janela 2/3 · UUID B · replacement · PROCESSING · 3/6
VM · Janela 3/3 · UUID B · PROCESSING · 4/6 · timeout
VM · automático esgotado · retry manual disponível
```

## 15. Interface e filtros do lote

O resumo da família apresenta indicadores clicáveis:

- **Todos**;
- **Pendentes**;
- **Em execução**;
- **Em retry automático**;
- **Aguardando retry manual**;
- **Semiconcluídos**;
- **Falha definitiva**;
- **Concluídos**.

As categorias, exceto **Todos**, são mutuamente exclusivas e somam o total de
clientes da família. A prioridade para calcular o estado efetivo é:

1. falha definitiva;
2. retry automático ativo;
3. execução inicial ativa;
4. aguardando retry manual;
5. semiconcluído;
6. concluído integral;
7. pendente.

Clicar no indicador filtra a lista abaixo. Clicar novamente ou usar **Limpar
filtro** volta a **Todos**. O cabeçalho informa `N de TOTAL clientes`. Busca textual
e filtro por analista combinam com o filtro de estado.

Cada linha mostra o estado efetivo e um resumo independente:

```text
Cliente X · Em retry automático
VM: Janela 2/2 · UUID ... · 2/6 chunks
WAS: Concluído
Cloud: Janela 3/3 · página 48 · cursor preservado
```

Ao expandir, a linha do tempo apresenta lote raiz e descendentes. O analista pode
abrir o conjunto relacionado e escolher **Tentar componentes pendentes** depois do
esgotamento automático.

## 16. Persistência

O modelo durável precisa representar, no mínimo:

- `root_batch_id` e `parent_batch_id`;
- origem e competência do lote;
- estado efetivo do cliente;
- componente, janela, tentativa e deadline;
- `replacement_created_in_window_2`;
- tipo do identificador remoto (`UUID`, `CURSOR`, `DATASET`);
- identificador atual e origem;
- manifesto/checkpoint e hashes;
- último estado, comunicação e progresso;
- falha sanitizada e decisão automática.

A configuração mensal declarativa precisa conter versão, estado ativo, dia fixo,
horário local e nome da tarefa. O banco registra eventos administrativos de
validação, sincronização, ativação e desativação com ator, resultado e mensagem
sanitizada; não duplica a configuração como uma segunda fonte editável.

Uma migration versionada amplia as tabelas de lotes/componentes existentes em vez
de criar uma segunda fila paralela. Lotes antigos permanecem `LEGACY`, sem receber
janelas automáticas retroativamente.

## 17. Reinício, pausa e parada

- Reinício recupera janela e deadline originais; não devolve 10 horas já gastas.
- Worker abandonado volta à fase compatível com seu checkpoint.
- **Pausar** impede novos claims, mas não cancela operação Tenable.
- **Parar lote** interrompe processos locais e preserva identificadores e artefatos.
- Nenhuma parada envia DELETE/cancelamento remoto por padrão.
- Retomar lote ativo continua a mesma janela se ainda houver orçamento.
- Falha terminal exige retry derivado; não é convertida em simples `resume`.

## 18. Testes obrigatórios

### 18.1. Janelas e relógio

- Janela 1 conclui sem criar retry;
- timeout/falha retentável cria Janela 2 automaticamente;
- Janela 2 com UUID válido continua o mesmo export;
- Janela 2 sem substituição expira e vai para retry manual;
- substituição na Janela 2 habilita exclusivamente a Janela 3;
- Janela 3 nunca cria Janela 4;
- reinício em cada janela preserva deadline;
- falha terminal antecipada não espera 10 horas para avançar.

### 18.2. VM/WAS

- estados `QUEUED`, `PROCESSING`, `FINISHED`, `FAILED`, `CANCELLED` e 404;
- download incremental e idempotente de chunks;
- chunk persistido não é baixado novamente;
- 401/403 não cria substituto;
- 429/5xx usam backoff sem trocar UUID;
- fingerprint incompatível bloqueia reuso;
- WAS não contratado vira `NOT_APPLICABLE`.

### 18.3. Cloud

- retomada por cursor;
- dataset válido pula a API;
- cursor rejeitado reinicia somente a fonte;
- páginas repetidas não duplicam registros;
- reinício de fonte na Janela 2 habilita Janela 3;
- token inválido não entra em loop automático.

### 18.4. Concorrência e banco

- mais de vinte clientes remotos podem ficar ativos;
- componentes de clientes distintos não se bloqueiam;
- export equivalente não é duplicado;
- conexões não ficam presas durante espera HTTP;
- exatamente um `BUILD_RUNNING` global;
- dois coordenadores não reivindicam o mesmo componente;
- lote mensal duplicado retorna a mesma família idempotente.

### 18.5. Interface

- contagem usa clientes únicos em toda a família;
- falha antiga com retry ativo conta somente em **Em retry automático**;
- sucesso posterior remove cliente dos filtros de falha;
- cada indicador filtra e desfaz o filtro;
- busca, analista e status se combinam;
- concluído exige todos os componentes habilitados ou não aplicáveis;
- detalhe exibe a linha do tempo e a ação manual correta.

### 18.6. Agendamento e painel Admin

- ausência da tarefa aparece como `NÃO INSTALADA`, sem quebrar o painel;
- tarefa igual à política aparece como `SINCRONIZADA`;
- horário, comando ou estado divergente aparece como `DIVERGENTE` ou
  `DESABILITADA`;
- salvar configuração não chama Agendador, API Tenable nem cria lote;
- validação calcula competência, clientes e chave idempotente sem efeitos externos;
- aplicar/ativar/desativar exige confirmação e usa runner simulado nos testes;
- falta de privilégio produz erro acionável sem corromper a configuração;
- cálculo da próxima execução respeita o relógio local e a virada do mês;
- o período de cada cliente continua usando o fuso de seu perfil;
- duas chamadas simultâneas de sincronização não criam tarefas conflitantes.

### 18.7. Publicação e segurança

- parcial não vira `MAIN`;
- reparo substitui somente documentos afetados;
- componentes concluídos nunca são coletados novamente;
- eventos e checkpoints não contêm secrets nem dados sensíveis;
- nenhuma coleta real é feita pela suíte automatizada;
- suíte completa, validação de guias, auditoria de secrets e `git diff --check`.

## 19. Critérios de aceite

1. Manual e mensal automático criam lotes `STAGED_V1` processados pelo mesmo
   coordenador.
2. A tarefa mensal funciona com a interface fechada e não duplica lote quando
   executada novamente para a mesma competência.
3. Todos os clientes elegíveis começam remotamente em paralelo até o teto seguro de
   64, sem segurar conexão PostgreSQL durante espera.
4. VM, WAS e Cloud possuem progresso e retentativa independentes.
5. Todo componente retentável recebe no máximo duas janelas automáticas comuns.
6. Somente componente que criou operação substituta na Janela 2 pode receber a
   Janela 3.
7. Não existe Janela 4 automática.
8. UUID/cursor válido é retomado; identificador irrecuperável é substituído sem
   interromper outros componentes ou clientes.
9. Chunks, páginas e documentos válidos sobrevivem a timeout, retry e reinício.
10. Depois do esgotamento, a interface oferece retry manual somente dos componentes
    pendentes e retentáveis.
11. Indicadores clicáveis mostram, sem dupla contagem, falhos, retries ativos,
    parciais e concluídos integrais da família.
12. O analista consegue reconstruir pelos logs o motivo de cada transição e
    substituição sem acessar conteúdo sensível.
13. Exatamente uma montagem DOCX ocorre por vez e cliente pronto não espera os
    demais coletores.
14. Conjunto parcial não é promovido automaticamente a `MAIN`.
15. A implantação não inicia lote, retry ou coleta real sem ação posterior do
    analista.
16. O painel Admin permite salvar e validar a automação mensal e distingue
    claramente configuração salva de tarefa do Windows instalada.
17. O painel mostra próxima execução, última família mensal e clientes elegíveis,
    sem expor secrets nem executar coleta durante a validação.
18. Sincronizar, ativar ou desativar a tarefa exige confirmação explícita e falha de
    privilégio não impede a operação manual da aplicação.

## 20. Estratégia de implantação

1. Implementar contratos e migration com workers desativados por feature flag.
2. Validar máquina de estados e relógio exclusivamente com fakes.
3. Integrar o coordenador à interface mantendo o mensal legado disponível como
   rollback temporário.
4. Testar um lote manual pequeno autorizado e acompanhar UUID/cursor/chunks.
5. Habilitar o comando mensal headless em modo dry-run para validar competência,
   clientes e idempotência.
6. Entregar a área **Automação mensal** no Admin primeiro com consulta, salvamento e
   validação sem efeitos externos.
7. Validar a sincronização com o Agendador usando runner simulado e depois uma
   tarefa de homologação, com autorização explícita.
8. Executar um mensal controlado de poucos clientes com autorização explícita.
9. Trocar a tarefa oficial do Windows para `00:05` somente depois da validação.
10. Remover o caminho mensal legado da rotina operacional, preservando comandos de
   diagnóstico compatíveis.

## 21. Atualização obrigatória da documentação

A implementação só pode ser considerada concluída depois de atualizar a
documentação vigente para refletir o comportamento realmente testado. A etapa
final inclui:

1. atualizar `README.md` com o fluxo mensal, o painel Admin e o caminho rápido de
   operação;
2. atualizar `DESIGN.md` com coordenador único, janelas, componentes, família de
   lotes, configuração declarativa e integração com o Agendador;
3. atualizar `docs/19-visao-geral-e-objetivos.md` e
   `docs/20-arquitetura-e-fluxo-de-dados.md` com objetivo e arquitetura vigentes;
4. atualizar `docs/22-guia-operacional.md` com configuração, validação, instalação,
   ativação/desativação, acompanhamento e recuperação manual;
5. atualizar `docs/23-guia-de-desenvolvimento.md` com estados, migrations,
   idempotência, testes e limites de concorrência;
6. atualizar `orchestration/clients.example.json` com o bloco
   `monthly_schedule`, sem dados reais;
7. atualizar o runbook da skill `operating-tenable-reports` e o `SKILL.md` somente
   se o roteamento operacional mudar;
8. preservar documentos históricos e acrescentar notas de substituição quando
   houver contrato antigo incompatível, em vez de apagar decisões anteriores;
9. conferir exemplos de comandos, textos da interface, links internos e ajuda da
   CLI contra a implementação final;
10. executar `tools/validate_project_guidance.py`, auditoria de secrets, suíte
    completa e `git diff --check` após as mudanças documentais.

Os documentos devem descrever apenas recursos entregues e validados. A tela Admin
não pode ser anunciada como capaz de sincronizar o Agendador antes da validação
real dessa integração.

## 22. Referências oficiais

- Tenable VM e WAS integrations:
  https://developer.tenable.com/docs/vm-and-was-integrations
- Download de chunk VM e expiração:
  https://developer.tenable.com/reference/exports-vulns-download-chunk
- Export VM e prevenção de duplicidade:
  https://developer.tenable.com/reference/exports-vulns-request-export
- Tenable Cloud Security integrations e paginação GraphQL:
  https://developer.tenable.com/docs/cloud-security-integrations
