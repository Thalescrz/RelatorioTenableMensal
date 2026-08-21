# Relatórios operacionais por TAG — Especificação de design

**Data:** 2026-08-21  
**Status:** aprovado em conversa; aguardando revisão do documento consolidado  
**Projeto:** RelatorioTenableMensalv2

## 1. Objetivo

Permitir que cada cliente habilite a geração de um relatório operacional compacto para cada TAG selecionada na Tenable, sem limitar a coleta nem o relatório geral. Cada TAG também pode, de forma independente dentro do conjunto de relatórios habilitados, incluir um comparativo temporal da própria TAG.

A execução continua produzindo:

1. o relatório-base geral, sem filtro por TAG;
2. o relatório de inteligência e customizações geral;
3. zero ou mais relatórios operacionais por TAG.

O relatório por TAG não é uma cópia integral do relatório-base. Ele reúne somente o conteúdo operacional solicitado no documento de referência do cliente K e reutiliza o padrão editorial, visual e tabular já aprovado no projeto.

## 2. Decisões aprovadas

- A coleta VM principal permanece geral e nunca recebe filtro `tag.*`.
- Uma única coleta geral alimenta o relatório-base, o customizado e todos os relatórios por TAG.
- A API é consultada separadamente para descobrir os ativos pertencentes a cada TAG configurada; os recortes são feitos localmente sobre os dados normalizados da coleta geral.
- TAGs de categorias diferentes podem ser selecionadas, pois cada associação é resolvida individualmente e não por uma expressão remota combinando categorias.
- O recurso é opcional por cliente e fica desabilitado quando não houver TAG configurada para relatório.
- Cada TAG possui as opções `Gerar relatório` e `Incluir comparativo temporal`.
- `Incluir comparativo temporal` exige `Gerar relatório`; a interface não permite uma comparação sem o respectivo documento.
- O comparativo é sempre da mesma TAG em períodos diferentes. Não existe comparação entre TAGs.
- O comparativo temporal da TAG aparece dentro do respectivo relatório por TAG e não é duplicado no relatório customizado.
- As tabelas e os gráficos mensais gerais permanecem no relatório customizado.
- As tabelas e os gráficos filtrados por TAG aparecem somente no relatório daquela TAG quando o comparativo estiver habilitado.
- A série temporal da TAG cobre janeiro até o mês analisado, dentro do mesmo ano civil.
- Meses sem histórico são exibidos como indisponíveis nas tabelas e não são convertidos em zero nem conectados artificialmente nos gráficos.
- O nome do arquivo contém a categoria e o valor da TAG.
- A tabela de hosts do Top 5 usa o padrão completo do projeto, não a tabela reduzida de hostname e IP observada no documento do cliente K.

## 3. Limites do escopo

### Incluído

- descoberta de TAGs pela API a partir da interface web;
- seleção persistente das TAGs por cliente;
- seleção individual de relatório e comparativo para cada TAG;
- geração dos documentos por TAG em execuções automáticas e pontuais;
- histórico compacto por TAG no PostgreSQL;
- gráficos e tabelas anuais por TAG;
- registro, download, agrupamento e alertas dos documentos na interface;
- compatibilidade de leitura com a configuração legada denominada “rede”.

### Não incluído

- relatório WAS, Cloud Security ou cópia integral do relatório-base por TAG;
- comparação de uma TAG contra outra;
- uma exportação VM completa por TAG;
- reconstrução retroativa de métricas por TAG quando os dados brutos antigos já foram eliminados;
- armazenamento permanente de datasets pesados por TAG;
- criação de textos editoriais novos com estilo generativo.

## 4. Configuração do cliente

O perfil passa a aceitar uma configuração genérica, sem a palavra “rede”:

```json
{
  "report": {
    "tag_reports": {
      "enabled": true,
      "tags": [
        {
          "tag_uuid": "11111111-2222-3333-4444-555555555555",
          "category_uuid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
          "category_name": "Equipe",
          "value": "Infraestrutura",
          "generate_report": true,
          "include_temporal_comparison": true
        },
        {
          "tag_uuid": "66666666-7777-8888-9999-000000000000",
          "category_uuid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
          "category_name": "Equipe",
          "value": "Aplicações",
          "generate_report": true,
          "include_temporal_comparison": false
        }
      ]
    }
  }
}
```

Regras:

