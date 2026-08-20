# Relatórios Tenable: Referência Main, Retentativas e Inteligência Customizada

**Data:** 15/08/2026  
**Status:** desenho aprovado pelo usuário  
**Projeto:** RelatorioTenableMensalv2

## 1. Objetivo

Completar a geração dos relatórios Tenable mensais e pontuais com uma referência
histórica determinística, retentativas automáticas, controle seguro de armazenamento,
indicadores customizados do período atual, mensagens controladas na ausência de dados,
nomes editoriais adequados, filtros auditáveis e gerenciamento dos relatórios pela
interface web.

O fluxo final deve preservar os textos, títulos, tabelas e parágrafos dos documentos
de referência. Mensagens operacionais novas serão limitadas às situações aprovadas de
ausência de histórico, ausência de ocorrências ou indisponibilidade de dados.

## 2. Problemas confirmados

### 2.1. Consumo de disco

A execução do TRT15 falhou com `OSError: [Errno 28] No space left on device` ao
gravar um chunk do export de vulnerabilidades. Duas tentativas recentes mantiveram
aproximadamente 7,85 GB de dados brutos do TRT15. A implementação atual grava chunks
JSONL sem compactação quando a API não os entrega comprimidos, cria dados
normalizados adicionais, preserva resíduos de execuções que falharam e somente aplica
retenção quando ela é solicitada explicitamente.

### 2.2. Histórico ambíguo

O histórico atual separa execuções `MANUAL` e `AUTOMATIC_MONTHLY`, aceita o candidato
compatível mais recente que terminou antes do período atual e pode pular uma
competência ausente. Também impede substituir de forma controlada uma referência que
tenha dados incompletos.

### 2.3. Documento customizado incompleto

O renderizador existe, mas vários produtores de dados ainda não alimentam os campos
esperados. Além disso, os gráficos mensais exigem pelo menos dois períodos mesmo
quando o snapshot atual já pode servir como baseline. Isso pode resultar em um DOCX
customizado sem conteúdo útil no primeiro mês.

## 3. Alternativas avaliadas

### 3.1. Ponteiro canônico no PostgreSQL — escolhida

Cada execução permanece imutável, enquanto uma referência explícita identifica o
relatório `main` por cliente e período compatível. A abordagem permite trocar a
referência, manter auditoria, restaurar exclusões lógicas e explicar qual snapshot
alimentou cada comparação.

### 3.2. Sobrescrever o registro mensal

Foi rejeitada porque elimina rastreabilidade e dificulta explicar alterações nos
números após uma nova execução da mesma competência.

### 3.3. Escolher automaticamente o melhor relatório

Foi rejeitada porque um algoritmo de prioridade pode mudar o alvo histórico sem uma
decisão explícita do analista.

## 4. Modelo canônico de relatórios

Cada tentativa possui `run_id`, documentos, dataset, status, origem e metadados
próprios. Uma referência canônica associa o `main` a:

- cliente;
- tenant;
- competência ou intervalo exato;
- modo do período;
- escopo geral;
- versão das definições métricas.

Somente uma execução concluída, estruturalmente válida e não excluída pode ser `main`
para cada combinação. A restrição será garantida pelo PostgreSQL e pela transação de
promoção.

O primeiro relatório mensal automático concluído com sucesso será promovido
automaticamente. Novas execuções da mesma competência serão candidatas e não
substituirão silenciosamente o `main`. O analista poderá promover outro relatório,
informando um motivo. A auditoria registrará usuário, instante, referência anterior,
nova referência e motivo.

Uma execução manual que cubra exatamente um mês-calendário pode ser promovida como
referência da competência automática correspondente. Períodos móveis, parciais ou
personalizados somente podem referenciar intervalos com a mesma modalidade e os
mesmos limites; eles não representam uma competência mensal automática.

## 5. Regra de comparação

Um relatório mensal consulta exclusivamente o `main` da competência imediatamente
anterior. A ausência dessa referência produz a mensagem de que não existe histórico
comparável; um mês mais antigo não substitui a lacuna.

Cada documento gerado registra o `run_id` e o snapshot usados na comparação. Uma
troca posterior de `main` influencia somente novas gerações. Documentos anteriores
não são recalculados automaticamente.

O comparativo de rede mantém a regra `mesma tag atual × mesma tag anterior`. Tags
selecionadas não filtram métricas gerais, Top 5 ou rankings do relatório-base. Se a
tag atual não existir no `main` anterior, o relatório mostra somente a tabela atual
como baseline.

## 6. Exclusão lógica e restauração

