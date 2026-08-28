# Recuperação do WAS com decisão do analista — Especificação de design

**Data:** 2026-08-28  
**Status:** aprovado para planejamento  
**Projeto:** RelatorioTenableMensalv2

## 1. Objetivo

Quando a coleta Tenable Web Application Scanning (WAS) falhar, expirar ou ficar
sem progresso, a aplicação deve explicar o problema sem invalidar a coleta
Tenable Vulnerability Management (VM) já concluída. Em uma execução manual pela
interface, o analista decide entre continuar a geração sem WAS ou tentar somente
o WAS novamente. Em uma execução automática mensal, a aplicação continua sem
interação, publica os documentos possíveis e deixa uma retentativa WAS disponível.

O fluxo não repete inventário, export VM, normalização VM ou recortes por TAG.

## 2. Comportamento atual

O WAS já é opcional e separado do VM. Hoje, falhas conhecidas são convertidas em
`WAS_NOT_AVAILABLE` ou `WAS_COLLECTION_UNAVAILABLE`; a execução continua e os
documentos são publicados. A interface mostra o aviso, mas não diferencia bem o
componente WAS e não oferece uma retentativa isolada. Uma retentativa geral volta
a executar etapas VM desnecessariamente.

O novo comportamento mantém a tolerância a falhas e acrescenta decisão,
recuperação e mensagem operacional explícitas.

## 3. Decisões aprovadas

- O controle **Vulnerabilidades WEB** representa `scope.was.enabled`.
- WAS permanece opcional e nunca invalida uma coleta VM válida.
- Execução manual iniciada pela interface aguarda a decisão do analista antes de
  renderizar e publicar o conjunto final quando o WAS falhar.
- Execução automática mensal nunca aguarda interação: continua sem WAS, publica o
  conjunto e registra alerta com retentativa posterior.
- A retentativa executa somente WAS e a reconstrução documental necessária; não
  repete coleta VM, ativos, TAGs ou Cloud Security.
- Uma nova falha WAS volta a oferecer as mesmas alternativas.
- O relatório não apresenta falha de coleta como ausência de vulnerabilidades.
- UUID, origem, estado, chunks e razão sanitizada permanecem disponíveis ao
  analista.
- Job WAS reutilizado, fornecido ou retomado nunca é cancelado automaticamente.
- Cancelamento de job criado pela execução depende das regras seguras vigentes e
  de confirmação explícita quando iniciado pela interface.

## 4. Estados da execução

A fila web passa a reconhecer os estados terminais e intermediários:

- `QUEUED`: aguardando worker;
- `RUNNING`: coleta ou geração em andamento;
- `WAITING_WAS_DECISION`: VM concluído e WAS indisponível; requer decisão manual;
- `COMPLETE`: documentos publicados sem alerta bloqueante;
- `COMPLETE_WITH_WARNINGS`: documentos publicados, mas WAS ou outro componente
  opcional ficou indisponível;
- `FAILED`: componente obrigatório impediu a publicação.

`WAITING_WAS_DECISION` não ocupa um worker nem bloqueia outros clientes da fila,
mas continua visível como atenção necessária e preserva o staging necessário.

## 5. Checkpoint de recuperação

Depois da normalização VM e antes da coleta WAS, a aplicação grava um checkpoint
durável e idempotente contendo:

- cliente, tenant, run, tentativa e período `[início, fim)`;
- modo manual ou automático;
- versão do perfil, templates, métricas e aplicação;
- caminhos e hashes das fontes VM normalizadas;
- dataset editorial necessário aos documentos geral, customizado e por TAG;
- estado dos componentes opcionais;
- consulta WAS sanitizada;
- documentos ainda não publicados;
- estado e decisão WAS.

O checkpoint nunca contém credenciais. Ele permite retomar a mesma execução sem
consultar novamente a API VM. Dados intermediários pesados seguem a política de
retenção vigente: só são removidos depois de publicação validada e persistência do
histórico compacto. Enquanto houver decisão manual pendente, a interface mostra o
staging como retenção operacional pendente.

## 6. Fluxo manual

1. A execução coleta ativos e vulnerabilidades VM normalmente.
2. A normalização e o checkpoint de recuperação são concluídos.
3. O WAS é executado se **Vulnerabilidades WEB** estiver habilitado.
4. Se o WAS terminar, a execução segue sem intervenção.
5. Se o WAS falhar, expirar ou ficar sem progresso, o processo persiste o estado,
   encerra a etapa atual de forma controlada e a fila assume
   `WAITING_WAS_DECISION`.