- `tag_uuid` é a identidade estável usada em coleta, histórico e comparação;
- categoria e valor são rótulos persistidos para apresentação e nome do arquivo;
- UUIDs repetidos são rejeitados;
- `include_temporal_comparison=true` com `generate_report=false` é inválido;
- `enabled=false` desativa todos os documentos por TAG sem apagar a seleção;
- uma TAG removida ou inacessível na Tenable continua visível na configuração como indisponível, em vez de ser apagada silenciosamente.

### Compatibilidade legada

Os campos `report.network_comparison_tags` e `vm_network_comparison` continuam legíveis durante a transição. Em memória, cada seletor legado resolvido é convertido em uma TAG com relatório e comparativo habilitados. Ao salvar o cliente novamente pela interface, a configuração é gravada no novo formato.

Novos datasets, telas, mensagens e APIs usam os termos `tag_report`, `tag_snapshot` e `tag_comparison`. Os leitores históricos aceitam os nomes legados `network_tag_snapshots` e `network_comparisons`, sem duplicar o conteúdo armazenado.

## 5. Interface web

Na edição do cliente, o campo textual “Tags para comparativo por rede” é substituído por:

- chave `Gerar relatórios por TAG`;
- botão `Buscar TAGs da Tenable`;
- data e hora da última consulta bem-sucedida na sessão;
- pesquisa por categoria ou valor;
- lista agrupada por categoria;
- colunas `TAG`, `Gerar relatório` e `Incluir comparativo temporal`.

O botão consulta a API somente quando acionado. Ele usa as credenciais locais do cliente e nunca devolve chaves ao navegador. A rota será `GET /api/clients/{client_id}/tags` e responderá com UUIDs, categoria, valor e horário da consulta.

Estados tratados na tela:

- sem credenciais: orientação para cadastrar as chaves;
- `401`/`403`: credencial ou permissão insuficiente;
- `429`: limite temporário da Tenable;
- indisponibilidade da API: erro preservado no cliente, sem apagar a seleção anterior;
- TAG configurada não retornada: item marcado como indisponível;
- lista vazia: mensagem de que não foram encontradas TAGs selecionáveis.

Durante uma execução, o progresso do cliente informa a etapa geral e, na geração segmentada, `TAG n de total`. Falhas individuais aparecem no alerta do cliente com a categoria e o valor da TAG afetada.

Na lista de relatórios, os arquivos são agrupados em `Geral`, `Customizado` e `Por TAG`. Cada documento por TAG mostra categoria, valor, período, tamanho e botão de download. A exclusão e a escolha do relatório principal continuam pertencendo à execução completa; todos os documentos dessa execução compartilham o mesmo `run_id` e a mesma referência `main`.

## 6. Coleta e recorte de dados

O fluxo de uma execução é:

1. carregar o perfil e resolver as TAGs habilitadas;
2. obter uma associação atual de ativos para cada TAG por meio da API de Workbenches;
3. realizar uma única exportação geral de ativos e uma única exportação geral de findings VM para o período;
4. normalizar os dados gerais uma única vez;
5. construir o dataset geral sem filtro por TAG;
6. cruzar os identificadores dos ativos de cada TAG com os ativos normalizados;
7. construir um dataset compacto e independente para cada TAG a partir do mesmo conjunto normalizado;
8. gerar os documentos, validar os pacotes DOCX e publicar um único manifesto da execução;
9. persistir somente documentos e histórico compacto; os datasets segmentados seguem a política de limpeza dos demais intermediários.

A associação das TAGs é registrada em um snapshot de escopo versão 2, com uma entrada por UUID. Não há união semântica entre TAGs: cada uma conserva seu próprio conjunto de ativos.

Uma TAG pode conter ativos que também pertencem a outra TAG. Isso é esperado; totais de relatórios por TAG não devem ser somados para representar o ambiente geral.

Se uma resposta de ativos atingir um limite que impeça provar completude, o relatório daquela TAG não é produzido. O sistema nunca publica um recorte parcial como se estivesse completo.

## 7. Dataset operacional por TAG

Cada recorte contém apenas dados VM e preserva a mesma definição de período, estados, severidades e população do dataset geral. O dataset inclui:

- identidade da TAG: UUID, categoria e valor;
- população de ativos pertencentes à TAG e população observada no período;
- métricas de vulnerabilidades não mitigadas, mitigadas, novas e ressurgidas, totais e por severidade;
- quantidade de vulnerabilidades exploráveis;
- Top 10 ativos vulneráveis, incluindo `Exploitable`;
- ranking das Top 5 vulnerabilidades não mitigadas;
- descrição, solução, referências e hosts das Top 5;
- dados compactos necessários ao histórico e aos gráficos anuais;
- proveniência e filtros de validação, quando essa opção estiver ativa no perfil.

