# Arquitetura e fluxo de dados

## Visão por camadas

```text
Tenable VM / WAS / Cloud Security
        |
        v
infrastructure: clientes HTTP, exports, chunks e PostgreSQL
        |
        v
application: coleta, normalização, datasets, histórico e publicação
        |
        v
domain: identidades, períodos, métricas e regras puras
        |
        v
presentation: DOCX, gráficos, tabelas, textos e filtros de validação
        |
        v
webapp / CLI: configuração, fila, progresso e operação
```

As dependências apontam para as regras centrais. Código de domínio não deve depender
da interface, de Word ou de uma resposta HTTP específica.

## Componentes

- `src/tenable_reports/domain`: modelos normalizados, períodos, fingerprints,
  histórico e contratos dos datasets.
- `src/tenable_reports/application`: casos de uso que coordenam coleta,
  normalização, inteligência, TAGs, retenção, registro e publicação.
- `src/tenable_reports/infrastructure`: Tenable VM, Tenable WAS, cliente GraphQL
  Cloud, JSONL, PostgreSQL e migrations.
- `src/tenable_reports/presentation`: documentos Word, catálogo editorial, imagens,
  nomes de arquivos, tradução e filtros de conferência.
- `src/tenable_reports/webapp`: servidor local, endpoints e interface estática.
- `clients`: exemplos versionados e perfis gerenciados locais.
- `credentials`: exemplos versionados e segredos locais ignorados pelo Git.
- `templates`: template corporativo e ativos visuais.
- `data`: execução, staging e documentos publicados; ignorado pelo Git.

## Fluxo de uma execução

1. O modo de execução resolve o intervalo `[início, fim)` no fuso do cliente.
2. O perfil e os arquivos de credenciais são validados sem imprimir segredos.
3. O export de ativos fornece o inventário e as TAGs associadas aos UUIDs.
4. O export VM coleta findings gerais. O caminho combinado é o padrão; estratégias
   experimentais permanecem por cliente para diagnóstico.
5. Se habilitado, o WAS é consultado em fluxo independente e tolerante a falha.
6. Se Cloud Security estiver habilitado, o componente GraphQL valida o contrato e
   coleta uma fotografia independente, sem filtrar ou alterar o dataset VM.
7. Ativos e findings são normalizados. O vínculo válido é o UUID de ativo.
8. A janela temporal é aplicada localmente ao campo correto de cada estado.
9. Um dataset mensal reconciliado alimenta os relatórios gerais.
10. Para cada TAG habilitada, os UUIDs associados recortam localmente o mesmo dataset
   VM e formam um dataset de relatório por TAG.
11. O histórico compatível do cliente ou da própria TAG é recuperado do PostgreSQL.
12. Os DOCX são renderizados, validados, registrados e oferecidos para download.
13. Métricas compactas são persistidas; dados intermediários pesados de uma
   execução bem-sucedida são removidos.

## Fluxo Cloud Security

O componente Cloud usa `TCS_API_SECRET` e endpoint definido pelo ambiente do perfil.
Antes da coleta completa, um probe mínimo confirma autenticação e fontes obrigatórias.
O contrato de capacidades fica em cache sem o token e controla módulos opcionais;
nenhum campo GraphQL não comprovado é incluído silenciosamente.

A coleta pagina máquinas virtuais, imagens de contêiner, ocorrências de
vulnerabilidade, inventário, findings de postura e ciclo de vida. Descrição e
remediação usam consultas enriquecidas somente para os candidatos de Top 5 e Top
10. Consultas opcionais e isoladas de máquinas virtuais e imagens solicitam
`Software.Name` e `Vulnerabilities.FixedBy`. Se o tenant rejeitar esse campo, a
capacidade fica indisponível sem invalidar as fontes obrigatórias.

A normalização mantém a ocorrência consolidada por tipo de ativo, UUID e CVE para
os totais aprovados e, em paralelo, preserva combinações por CVE e software para as
tabelas de correção. Uma fotografia normalizada alimenta o único DOCX Cloud padrão
e o snapshot compacto PostgreSQL. O valor técnico de variante continua `expanded`
somente para compatibilidade com o histórico e com a restrição do banco.

O projeto legado `RelatorioCloudTenable` permanece documentado como base técnica
histórica do conector GraphQL: ajuda a localizar operações e campos já usados, mas
não é fonte de verdade para paginação, ausência, histórico, retry, segurança de
segredo ou publicação do fluxo atual.

Cloud é tolerante a falha. Erro obrigatório não publica DOCX Cloud parcial, preserva
checkpoints íntegros, registra alerta sanitizado e mantém VM, WAS, customizado e TAG.
A ação **Tentar Cloud novamente** reutiliza o contexto da execução e não repete a
coleta geral.

