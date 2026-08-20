# Catálogo preliminar das APIs Tenable

**Última revisão documental:** 2026-08-12  
**Escopo:** endpoints relevantes identificados, clientes de export VM e ativos v2 implementados com fixtures offline e contratos autenticados validados em 2026-08-12.

## Convenções de status

- **Confirmado na documentação:** método e finalidade constam na documentação oficial pública atual.
- **Validado no material local:** existe implementação e/ou documentação fornecida, mas não houve teste de contrato nesta fase.
- **Pendente no tenant:** filtros, campos, permissões ou endpoint operacional exigem teste mínimo autenticado.

## Autenticação comum VM/WAS

| Item | Contrato |
|---|---|
| Header | `X-ApiKeys: accessKey=...;secretKey=...` |
| Base global | `https://cloud.tenable.com` |
| User-Agent | `Integration/1.0 (VENDOR; PRODUCT; Build/VERSION)` |
| Segredos | Variáveis de ambiente ou secret manager; nunca em perfil de cliente |
| Rate limit | Em `429`, respeitar `Retry-After`; evitar coleta multithread |
| Erros permanentes | Não repetir cegamente `400`, `401`, `403`, `404` |

Referências: [User-Agent](https://developer.tenable.com/docs/user-agent-header) e [Rate Limiting](https://developer.tenable.com/docs/rate-limiting).

## Vulnerability Management - tags para comparativo temporal por rede

### GET `/tags/values`

Lista os valores de tags disponíveis para a credencial, com paginação por `limit` e
`offset`. O coletor usa `category_name`, `value`, UUID da categoria e UUID do valor para
montar a seleção interativa ou resolver os seletores gravados no perfil.

### GET `/workbenches/assets`

Resolve os UUIDs de ativos para cada `tag.<categoria>=valor` selecionado. A chamada é
somente de metadados; ela não cria um scan nem um export adicional. Como o endpoint é
limitado a 5.000 ativos, o coletor falha explicitamente quando o total informado excede
esse limite.

### Semântica adotada

- os exports gerais de VM e ativos não recebem filtros `tag.*`;
- os UUIDs associados à tag delimitam somente o snapshot customizado da rede;
- o relatório-base sempre usa a população geral normalizada;
- cada tag selecionada é comparada com a mesma tag em um período anterior
  compatível;
- selecionar várias tags cria comparativos temporais independentes, nunca uma
  comparação entre redes.

Referências: [listar tags](https://developer.tenable.com/reference/tags-list-tag-values),
[ativos por tag](https://developer.tenable.com/docs/list-assets-for-specific-tag-tio).

## Vulnerability Management - findings

### POST `/vulns/export`

| Campo | Registro |
|---|---|
| Produto | Vulnerability Management |
| Finalidade | Iniciar export assíncrono de findings |
| Parâmetros principais | `num_assets` 50-5000; `include_unlicensed`; `include_software_vulns`; `include_plugin_output`; `properties`; `filters` |
| Filtros já relevantes | `state`, `severity`, `since`, `last_found`, `last_fixed`, tags, `severity_modification_type`, `vpr_score` |
| Paginação | Não se aplica ao POST; resultado dividido em chunks por ativos |
| Permissão | Basic ou privilégio de export, mais Can View nos ativos |
| Observações | Sem filtro temporal, a exportação pode ficar limitada a 30 dias. Export idêntico concorrente pode retornar `409` com job ativo. A API assume `include_plugin_output=true` quando omitido; o coletor envia `false` explicitamente por padrão. `vpr_v2_score` foi descontinuado em 2026-07-01; usar `vpr_score`. |
| Status | Confirmado na documentação, coberto por fixtures offline e validado no tenant com export mínimo; filtros de escopo por cliente continuam pendentes |
| Referência | https://developer.tenable.com/reference/exports-vulns-request-export |

Os DOCX confirmaram a necessidade de identidade do finding, estado, datas, ativo, plugin, severidade, VPR, exploitabilidade, patch, descrição/solução e, opcionalmente, plugin output e software attribution. A documentação permite selecionar `properties`, mas o tenant validado falhou ao processar todos os chunks seletivos; por isso, a Fase 2 usa payload completo com `include_plugin_output=false` e preserva `properties` apenas como opção experimental.

### Semântica temporal adotada na Fase 4

O fluxo mensal não depende do default móvel da API. Ele envia `since=period_start` com `OPEN`, `REOPENED` e `FIXED` para reduzir a população. Conforme o contrato atual, `since` usa `last_found` para estados abertos/reabertos e `last_fixed` para corrigidos. Como esse filtro não estabelece `period_end`, o dataset reaplica localmente o intervalo `[period_start, period_end)`; registros posteriores ao mês são excluídos com motivo. Referências: [refinamento de requests](https://developer.tenable.com/docs/refine-vulnerability-export-requests) e [mudança do filtro `since`](https://developer.tenable.com/changelog/io-new-behavior-for-since-filter-in-vulnerability-exports).

### Evidência autenticada da Fase 2 — 2026-08-12

- Export mínimo concluído com estado `FINISHED`, 37 chunks e leitura válida do primeiro chunk.
- O payload completo do tenant retornou os objetos `asset`, `plugin`, `port` e `scan`; aliases seletivos do request usam a nomenclatura documental `definition.*` e são convertidos pela API no payload.
- Identidade confirmada: `asset.uuid + plugin.id + port.port + port.protocol`; o payload também fornece `finding_id`.
- Exploitabilidade disponível em `plugin.exploit_available` e `plugin.exploitability_ease`, além dos flags por framework.
- VPR corrente disponível em `plugin.vpr.score`; CVSS v2/v3/v4, EPSS, descrição, sinopse, solução e referências também foram observados.
- `include_plugin_output=false` foi enviado e nenhum campo `plugin_output`/`output` apareceu no schema inspecionado.
- Dois exports com `properties` (38 e 17 propriedades) foram aceitos pelo POST, mas ambos terminaram com `37/37` chunks falhos. A capacidade seletiva foi classificada como incompatível neste tenant e ficou desativada por padrão.
- O gate foi endurecido: estado superior `FINISHED` com `chunks_failed` ou `chunks_cancelled` agora é falha de coleta, nunca `NO_DATA`.
- Nenhum valor de finding ou credencial foi registrado neste catálogo; apenas nomes/tipos de campos e contagens técnicas.

### GET `/vulns/export/{export_uuid}/status`

| Campo | Registro |
|---|---|
| Finalidade | Consultar estado e chunks disponíveis |
| Regra | Chunks são processados em paralelo e podem chegar fora de ordem |
| Polling | Intervalo configurável, timeout global e sem busy-loop |
| Caso vazio | Estado concluído sem chunks deve produzir coleta completa com zero registros, não timeout |
| Status | Confirmado, coberto por fixtures offline e validado no tenant: `FINISHED`, chunks fora de ordem normalizados e conclusão vazia tratada |
| Referência | https://developer.tenable.com/reference/exports-vulns-export-status |

### GET `/vulns/export/{export_uuid}/chunks/{chunk_id}`

| Campo | Registro |
|---|---|
| Finalidade | Baixar chunk em `application/octet-stream` |
| Formato esperado | JSON/JSON Lines; atributos vazios podem ser omitidos |
| Retenção | Adotar janela conservadora de 24 horas conforme a página atual do endpoint |
| Limite | Plugin output individual limitado a 1 MB |
| VPR | Usar `plugin.vpr`, não `plugin.vpr_v2` |
| Status | Confirmado e validado no tenant; payload completo usa objetos `asset`, `plugin`, `port` e `scan` |
| Referência | https://developer.tenable.com/reference/exports-vulns-download-chunk |

### Semântica e identidade

| Item | Regra preliminar |
|---|---|
| UI New/Active | API `OPEN` |
| UI Resurfaced | API `REOPENED` |
| UI Fixed | API `FIXED` |
| Chave de finding VM | `asset.uuid + plugin.id + port.port + port.protocol` |
| Ativo | Vincular `finding.asset.uuid` ao `asset.id` do export de ativos |
| Deleted/terminated | Não inferir que findings foram corrigidos |

Referência: https://developer.tenable.com/docs/vm-and-was-integrations

## Vulnerability Management - ativos

### POST `/assets/v2/export`

| Campo | Registro |
|---|---|
| Finalidade | Iniciar export assíncrono de ativos VM e, opcionalmente, WAS |
| Parâmetros | `chunk_size` 100-10000; recomendação de operação até 5000; `include_open_ports`; `include_resource_tags`; `filters` |
| Filtros relevantes | `since`, `sources`, `types` (`host`/`webapp`), deleted/terminated conforme contrato da API |
| Observação | v2 substitui v1; não suporta filtro `tag.<category>` que existia no v1 |
| Campos candidatos | `id`, `types`, `sources`, `scan.*`, `network.*`, `timestamps.*`, `ratings.acr.score`, `ratings.aes.score` |
| Status | Confirmado, coberto por fixtures e validado no tenant; filtros específicos de escopo por cliente ainda dependem do perfil real |
| Referência | https://developer.tenable.com/reference/export-assets-v2 |

Na coleta mensal, `since=period_start` no export de ativos cria apenas uma população candidata de ativos atualizados, excluídos ou terminados desde o início. Ele não comprova sozinho que o ativo foi escaneado no mês. A classificação local exige evidência por scan ou por finding dentro do período e registra um motivo para cada inclusão/exclusão.

### Evidência autenticada da Fase 4 — 2026-08-12/13

- Request de ativos: `since` no início de julho e `types=[host]`; 2.552 registros retornados.
- Request VM: mesmo `since`, estados `OPEN`, `REOPENED`, `FIXED` e severidades `low`, `medium`, `high`, `critical`; 60.057 registros retornados.
- Gate local: 840 ativos observados; 1.712 excluídos por primeiro scan posterior, staleness ou ausência de evidência no mês.
- Gate local de findings: 14.091 incluídos; 45.966 com evento posterior a julho excluídos.
- Resultado: 7.755 não mitigados, 6.336 mitigados, 2.142 ressurgidos, 198 ativos vulneráveis e zero órfãos.
- A coleta tardia gerou aviso explícito; não foi apresentada como fotografia perfeita do fechamento.

### GET `/assets/export/{export_uuid}/status`

Status de export de ativos v1/v2. Chunks podem concluir fora de ordem.  
Referência: https://developer.tenable.com/reference/exports-assets-export-status

### GET `/assets/export/{export_uuid}/chunks/{chunk_id}`

O download retorna os registros do chunk do export v1/v2. A implementação preserva o conteúdo raw, calcula SHA-256 e normaliza somente depois da conclusão íntegra do job. A retenção operacional deve ser tratada como 24 horas.

### Evidência autenticada da Fase 3 — 2026-08-12

- Export de ativos v2 concluído em `FINISHED`, com 32 chunks e 3.173 ativos no total.
- O primeiro chunk retornou 100 registros e confirmou `id`, `types`, `sources`, `scan`, `network`, `timestamps`, `ratings` e `operating_systems`.
- O tenant usa `network.network_id` e `network.network_name`; esses aliases foram incorporados ao normalizador.
- A validação cruzada inicial reconciliou os 3.173 ativos com 4.746 findings de um chunk VM: 4.746 vínculos por UUID, zero órfãos e zero rejeições.
- A publicação completa reconciliou 148.901 findings: todos vinculados por `finding.asset.uuid == asset.id`, zero órfãos, zero rejeições, zero ativos duplicados e zero ocorrências de qualidade.
- Os artefatos normalizados e o manifesto tiveram seus hashes SHA-256 recalculados e conferidos.
- Nenhum hostname, IP, nome, e-mail, texto de vulnerabilidade ou credencial foi registrado nesta documentação.

Baixa chunks gerados por v1 ou v2; o parser deve exigir o modelo v2 quando o job foi iniciado em `/assets/v2/export`. Chunks ficam disponíveis por até 24 horas.  
Referência: https://developer.tenable.com/reference/exports-assets-download-chunk

## Vulnerability Management - plugins

### GET `/plugins/plugin`

Lista paginada de plugins. Parâmetros: `last_updated`, `size` (máximo 10000) e `page` (base 1). Mudanças de VPR não são capturadas adequadamente apenas por `last_updated`; para cache completo de VPR, a documentação orienta obter todos e filtrar pelo `updated` do objeto VPR.  
Referência: https://developer.tenable.com/reference/io-plugins-list

### GET `/plugins/plugin/{id}`

Retorna detalhes de um plugin. Campos potencialmente relevantes incluem `description`, `synopsis`, `solution`, `cve`, scores/vetores CVSS, exploitabilidade, patch, CPE, referências e `vpr`. Deve existir cache para evitar uma chamada por finding em cada execução.  
Referências: https://developer.tenable.com/reference/io-plugins-details e https://developer.tenable.com/docs/tenable-plugin-attributes

## Web App Scanning - export em volume

### POST `/was/v1/export/vulns`

| Campo | Registro |
|---|---|
| Finalidade | Iniciar export assíncrono de findings WAS |
| Parâmetros | `num_assets` 50-5000, `include_unlicensed`, `filters` |
| Filtros relevantes | `state`, `severity`, `since`, `vpr_score`; demais filtros serão definidos pelo perfil e confirmados no tenant |
| Observação | Contrato e modelo são distintos de VM; compartilhar infraestrutura HTTP, não o parser de domínio |
| Status | Implementado e validado no tenant em 2026-08-13 |
| Referência | https://developer.tenable.com/reference/was-export-findings |

### GET `/was/v1/export/vulns/{export_uuid}/status`

Consulta status e chunks disponíveis.  
Referência: https://developer.tenable.com/reference/was-export-findings-status

### GET `/was/v1/export/vulns/{export_uuid}/chunks/{chunk_id}`

Baixa chunk JSON; atributos vazios podem ser omitidos; retenção de até 24 horas.  
Referência: https://developer.tenable.com/reference/was-export-findings-download-chunk

### Evidência autenticada da Fase 8 — 2026-08-13

O teste de contrato usou `num_assets=50`, filtros temporais explícitos e somente
severidades acionáveis. O job terminou com um chunk e 3.304 registros. A inspeção
registrou apenas nomes e tipos de campos, sem imprimir URI, hostname, evidência ou
outros valores sensíveis. O payload observado contém `finding_id`, `asset`, `url`,
`plugin`, `state`, `severity`, datas, `output`, `proof`, `payload` e atributos do ponto
de injeção. O parser aceita os aliases históricos `definition.*`, mas o contrato
principal validado usa `plugin.*`.

No dataset de julho de 2026, o gate de domínio incluiu 1.151 instâncias: 952 abertas
ou ressurgidas e 199 corrigidas, distribuídas em 20 aplicações. O Top 5 padrão é
agrupado por `plugin.id` e ordenado por VPR, severidade, número de instâncias e ID.
Para instâncias abertas é usada `last_found`; para corrigidas, `last_fixed`. O
`finding_id` é a identidade preferencial; na sua ausência, o fallback determinístico
combina aplicação, plugin, URI e método HTTP.

## Web App Scanning - busca e detalhe

### POST `/was/v2/vulnerabilities/search`

Busca vulnerabilidades com `limit` até 200, `offset`, `sort` e filtros descobertos por endpoint de metadados. Adequado a busca/detalhe, não substitui o export em volume.  
Referência: https://developer.tenable.com/reference/was-v2-vulns-search

### GET `/was/v2/vulnerabilities/{vuln_id}`

Obtém detalhe de uma instância WAS. É candidato ao enriquecimento do Top 5 WEB quando o export não trouxer descrição, solução, referências, URI e evidência suficientes.  
Referência: https://developer.tenable.com/reference/was-v2-vulns-details

## Tenable Cloud Exposure / Cloud Security

### GraphQL

| Campo | Registro |
|---|---|
| Produto | Tenable Cloud Exposure / Cloud Security |
| Autenticação no material local | `Authorization: Bearer <Service Account secret>` |
| Endpoint local validado anteriormente | `https://app.tenable.com/api/graph` |
| Endpoint citado na documentação pública | Endpoint único descrito genericamente como `/graphql` |
| Paginação | `first`, `after`, `pageInfo.hasNextPage`, `pageInfo.endCursor` |
| Fontes conhecidas | `VirtualMachines`, `ContainerImages`, `Entities`, `Findings`, `VulnerabilityInstances` |
| Situação | Não implementar agora. Confirmar endpoint e schema no tenant e na documentação autenticada antes de qualquer mudança. |
| Referência | https://developer.tenable.com/docs/cloud-security-integrations |

O material local testa cursor repetido, timeout, reset de conexão, redução adaptativa de página e isolamento de fontes opcionais. Esses comportamentos devem ser extraídos para o futuro adaptador Cloud.

## Lacunas remanescentes após os DOCX

- nomes exatos e disponibilidade de propriedades no tenant;
- fonte mais confiável para autenticação/cobertura de scans;
- regras de escopo por tags, grupos, UUIDs, redes ou scans de cada perfil;
- disponibilidade de software attribution e fix versions por produto;
- definições métricas aprovadas para SLA, aging, novas, corrigidas e ressurgidas;
- fonte externa aprovada para ciclo de vida/EOL;
- contrato GraphQL Cloud autenticado para Container Images.

## Checklist de validação por endpoint

Para cada endpoint incorporado ao código, registrar:

```text
produto
nome lógico
método e path
finalidade
permissões
parâmetros e filtros usados
campos usados
paginação/chunks
retry e erros permanentes
fixture sanitizada
resultado do teste de contrato
link oficial
data da última validação
```