Descrições e traduções já calculadas para um plugin são reutilizadas entre o relatório geral e os relatórios por TAG. O número de documentos não deve multiplicar chamadas de tradução para o mesmo conteúdo.

## 8. Estrutura do relatório operacional por TAG

O documento utiliza o mesmo template corporativo, capa, cabeçalhos, rodapés, estilos e campos de período do relatório aprovado. Os textos reaproveitados vêm do catálogo editorial existente e permanecem fiéis aos documentos de referência; não são acrescentados parágrafos com linguagem artificial.

Conteúdo:

1. capa com cliente, período, categoria e valor da TAG;
2. sumário e identificação do período;
3. `Principais Ativos Vulneráveis`, com Top 10 e colunas IP, ativo, severidades, total e `Exploitable`;
4. ranking das vulnerabilidades não mitigadas;
5. `Vulnerabilidades e suas correções e/ou contramedidas recomendadas`, com Top 5 detalhado;
6. tabela completa de hosts com Asset Name, IP, porta, protocolo e `Output` somente quando habilitado no perfil;
7. seção de comparativo temporal, somente quando habilitada para a TAG;
8. contracapa.

Quando a TAG não possui vulnerabilidades no período, o documento ainda é gerado e os blocos vazios recebem a mensagem editorial padronizada `Neste mês não foram identificadas...`, adequada ao conteúdo do bloco.

### Nome do arquivo

Formato:

```text
[CLIENTE] Relatório de Vulnerabilidades Tenable TAG <Categoria> - <Valor> <PERÍODO>.docx
```

Caracteres inválidos no Windows são removidos. Se duas combinações resultarem no mesmo nome normalizado, um fragmento curto do UUID é acrescentado para impedir colisão.

## 9. Comparativo temporal por TAG

O histórico da TAG é identificado por cliente, tenant, UUID da TAG, tipo de execução, modo do período, timezone, definição da métrica e escopo geral compatível. Apenas snapshots pertencentes a execuções `main` participam da série publicada.

Para relatórios que representam um mês civil completo, a seção apresenta janeiro até o mês do relatório no mesmo ano:

- tabela mensal nativa do Word para não mitigadas por severidade e total;
- gráfico comparativo e gráfico de volume de não mitigadas;
- tabela mensal nativa do Word para mitigadas por severidade e total;
- gráfico comparativo e gráfico de volume de mitigadas;
- tabela mensal de vulnerabilidades novas por severidade e total;
- gráfico de evolução conjunta de não mitigadas, mitigadas e novas;
- ranking de ativos da mesma TAG no período corrente e no predecessor imediato, quando ambos existirem;
- tabela de movimentação dos ativos, quando houver duas posições comparáveis.

As tabelas usam `Indisponível` para meses sem snapshot. Os gráficos mantêm lacunas e não desenham valores ausentes como zero. Para uma execução pontual que não corresponda a um mês civil completo, o documento corrente é gerado, mas a série mensal é omitida com mensagem de incompatibilidade de período.

Quando não existe predecessor ou série suficiente, o restante do relatório da TAG continua válido. A seção informa que ainda não há histórico comparável, sem criar tendências artificiais.

O relatório customizado continua exibindo suas tabelas e gráficos mensais gerais. O antigo comparativo específico de rede/TAG deixa de ser renderizado nesse documento para novas execuções.

## 10. Histórico e armazenamento

O snapshot histórico compacto passa a armazenar `tag_snapshots`, uma entrada por UUID, contendo somente:

- identificação e rótulo da TAG;
- resumo mensal total e por severidade;
- Top ativos necessários ao comparativo e à movimentação;
- hashes e versões de compatibilidade.

Dados brutos, listas integrais de findings e datasets por TAG são intermediários. Depois que todos os DOCX forem validados, o histórico for confirmado e o manifesto for registrado, eles podem ser removidos pela política de retenção já existente.

Históricos antigos que contêm apenas `network_tag_snapshots` continuam legíveis. Como não possuem todas as métricas mensais novas, eles podem fornecer somente os campos realmente armazenados; valores ausentes não são inferidos. A primeira execução com o novo formato estabelece o baseline completo da TAG.

Não é necessária uma linha de histórico independente para cada documento: o snapshot compacto da execução `main` reúne o geral e as TAGs. Alterar manualmente o `main` de uma competência também altera, de forma consistente, a fonte geral e a fonte das TAGs para comparações futuras.

