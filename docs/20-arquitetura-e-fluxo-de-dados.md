# Arquitetura e fluxo de dados

## Visão por camadas

```text
Tenable VM / WAS
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
- `src/tenable_reports/infrastructure`: Tenable VM, Tenable WAS, JSONL, PostgreSQL e
  migrations.
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
6. Ativos e findings são normalizados. O vínculo válido é o UUID de ativo.
7. A janela temporal é aplicada localmente ao campo correto de cada estado.
8. Um dataset mensal reconciliado alimenta os relatórios gerais.
9. Para cada TAG habilitada, os UUIDs associados recortam localmente o mesmo dataset
   VM e formam um dataset de relatório por TAG.
10. O histórico compatível do cliente ou da própria TAG é recuperado do PostgreSQL.
11. Os DOCX são renderizados, validados, registrados e oferecidos para download.
12. Métricas compactas são persistidas; dados intermediários pesados de uma
   execução bem-sucedida são removidos.

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

Cada chunk é persistido assim que fica disponível. O manifesto parcial registra
UUID, origem do job, chunks concluídos e progresso. Uma nova tentativa do mesmo
trabalho pode reutilizar chunks íntegros sem baixá-los novamente.

O cancelamento automático é conservador: somente um job criado pela execução atual,
sem progresso, pode ser cancelado ao atingir o limite. Job preexistente, fornecido
ou retomado nunca é cancelado automaticamente. Timeout de export é falha temporária
e elegível a retentativa.

Propriedades seletivas reduzem o payload quando previamente validadas no tenant. A
configuração é por cliente e possui fallback único para payload completo se houver
rejeição HTTP 400 ou contrato incompleto. Timeout, autenticação e rate limit não são
ocultados por esse fallback.

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

## Ciclo de vida do armazenamento

Duráveis:

- DOCX publicados, até exclusão explícita;
- métricas mensais compactas e fingerprints para tendências, até a exclusão
  explícita do conjunto;
- registros de execução, publicação, documento e `MAIN` no PostgreSQL, enquanto o
  conjunto existir.

Temporários:

- respostas raw e chunks;
- snapshots normalizados completos;
- datasets intermediários e imagens de montagem.

Após sucesso validado, os temporários são descartados. Em falha, permanecem por
uma janela curta, atualmente orientada a sete dias, para diagnóstico e retomada.

## Falhas e observabilidade

A execução registra eventos estruturados por cliente. Falhas são classificadas
para diferenciar credencial, contrato, limite de taxa, indisponibilidade temporária,
timeout e erro não esperado. A interface mostra progresso e alerta por cliente sem
expor chaves ou conteúdo sensível dos findings.