## Replay e coleta nova

Antes de abrir exports, o roteador reutiliza um snapshot compacto exato quando
elegível. Isso torna uma regeneração comum reprodutível e evita chamadas externas
desnecessárias. Em uma execução manual, o analista pode forçar novos jobs de API
sem apagar o snapshot anterior; rota, chunk e payload continuam vindo do perfil do
cliente.

Forçar uma coleta não seleciona outra estratégia de VM e não habilita propriedades
seletivas. O resultado registra essa decisão e a origem dos jobs para que a
interface diferencie replay de exports recém-criados.

## Período e estados VM

O contrato de domínio permanece `[início, fim)`. A interface, porém, recebe duas
datas inclusivas e normaliza a data final para a meia-noite do dia seguinte antes
de criar o job. Dessa forma, um intervalo visível de 01/07 a 31/07 chega ao domínio
como `[01/07 00:00, 01/08 00:00)`.

O filtro `since` enviado ao export limita o universo inferior, mas a fronteira
superior é garantida no processamento local. Isso é necessário porque a API pode
coletar até o momento em que a execução ocorre.

Por isso, uma coleta nova de um mês já encerrado pela rota `legacy_vm` é publicada
como `HISTORICAL_RECONSTRUCTION`, acompanhada de aviso explícito. O dado continua
delimitado pelo contrato local, mas não é apresentado como se tivesse sido
observado exatamente no fechamento original.

- `OPEN` e `REOPENED`: pertencem ao período quando `last_found` está no intervalo.
- `FIXED`: pertence ao período quando `last_fixed` está no intervalo.
- ressurgidas: são `REOPENED` com `resurfaced_at` no intervalo.

Os indicadores ativos e mitigados são calculados a partir desses conjuntos, sem
misturar `Last Seen` com `Last Fixed`.

## Exports, chunks e recuperação

O export VM é assíncrono. `total_chunks` maior que zero não significa conclusão: o
job só está completo quando o estado remoto é `FINISHED` e os chunks disponíveis
foram tratados.

O manifesto parcial é criado assim que a API fornece o UUID, mesmo antes do
primeiro chunk. Cada chunk é persistido assim que fica disponível. O checkpoint
registra UUID, origem do job, consulta e chunks concluídos, permitindo retomar
também um export que ainda estava em processamento sem criar uma operação
duplicada.

Antes de abrir um novo export VM para o mesmo trabalho lógico, a aplicação consulta
o UUID preservado, inclusive quando a fila o fornece explicitamente à nova
tentativa. Essa validação ocorre antes de aguardar ou baixar chunks. Estados ativos
continuam em acompanhamento; um estado
`FINISHED` reutiliza os chunks ainda disponíveis e os já persistidos. Um novo
export só é aberto quando o anterior está terminal (`CANCELLED`, `FAILED` ou
`ERROR`), não existe mais (HTTP 404) ou terminou sem que todos os chunks restantes
continuem disponíveis. Erros de autenticação, limite ou servidor não são
convertidos silenciosamente em um novo job.

O manifesto parcial WAS também nasce antes do primeiro chunk, preservando o UUID
para a retomada já controlada pelo checkpoint. Uma falha manual individual cria
checkpoint e interrompe o fluxo antes dos DOCX para a decisão do analista. No botão
**Gerar todos** e no mensal automático, a aplicação repete apenas o WAS uma vez; se
a segunda tentativa falhar, publica sem WEB e registra `WAS_RETRY_EXHAUSTED`.
VM, assets, TAG e Cloud nunca são repetidos por essa política. Uma recuperação
posterior materializa VM/assets/TAG localmente do snapshot compacto, coleta apenas
WAS e troca os documentos VM/TAG e o manifesto em uma transação com rollback.
Cloud é preservado e não é executado novamente.

O cancelamento automático é conservador: somente um job criado pela execução atual,
sem progresso, pode ser cancelado ao atingir o limite. Job preexistente, fornecido
ou retomado nunca é cancelado automaticamente. Timeout de export é falha temporária
e elegível a retentativa.

Propriedades seletivas reduzem o payload quando previamente validadas no tenant. A
configuração é por cliente e possui fallback único para payload completo se houver
rejeição HTTP 400 ou contrato incompleto. Timeout, autenticação e rate limit não são
ocultados por esse fallback.

## Lotes duráveis e controle local

`web_batches`, `web_batch_jobs` e `web_batch_events` no PostgreSQL guardam o lote,
cada cliente e a trilha de auditoria. O dispatcher mantém uma fila sequencial: um
único cliente é reivindicado por vez com bloqueio transacional, e um reinício local
reconcilia qualquer `RUNNING` abandonado como `INTERRUPTED` e o lote como `PAUSED`.