## 11. Publicação e registro

O manifesto de publicação aceita metadados por documento:

- `document_kind`: `base`, `custom` ou `tag`;
- `tag_uuid`, `tag_category` e `tag_value` para documentos `tag`;
- caminho, hash, tamanho e estado de validação já existentes.

O PostgreSQL recebe campos opcionais equivalentes em `published_documents`, permitindo que a interface agrupe arquivos sem interpretar o nome. Registros antigos permanecem válidos com tipo desconhecido inferido apenas para apresentação.

Uma falha em uma TAG não invalida automaticamente o relatório-base e o customizado. O sistema:

1. não publica o documento incompleto da TAG;
2. registra um alerta estruturado com UUID, rótulo, etapa e erro;
3. continua as demais TAGs;
4. publica os documentos válidos e marca a execução como concluída com alertas.

Erros na coleta geral, normalização geral ou validação do relatório-base continuam sendo falhas da execução inteira.

## 12. Segurança e privacidade

- credenciais permanecem somente nos arquivos `.env` locais e não aparecem nas respostas da API web;
- erros apresentados no navegador passam pela sanitização já existente;
- IP, hostname, URI interna e Plugin Output obedecem às opções de mascaramento do relatório geral;
- Plugin Output permanece opcional e desligado por padrão;
- os nomes das TAGs são tratados como dados potencialmente sensíveis em logs e artefatos publicados;
- nenhuma consulta de TAG é feita para clientes diferentes daquele solicitado pela rota ou execução.

## 13. Testes e critérios de aceite

### Configuração

- perfis novos aceitam TAGs de categorias diferentes;
- UUID duplicado e comparação sem relatório são rejeitados;
- configuração legada é lida e convertida em memória;
- salvar pela interface grava o formato novo sem segredos.

### API e interface

- `Buscar TAGs` lista, agrupa e pesquisa os valores retornados;
- seleções permanecem salvas após reiniciar o servidor;
- TAG ausente é mostrada como indisponível;
- erros `401`, `403`, `429` e falha de rede produzem mensagens seguras;
- a lista de relatórios agrupa documentos gerais, customizado e por TAG.

### Coleta e dados

- somente uma exportação geral de ativos e findings é iniciada por cliente;
- cada TAG contém exclusivamente os ativos associados ao seu UUID;
- o dataset e o relatório geral permanecem idênticos com ou sem TAGs configuradas;
- sobreposição de ativos entre TAGs não mistura os respectivos recortes;
- uma TAG que excede o limite seguro não produz resultado parcial;
- traduções do mesmo plugin são reutilizadas.

### Documentos

- cada TAG habilitada gera um DOCX com nome único e seguro para Windows;
- o documento contém Top 10 de ativos, Top 5 não mitigadas e a tabela completa de hosts;
- `Output` aparece somente quando habilitado;
- ausência de dados produz a mensagem mensal, não uma página silenciosamente vazia;
- comparação desabilitada não deixa títulos ou páginas vazias;
- todos os DOCX passam pela validação de pacote e pela renderização visual de amostra.

### Histórico

- a série contém somente a mesma TAG e snapshots `main` compatíveis;
- janeiro até o mês corrente é apresentado no mesmo ano;
- mês ausente é indisponível, nunca zero;
- relatório sem predecessor ainda é produzido;
- execução pontual fora de um mês civil não entra na série mensal;
- trocar o `main` altera a fonte do comparativo geral e por TAG de modo atômico;
- snapshots legados continuam carregando sem perda dos campos existentes.

### Regressão

- relatório-base continua geral e sem filtro por TAG;
- tabelas e gráficos mensais gerais continuam no relatório customizado;
- WAS opcional continua sem bloquear VM;
- exclusão, restauração, download, seleção de `main`, retenção e orquestração multicliente continuam funcionando.

## 14. Implantação

1. aplicar a migração aditiva de metadados dos documentos;
2. implantar leitores compatíveis com configurações e snapshots legados;
3. disponibilizar a nova edição de TAGs na interface;
4. executar uma coleta de validação com um cliente de teste e duas TAGs, incluindo categorias diferentes e ativos sobrepostos;
5. renderizar e revisar o relatório-base, o customizado, um relatório com comparativo e outro sem comparativo;
6. ativar o recurso apenas nos clientes selecionados;
7. considerar a primeira competência nova como baseline completo quando o histórico legado não tiver métricas suficientes.

O recurso não altera automaticamente os perfis de clientes que não utilizam relatórios por TAG.