6. A interface apresenta causa, UUID, origem, estado e chunks, com dois botões:
   - **Continuar sem WAS**;
   - **Tentar WAS novamente**.
7. **Continuar sem WAS** retoma o checkpoint, gera os documentos e publica o
   conjunto com `COMPLETE_WITH_WARNINGS`.
8. **Tentar WAS novamente** reabre somente o componente WAS. Em caso de sucesso,
   normaliza os dados WEB e retoma a renderização. Em nova falha, retorna para
   `WAITING_WAS_DECISION`.

A decisão é confirmada na interface e auditada com instante, ator local, run e
UUID, sem conteúdo sensível.

## 7. Fluxo automático mensal

Uma execução automática não entra em `WAITING_WAS_DECISION`. Ao falhar o WAS:

1. registra aviso estruturado e o estado do export;
2. continua imediatamente com o checkpoint VM;
3. publica os documentos como `COMPLETE_WITH_WARNINGS`;
4. mantém uma cápsula compacta de recuperação para retentativa WAS;
5. exibe **Tentar WAS novamente** no conjunto publicado.

A cápsula contém somente os dados normalizados e editoriais indispensáveis para
recriar os documentos afetados. Raw e chunks pesados continuam sujeitos à limpeza
normal depois da publicação. A cápsula é removida após retentativa bem-sucedida,
exclusão do conjunto ou expiração pela política operacional documentada.

## 8. Semântica da retentativa WAS

Antes de iniciar outro export, a aplicação consulta o UUID anterior:

- `FINISHED`: baixa e valida os chunks existentes;
- `QUEUED` ou `PROCESSING`: informa que o job ainda está ativo; job criado pela
  execução pode ser cancelado somente após confirmação explícita;
- `CANCELLED` ou `ERROR`: inicia um novo export;
- UUID expirado ou não localizado: inicia um novo export e registra a substituição;
- origem `reused`, `provided` ou `resumed`: nunca cancela automaticamente.

A interface deve dizer claramente se o botão apenas retomará o UUID, cancelará um
job próprio confirmado ou criará um novo export. O novo UUID é associado ao mesmo
run e a uma nova tentativa do componente WAS.

## 9. Publicação e documentos

Sem WAS por falha operacional, as seções WEB usam uma mensagem específica, por
exemplo:

> Não foi possível obter os dados de vulnerabilidades WEB nesta execução. Os
> resultados de Vulnerability Management foram processados normalmente.

Essa mensagem é diferente de:

> Neste mês não foram identificadas vulnerabilidades WEB.

A segunda frase só pode ser usada quando a coleta WAS concluiu e confirmou
população vazia.

Uma retentativa bem-sucedida produz uma nova versão controlada dos documentos
afetados, vinculada ao mesmo período e à mesma coleta VM. A publicação é atômica:
o conjunto anterior permanece disponível até os novos DOCX passarem por validação,
registro e hash. A referência `MAIN` não muda silenciosamente; segue as regras
vigentes de execução automática e promoção manual.

## 10. Interface web

O card e a área de alertas distinguem `VM`, `WAS`, `Cloud Security` e `TAG`. Um
aviso WAS nunca recebe o rótulo genérico de TAG.

Para decisão manual, a interface mostra:

- **WAS requer decisão**;
- mensagem curta e acionável;
- UUID e origem;
- estado remoto e progresso `concluídos/total`;
- tempo sem progresso e limite aplicado, quando disponíveis;
- **Continuar sem WAS**;
- **Tentar WAS novamente**.

Para conjunto automático já publicado, mostra o aviso WAS e somente a ação
**Tentar WAS novamente**, pois a continuação sem WAS já ocorreu.

Os botões ficam desabilitados enquanto existe outra operação ativa para o mesmo
cliente. Requisições repetidas usam chave idempotente baseada em run e decisão.

## 11. Contratos de aplicação e web

O caso de uso de coleta WAS retorna falha estruturada com:

- código;
- mensagem sanitizada;
- retentabilidade;
- UUID, origem e estado;
- chunks concluídos e totais;
- fase do timeout;
- indicador de progresso;
- possibilidade de cancelamento seguro.