Pausa e retomada não reclassificam resultados antigos. **Pausar após o atual** espera
o trabalho ativo; **Retomar lote** libera somente os itens ainda `QUEUED`. **Parar
lote** muda o ativo para `INTERRUPT_REQUESTED`, os pendentes para
`CANCELLED_BY_USER` e termina em `STOPPED`. O processo recebe primeiro um arquivo de
controle cooperativo. O PID é persistido; depois do prazo de tolerância, um fallback
encerra apenas a árvore local. O export remoto, seu UUID, chunks e manifesto parcial
são preservados.

`COMPLETE`, `COMPLETE_WITH_WARNINGS`, `FAILED`, `INTERRUPTED` e
`CANCELLED_BY_USER` são terminais no trabalho. O lote agrega isso em `COMPLETE`,
`COMPLETE_WITH_WARNINGS`, `COMPLETE_WITH_FAILURES` ou `STOPPED`. Ator, motivo e
chave idempotente são registrados para cada mutação.

**Tentar falhas/interrompidos** cria um lote derivado somente de `FAILED`,
`INTERRUPTED` e `CANCELLED_BY_USER`. **Gerar todos novamente** inclui a seleção
integral e exige a frase de confirmação. Nenhum dos dois altera o lote de origem;
conflitos com outro trabalho ativo do mesmo cliente retornam HTTP 409.

Um snapshot anterior à fila durável pode ser validado por
`import-web-batch-recovery --dry-run`. A aplicação mapeia `running` para
`INTERRUPTED`, cria o lote `RECOVERED` em `PAUSED` e usa o hash do arquivo como
identidade. `--apply` grava lote, trabalhos e evento na mesma transação. Reaplicar é
idempotente; erro faz rollback e nunca deixa importação parcial.

## Histórico e referência `MAIN`

Cada documento publicado possui identidade de cliente, período, tipo, execução e
tentativa. O relatório automático válido torna-se `MAIN` por padrão. O analista pode
promover outra geração do mesmo contexto, preservando rastreabilidade.

O próximo comparativo consulta a referência compatível anterior, não apenas o
arquivo mais recente da pasta. Para TAGs, categoria e valor fazem parte da
identidade; mudar a TAG evita comparações incorretas.

Excluir é uma ação explícita sobre o conjunto inteiro. Se ele for `MAIN`, a
interface exige outra referência compatível. O serviço bloqueia gerações ativas,
valida que todos os alvos pertencem à raiz `data` e usa quarentena reversível antes
da transação PostgreSQL. Somente após o commit a remoção física é finalizada.

Os pacotes ZIP são projeções temporárias dos documentos registrados, não novos
artefatos duráveis. O pacote de um conjunto usa exatamente a execução escolhida. O
pacote mensal seleciona somente o `MAIN` de cada cliente no período, separa os
arquivos por cliente e registra em `RESUMO.txt` clientes sem `MAIN`, documentos
ausentes e demais omissões. Caminhos fora da raiz `data` são rejeitados. O ZIP é
montado em `data/.downloads`, transmitido com `no-store` e removido ao final da
resposta, inclusive quando o navegador interrompe o download.

## Ciclo de vida do armazenamento

Duráveis:

- DOCX publicados, até exclusão explícita;
- fotografias Cloud compactas e compatíveis para tendência e replay;
- métricas mensais compactas e fingerprints para tendências, até a exclusão
  explícita do conjunto;
- registros de execução, publicação, documento e `MAIN` no PostgreSQL, enquanto o
  conjunto existir.

Temporários:

- respostas raw e chunks;
- respostas GraphQL, checkpoints e enriquecimentos Cloud;
- snapshots normalizados completos;
- datasets intermediários e imagens de montagem.
- pacotes ZIP de download, somente durante a resposta HTTP.

Após sucesso validado, os temporários são descartados. Em falha, permanecem por
uma janela curta, atualmente orientada a sete dias, para diagnóstico e retomada.

## Falhas e observabilidade

A execução registra eventos estruturados por cliente. Falhas são classificadas
para diferenciar credencial, contrato, limite de taxa, indisponibilidade temporária,
timeout e erro não esperado. A interface mostra progresso e alerta por cliente sem
expor chaves ou conteúdo sensível dos findings. Falha Cloud é registrada
separadamente e pode produzir sucesso parcial com retentativa exclusiva. Falha WAS
usa disponibilidade tipada: `NOT_COLLECTED` produz alerta editorial, enquanto
`NO_DATA` significa coleta concluída sem ocorrências.
