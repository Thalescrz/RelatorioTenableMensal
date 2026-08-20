# Protocolo de análise dos relatórios DOCX

Este protocolo foi aplicado aos quatro relatórios Word de referência em 2026-08-12 e deve ser repetido quando novos modelos forem incorporados.

## 1. Intake

Para cada arquivo, registrar:

- identificador neutro (`Relatório A`, `Relatório B` etc.);
- cliente/versão apenas em área local restrita;
- data ou período do relatório;
- indicação de atual, legado, exceção ou template;
- presença de dados reais, sanitizados ou tabelas vazias;
- arquivos vinculados, logos e fontes necessárias;
- checksum do arquivo original.

Os originais não serão modificados.

## 2. Extração estrutural

- propriedades de página e seções;
- estilos, fontes, tamanhos, cores e espaçamentos;
- títulos e hierarquia;
- cabeçalhos, rodapés, campos, paginação e TOC;
- tabelas, dimensões, cabeçalhos, células mescladas e repetição de cabeçalho;
- imagens, gráficos, shapes e relacionamentos;
- conteúdo estático, placeholders e valores aparentes;
- quebras de página/seção e orientações diferentes.

## 3. Renderização e inspeção visual

Cada DOCX será renderizado em imagens por página. Todas as páginas serão inspecionadas para registrar:

- capa e front matter;
- geometria e densidade;
- identidade visual;
- alinhamentos e espaçamentos;
- posição de logos, cabeçalhos e rodapés;
- tratamento de títulos, tabelas e gráficos;
- páginas paisagem;
- tabelas multipágina;
- inconsistências visuais relevantes.

A análise não será concluída apenas pela leitura do XML ou extração de texto.

## 4. Ficha por relatório

| Campo | Conteúdo |
|---|---|
| Seção | Nome e nível |
| Objetivo | O que comunica |
| Elementos | Texto, tabela, gráfico, callout, imagem |
| Estático/dinâmico | Classificação e evidência |
| Indicador/campo | Nome visível |
| Unidade de contagem | Finding, ativo, plugin, CVE, scan ou outra |
| Fonte provável | VM, Assets, Plugins, WAS, configuração ou manual |
| Transformação | Filtro, agregação, cálculo e ordenação |
| Ausência | Como o documento representa dado indisponível |
| Visual | Estilo, posição e comportamento de quebra |
| Confiança | Confirmado, provável ou pendente |

## 5. Matriz comparativa

| Elemento | Cliente Y | Cliente X | Cliente Z | Cliente A | Classificação | Recomendação |
|---|---|---|---|---|---|---|
| Resultado consolidado | Analisado | Analisado | Analisado | Analisado | Ver matriz final | `docs/04-matriz-e-contrato-dos-relatorios.md` |

Classificações: `Padrão`, `Comum`, `Customização`, `Candidato a novo padrão` e `Pendente de investigação`.

## 6. Mapeamento de dados

Para cada elemento dinâmico:

```text
informação visível
-> definição de negócio
-> unidade de contagem
-> produto/módulo Tenable
-> endpoint/query
-> filtros e escopo
-> campos
-> normalização
-> cálculo/ordenação
-> regra para ausência
-> validação e reconciliação
```

Endpoint ou campo não confirmado é marcado como `PENDENTE DE INVESTIGAÇÃO`; não é criado por similaridade de nome.

## 7. Saídas do discovery dos DOCX

1. inventário completo;
2. ficha individual de cada documento;
3. matriz comparativa;
4. catálogo visual corporativo;
5. catálogo de seções e módulos;
6. catálogo de indicadores e definições;
7. mapa campo -> fonte -> transformação;
8. lista de endpoints confirmados e pendentes;
9. proposta de template padrão futuro;
10. lista de customizações que devem virar configuração.

## 8. Gate de conclusão

Gate técnico atingido para a amostra quando:

- todos os DOCX representativos tiverem extração e render;
- todas as páginas tiverem sido inspecionadas;
- cada elemento dinâmico tiver fonte confirmada ou pendência explícita;
- a matriz comparativa estiver preenchida;
- não houver dado confidencial copiado para Git;
- a decisão de padrão tiver sido registrada separadamente da recorrência observada.

A validação de negócio do contrato pelo responsável continua sendo o gate anterior à implementação dos templates finais.
