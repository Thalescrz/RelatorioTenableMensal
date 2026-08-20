# Tags e comparativo temporal por rede

## Objetivo

A seleção de tags existe exclusivamente para produzir o comparativo customizado dos
principais ativos vulneráveis de uma rede. Ela nunca restringe a população do
relatório-base: todos os ativos e findings gerais elegíveis no período continuam
participando das métricas, rankings e Top 5 padrão.

O comparativo também não coloca uma rede contra outra. Cada bloco compara a mesma
tag/rede em dois momentos compatíveis, normalmente o mês atual contra o mês anterior.

## Fluxo

1. `GET /tags/values` lista os valores de tag visíveis para a credencial.
2. No modo interativo, o terminal solicita uma categoria e um ou mais valores.
3. `GET /workbenches/assets` resolve os UUIDs associados a cada tag selecionada.
4. `POST /vulns/export` coleta a população geral do período, sem filtros `tag.*`.
5. `POST /assets/v2/export` coleta a população geral de ativos, também sem ser
   reduzida à união das tags.
6. A normalização e o dataset-base usam integralmente essas populações gerais.
7. Em paralelo, os UUIDs resolvidos em 3 delimitam somente um
   `network_tag_snapshot` corrente para cada tag.
8. A camada histórica procura o predecessor da mesma tag, cliente, tenant, regra de
   período e definição métrica.
9. Havendo predecessor compatível, o segundo DOCX recebe um
   `network_comparison` com dois períodos da mesma rede. Sem predecessor, o comparativo
   é omitido; nunca se compara contra zero nem contra outra rede.

Selecionar mais de uma tag significa solicitar vários comparativos independentes:
`Rede A atual × Rede A anterior`, depois `Rede B atual × Rede B anterior`. Não significa
`Rede A × Rede B` e não altera os resultados gerais.

## Modos de seleção

- Manual interativo: `--select-tags`.
- Manual ou automático não interativo: repetir `--tag` com UUID ou
  `Categoria: Valor`.
- Agendamento por cliente: preencher `report.network_comparison_tags` no perfil.

`--select-tags` não pode ser combinado com `--tag` nem com seletores já configurados
no perfil. `scope.vm.tags` não é aceito para essa finalidade, pois o nome indicaria
indevidamente um filtro global.

## Guardas de escopo

- arquivos passados por `--finding-filters` ou `--asset-filters` não podem conter
  `tag.*` no fluxo do relatório mensal;
- o manifesto declara `general_collection_filtered_by_tags=false`;
- o dataset guarda os recortes correntes como `network_tag_snapshots`, separados das
  métricas gerais;
- somente a montagem histórica cria `network_comparisons` publicáveis;
- a identidade da rede é o UUID estável da tag, e não apenas seu texto de exibição.

## Limitação conhecida

O export de ativos v2 não aceita `tag.<categoria>`. Por isso, a associação entre tag
e ativos é resolvida pelo Workbench, mas usada somente no recorte customizado. A
listagem do Workbench é limitada a 5.000 registros; se uma tag exceder esse limite,
o comparativo dessa tag deve falhar explicitamente em vez de publicar um recorte
incompleto. Essa falha não autoriza reduzir silenciosamente o relatório geral.

Referências oficiais:

- https://developer.tenable.com/reference/tags-list-tag-values
- https://developer.tenable.com/docs/list-assets-for-specific-tag-tio
- https://developer.tenable.com/reference/export-assets-v2