A exclusão comum na interface é lógica e recuperável. O relatório deixa a lista
normal e deixa de ser elegível para comparações, mas arquivos, snapshot e auditoria
permanecem até a política física de retenção.

Para excluir um `main`, o usuário precisa selecionar um substituto válido ou confirmar
explicitamente que deseja deixar a competência sem referência. Restaurar um relatório
o devolve à lista, mas não o promove automaticamente.

As ações de exclusão, restauração e promoção são idempotentes e auditáveis.

## 7. Agendamento e retentativas

Cada geração mensal agendada é um trabalho lógico com, por padrão, duas tentativas:
a original e uma retentativa automática após 15 minutos. O intervalo e o limite são
configuráveis.

Cada tentativa tem identificador e registro de erro próprios, mas permanece na mesma
cadeia `AUTOMATIC_MONTHLY`. `AUTOMATIC_RETRY` identifica a origem da tentativa e não
cria uma série histórica separada.

Erros transitórios, como timeout, HTTP 429, HTTP 5xx, falha temporária de export ou de
conexão, são reenfileirados. Erros permanentes, como credencial inválida, perfil
inválido ou pacote DOCX estruturalmente incorreto, falham com alerta sem repetição
inútil. Pouco espaço deixa o trabalho aguardando a próxima tentativa sem iniciar um
novo download completo.

A retentativa pode reutilizar chunks completos, íntegros e compatíveis. Arquivos
parciais ou sem hash válido nunca são reutilizados. Somente uma tentativa concluída
pode ser promovida a `main`.

Na interface, `Gerar relatório` cria uma execução manual. `Tentar novamente` mantém a
natureza da execução que falhou. `Reexecutar competência mensal` preserva a cadeia
automática mesmo quando acionado pelo analista.

## 8. Espaço, compactação e retenção

Antes da coleta, o sistema calcula uma reserva mínima de espaço. Para clientes com
histórico, a estimativa usa o tamanho da última execução bem-sucedida multiplicado por
1,5, mais a margem operacional. Para clientes sem histórico, a reserva inicial padrão
é 10 GB. Os valores são configuráveis.

Durante a coleta:

- chunks recebidos sem compactação são armazenados em `.jsonl.gz`;
- o conteúdo é processado em fluxo, sem manter todos os chunks simultaneamente na
  memória;
- tamanho, hash e status de cada chunk são registrados;
- o espaço livre é reavaliado antes de cada novo chunk;
- arquivos em gravação usam extensão temporária e são promovidos somente depois da
  validação;
- chunks válidos podem ser reaproveitados por uma retentativa compatível.

A retenção padrão é:

- fragmentos de execuções que falharam: 7 dias;
- dados brutos de execuções bem-sucedidas: 60 dias;
- dados normalizados: 90 dias;
- datasets, documentos e manifestos: 395 dias;
- histórico agregado, referências `main` e auditoria: mantidos até ação
  administrativa explícita.

A política é aplicada automaticamente e registra cada remoção. Nenhum alvo é removido
se estiver fora de uma pasta reconhecida, pertencer a uma execução ativa, for
necessário para retentativa, estiver protegido como `main` ou não tiver o histórico
agregado confirmado no PostgreSQL.

A interface mostra espaço livre, estimativa da fila, consumo por cliente e execução,
avisos de espaço insuficiente e uma ação para limpar somente resíduos seguros.

## 9. Produtores de inteligência do período atual

Uma nova etapa será executada entre a normalização e o histórico:

`dados normalizados → inteligência atual → histórico/main → documentos`

Ela implementa os produtores das lacunas já identificadas:

- `scan_auth_health` para saúde das varreduras autenticadas;
- `plugin_family` para agrupamento por família de plugin;
- `eol_assets` e `eol_software` para sistemas e softwares sem suporte;
- `attack_vectors` para vetores de ataque e explorabilidade;
- `was_unsupported_tech` para tecnologias WEB sem suporte;
- evolução mensal e executiva;
- volume atual;
- tabelas atuais das tags selecionadas.

Os critérios serão explícitos, versionados e rastreáveis. O sistema não classifica fim
de vida ou falha de autenticação sem evidência suficiente. Os catálogos de plugins e
regras de classificação possuem versão registrada no dataset.

## 10. Primeiro mês e ausência de dados

Sem `main` no período anterior, tabelas do período atual são geradas normalmente.
Gráficos compatíveis com um único período usam uma coluna ou ponto único. A tabela de
tag atual é apresentada como baseline. Deltas, movimentações e percentuais
comparativos não são calculados.

O relatório informa: `Não há histórico do período imediatamente anterior para
comparação.` O snapshot atual permanece elegível para tornar-se `main` e alimentar o
próximo período.