A execução principal retorna um resultado de decisão, em vez de transformar toda
falha WAS imediatamente em publicação. Um comando de retomada recebe `run_id` e
uma decisão canônica:

- `continue_without_was`;
- `retry_was`.

Rotas web específicas validam o cliente, o estado pendente, a confirmação, a
existência do checkpoint e a ausência de trabalho concorrente. O mesmo contrato é
usado para a retentativa posterior de uma execução automática.

## 12. Persistência e auditoria

O PostgreSQL registra:

- status do componente WAS por run e tentativa;
- UUID, origem e estado remoto;
- erro sanitizado e retentabilidade;
- decisão do analista;
- instante e ator da decisão;
- checkpoint/cápsula disponível ou expirada;
- documentos substituídos pela retentativa;
- resultado final `COMPLETE` ou `COMPLETE_WITH_WARNINGS`.

As migrations são aditivas e numeradas. Credenciais, hostname, IP, URL de alvo,
nome de aplicação e conteúdo de finding não entram nos registros operacionais de
erro ou auditoria.

## 13. Falhas e concorrência

- Checkpoint ausente ou incompatível impede a retomada e produz mensagem
  acionável; nunca dispara VM novamente de forma implícita.
- Duplo clique ou repetição HTTP não cria duas retentativas.
- Outra execução do mesmo cliente não pode iniciar enquanto uma retomada WAS está
  ativa; uma decisão aguardando não ocupa worker.
- Reinício do servidor preserva `WAITING_WAS_DECISION` por meio do estado durável.
- Falha na renderização após uma retentativa WAS não remove o conjunto publicado
  anteriormente.
- Falha do PostgreSQL não autoriza sobrescrever documentos nem perder o estado de
  decisão.
- Expiração da cápsula remove a ação de retentativa e explica que seria necessária
  uma nova execução completa.

## 14. Testes e critérios de aceite

### Coleta e decisão

- timeout WAS manual produz `WAITING_WAS_DECISION` após VM concluído;
- autenticação/permissão WAS manual também oferece continuação segura;
- execução automática continua e termina `COMPLETE_WITH_WARNINGS`;
- WAS vazio concluído não é tratado como falha;
- WAS indisponível nunca é apresentado como zero findings;
- checkpoint incompatível não reinicia VM.

### Retentativa

- `retry_was` não chama coletores de ativos ou VM;
- UUID `FINISHED` é retomado sem criar export;
- UUID terminal permite criar nova tentativa;
- job reutilizado/fornecido/retomado não é cancelado automaticamente;
- cancelamento confirmado verifica UUID e origem;
- nova falha volta ao estado de decisão;
- sucesso gera e valida somente os documentos afetados.

### Interface

- alerta identifica WAS, não TAG;
- os dois botões aparecem somente na execução manual pendente;
- execução automática publicada oferece apenas retentativa;
- confirmação contém run e UUID;
- duplo envio é idempotente;
- reinício do servidor recupera a decisão pendente.

### Publicação e retenção

- documentos VM continuam disponíveis sem WAS;
- retentativa não altera números VM nem período;
- substituição documental é atômica;
- `MAIN` permanece coerente;
- staging pesado só é limpo depois da publicação válida;
- cápsula de recuperação é removida conforme a política.

### Regressão

- cliente com WAS desabilitado não chama endpoints WAS;
- Cloud Security continua independente;
- relatório geral permanece sem filtro de TAG;
- geração por TAG, exclusão, download, histórico e fila multicliente mantêm o
  comportamento vigente.

## 15. Fora do escopo

- retentar VM e WAS simultaneamente;
- reconstruir WAS histórico que a API não consegue reproduzir;
- manter raw/chunks pesados permanentemente;
- cancelar automaticamente export WAS reutilizado ou fornecido;
- alterar a estrutura editorial além das mensagens operacionais aprovadas;
- transformar a interface em um orquestrador distribuído.

## 16. Documentação a atualizar na implementação

- `DESIGN.md`;
- `docs/13-was-fase8.md`;
- `docs/18-main-retentativas-inteligencia-operacao.md`;
- `docs/20-arquitetura-e-fluxo-de-dados.md`;
- `docs/22-guia-operacional.md`;
- `docs/23-guia-de-desenvolvimento.md`;
- runbook da skill `operating-tenable-reports`.

