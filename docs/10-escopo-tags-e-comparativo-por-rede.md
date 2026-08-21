# Relatórios operacionais e comparativo temporal por TAG

## Conceito

“Rede” não é um escopo técnico separado no gerador. É apenas um possível nome de
categoria ou valor de TAG na Tenable. O mesmo fluxo funciona para qualquer categoria,
como `Equipe`, `Local`, `Departamento` ou `Rede`, e cada valor é identificado pelo
UUID estável da TAG.

O relatório geral nunca é filtrado pelas TAGs. Uma única coleta VM traz todos os
ativos e findings elegíveis no período; depois da normalização, o gerador cruza os
UUIDs de ativos de cada TAG e cria recortes efêmeros independentes. Assim, habilitar,
desabilitar ou trocar uma TAG não altera métricas, rankings, textos ou Top 5 do
relatório-base e do relatório customizado geral.

## Documentos produzidos

Além dos documentos geral e customizado, o cliente pode habilitar um relatório
operacional compacto para cada TAG selecionada. Cada documento por TAG contém:

- identificação da categoria e do valor na capa e no nome do arquivo;
- principais ativos vulneráveis, com `Exploitable` na última coluna;
- Top 5 de vulnerabilidades VM não mitigadas;
- detalhamento de cada vulnerabilidade com a tabela de hosts padrão;
- tabelas e gráficos mensais da própria TAG, quando o comparativo estiver habilitado.

O relatório por TAG contém somente VM. Ele não repete WAS nem Cloud Security. O nome
segue o padrão:

`[Cliente] Relatório de Vulnerabilidades Tenable TAG Categoria - Valor JUL26.docx`

## Seleção pela interface

No cadastro do cliente, a área **Relatórios por TAG** possui uma chave de ativação e
o botão **Buscar TAGs da Tenable**. A consulta usa a credencial local do cliente e
agrupa o resultado por categoria; chaves de API nunca retornam ao navegador.

Cada TAG possui duas decisões independentes:

1. **Gerar relatório**: cria o documento operacional daquele recorte.
2. **Comparativo temporal**: inclui, dentro desse mesmo documento, as tabelas e os
   gráficos históricos da TAG.

O comparativo só pode ser marcado quando o relatório estiver marcado. É possível
gerar documentos para várias TAGs e habilitar o comparativo em apenas uma delas.
Uma TAG salva que deixe de aparecer na API continua visível como indisponível, para
que o analista decida se deve removê-la.

## Fluxo da coleta única

1. `GET /tags/values` lista os valores de TAG visíveis para a credencial.
2. `GET /workbenches/assets` resolve separadamente os UUIDs de ativos de cada TAG.
3. `POST /vulns/export` coleta a população geral do período, sem filtro `tag.*`.
4. `POST /assets/v2/export` coleta a população geral de ativos, também sem filtro.
5. A normalização e os datasets geral/customizado usam a população integral.
6. O cruzamento por UUID cria um dataset efêmero para cada TAG selecionada.
7. Os DOCX geral e customizado são gerados sem depender dos recortes.
8. Cada DOCX por TAG é gerado isoladamente; uma falha específica vira alerta e não
   invalida os documentos gerais nem as outras TAGs.

As TAGs nunca são combinadas em uma união semântica. Dois valores selecionados
produzem dois recortes e dois documentos distintos.

## Comparação temporal

O comparativo é sempre da mesma TAG em momentos diferentes: `TAG A atual × TAG A
anterior`. Nunca é `TAG A × TAG B`. A referência precisa ser uma execução `MAIN`
compatível do mesmo cliente, tenant, UUID de TAG, definição métrica, regra de período,
timezone, severidades e estados.

No primeiro mês, ou quando não existe predecessor compatível, o relatório corrente
continua sendo gerado. A área temporal informa a indisponibilidade de histórico; não
usa zero como substituto, não escolhe um arquivo arbitrário e não mistura períodos
automáticos com períodos pontuais incompatíveis.

As séries anuais vão de janeiro ao mês analisado no mesmo ano civil. Lacunas aparecem
como indisponíveis e não são plotadas como zero.

## Dados retidos e limpeza

Durante a geração existem datasets segmentados em `report-datasets/.../tags`. Eles
são temporários e entram na mesma limpeza segura dos dados pesados gerais somente
depois que os DOCX foram validados, o manifesto foi registrado e o histórico foi
confirmado.

Após uma publicação bem-sucedida, permanecem apenas:

- os DOCX publicados;
- o manifesto e os metadados de publicação;
- o resumo histórico compacto necessário para comparações futuras, indexado pelo
  UUID da TAG.

Raw, snapshots, normalizados e datasets segmentados elegíveis são descartados. Uma
falha preserva o staging pelo prazo configurado para diagnóstico.

## Compatibilidade legada

Os parâmetros `--select-tags`, `--tag` e `report.network_comparison_tags` continuam
aceitos para perfis antigos. Eles são entradas legadas somente para leitura; novos
clientes devem usar `report.tag_reports`, preferencialmente pela interface web.
`scope.vm.tags` não é aceito, pois indicaria incorretamente um filtro global.

## Guardas e limitação da API

- filtros passados ao fluxo mensal não podem conter `tag.*`;
- o manifesto declara `general_collection_filtered_by_tags=false`;
- cada recorte usa o UUID da TAG, nunca somente o texto exibido;
- falha em uma TAG é isolada e registrada com etapa, UUID e mensagem;
- a listagem de ativos do Workbench é limitada a 5.000 registros; uma TAG que exceda
  esse limite falha explicitamente, sem publicar um recorte incompleto.

Referências oficiais:

- https://developer.tenable.com/reference/tags-list-tag-values
- https://developer.tenable.com/docs/list-assets-for-specific-tag-tio
- https://developer.tenable.com/reference/export-assets-v2