Quando a coleta é válida, mas não existem ocorrências, o título e o texto editorial
permanecem e uma mensagem específica informa, por exemplo: `Neste mês não foram
identificadas tecnologias WEB sem suporte.`

Quando a fonte necessária não foi coletada, o documento informa `Dados indisponíveis
para este indicador`, o manifesto registra a causa e os demais módulos continuam.

## 11. Nomes dos documentos

Para competência mensal:

- `[CLIENTE] Relatório de Vulnerabilidades Tenable JUL26.docx`;
- `[CLIENTE] Inteligência e Customizações Tenable JUL26.docx`.

Para meses completos consecutivos:

- `[CLIENTE] Relatório de Vulnerabilidades Tenable JUL-AGO26.docx`.

Para intervalo personalizado:

- `[CLIENTE] Relatório de Vulnerabilidades Tenable 15JUL26-14AGO26.docx`.

Caracteres inválidos do Windows são removidos somente do nome do arquivo. O nome
oficial do cliente continua no conteúdo do documento.

## 12. Filtros de origem no Word

O perfil recebe a opção `presentation.show_source_filters`, também editável na
interface. Quando ativada, cada tabela proveniente da coleta recebe abaixo uma linha
discreta com o filtro efetivamente usado e as regras posteriores relevantes.

O texto inclui, quando aplicável:

- produto lógico VM, WAS ou Cloud;
- intervalo temporal;
- estados;
- severidades;
- tag somente na tabela de rede;
- critério de mitigada, não mitigada, nova ou explorável;
- limite do ranking;
- exclusão da severidade informativa;
- versão do catálogo EOL ou de tecnologias sem suporte.

Credenciais, tokens e dados sensíveis desnecessários nunca são exibidos. O dataset e o
manifesto também guardam a representação estruturada e sanitizada do filtro.

## 13. Interface de gerenciamento

A página de cada cliente permite filtrar por competência, status e origem e mostra:

- documento-base e documento customizado;
- período;
- origem automática, retentativa ou manual;
- indicador `MAIN`;
- referência histórica usada;
- tamanho em disco;
- alertas e módulos omitidos;
- ações para abrir, baixar, promover, excluir e restaurar.

## 14. Consistência transacional

A promoção do `main` ocorre em uma transação que desmarca a referência anterior e
promove a nova. Uma restrição no PostgreSQL impede dois `main` ativos para a mesma
combinação. Falhas posteriores não deixam referências parciais.

Somente relatórios com dataset, documentos obrigatórios e pacotes DOCX válidos são
elegíveis. Cada comparação registra a referência usada, preservando a explicação dos
números mesmo após uma troca futura.

## 15. Migração dos registros existentes

- Competência com um único relatório válido: promoção automática.
- Competência com vários relatórios e uma referência já usada pelo histórico:
  preservação dessa referência.
- Competência ambígua com vários candidatos válidos: alerta `Seleção de main
  necessária`.
- Relatórios existentes não são reescritos.
- Nenhum arquivo é removido durante a migração.

## 16. Validação

A implementação deve conter testes para:

- seleção exclusiva do `main` imediatamente anterior;
- proibição de pular competência ausente;
- promoção de execução manual que represente mês completo;
- rejeição de período parcial como referência mensal;
- retentativa preservando a cadeia automática;
- concorrência na troca do `main`;
- exclusão lógica, restauração e proteção da referência ativa;
- verificação de espaço antes e durante a coleta;
- compactação e reaproveitamento de chunks;
- limpeza segura de resíduos;
- todos os produtores de inteligência;
- primeiro mês com dados atuais e sem delta;
- mensagens de nenhuma ocorrência e dados indisponíveis;
- filtros no Word sem segredos;
- nomes mensais e personalizados;
- endpoints e ações da interface;
- abertura estrutural dos dois DOCX;
- renderização visual com LibreOffice.

Os testes de desenvolvimento usam fixtures offline. Chamadas reais à Tenable são
restritas a uma validação controlada para evitar iniciar exports acidentalmente.

## 17. Critérios de aceitação

Um cliente sem histórico deve conseguir:

1. Gerar os dois documentos sem deixar o customizado vazio.
2. Mostrar os indicadores disponíveis do período atual.
3. Registrar um relatório `main`.
4. Gerar o período seguinte comparando exatamente com o `main` imediatamente
   anterior.
5. Trocar a referência pela interface com auditoria.
6. Excluir e restaurar relatórios logicamente.
7. Sobreviver a uma falha automática com uma retentativa.
8. Operar com retenção e compactação sem crescimento descontrolado do disco.

