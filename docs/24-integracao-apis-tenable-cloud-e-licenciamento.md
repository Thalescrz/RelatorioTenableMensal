# Integração Tenable Cloud e licenciamento

**Estado:** referência técnica vigente  
**Verificado em:** 2026-10-08  
**Conector Cloud:** `cloud-graphql-v1`

Este guia consolida o contrato das integrações Cloud e das fontes de
licenciamento relevantes para este projeto. Ele descreve apenas capacidades
comprovadas pelo código, pelos testes locais ou pela documentação oficial da
Tenable. Não é uma cópia integral do schema da Tenable, que pode variar por
produto, tenant, licença e permissão.

O código e os testes continuam sendo a fonte executável. Quando a plataforma
autenticada divergir deste texto, interrompa a operação, preserve a evidência
sanitizada e reconcilie primeiro código, testes e documentação.

## Escopos e credenciais separados

O projeto usa duas famílias de API Tenable que não devem ser confundidas:

| Família | Uso no projeto | Autenticação | Base |
|---|---|---|---|
| Tenable Vulnerability Management e WAS | ativos, findings, plugins e exports | `X-ApiKeys` com access key e secret key | `https://cloud.tenable.com` ou base autorizada do perfil |
| Tenable Cloud Exposure / Cloud Security | fotografia Cloud do relatório padrão | `Authorization: Bearer` com secret de Service Account | endpoint GraphQL compatível com o ambiente |

As credenciais ficam somente em arquivos locais ignorados pelo Git. O token
Cloud usa `TCS_API_SECRET`; o alias legado `TCS_API_KEY` é aceito apenas para
compatibilidade. Chaves, tokens, cookies, códigos de ativação e payloads reais não
podem aparecer em documentação, logs, testes, commits ou mensagens.

## Endpoints GraphQL Cloud

O ambiente do perfil aceita `global` ou `us_gov`. O conector testa os candidatos
na ordem abaixo:

| Ambiente | Primeiro candidato | Compatibilidade |
|---|---|---|
| `global` | `https://app.tenable.com/graphql` | `https://app.tenable.com/api/graph` |
| `us_gov` | `https://app.tenable.us/graphql` | `https://app.tenable.us/api/graph` |

Uma rejeição de contrato em fonte obrigatória permite tentar o candidato seguinte.
Falha de autenticação, limite de taxa ou indisponibilidade temporária não autoriza
trocar de rota silenciosamente, pois a troca não corrige essas causas com
segurança.

Referência pública: [Cloud Security integrations](https://developer.tenable.com/docs/cloud-security-integrations).
O schema efetivo deve ser sondado no tenant porque campos opcionais podem não
existir ou não estar autorizados.

## Fontes GraphQL coletadas

As consultas versionadas ficam em
`src/tenable_reports/infrastructure/tenable_cloud/queries.py`. O probe solicita
uma única linha de cada fonte e valida `nodes` e `pageInfo` antes da coleta.

| Fonte local | Raiz GraphQL | Obrigatória | Finalidade | Página inicial |
|---|---|---:|---|---:|
| `virtual_machines` | `VirtualMachines` | sim | VMs, software, CVE, severidade, CVSS e VPR | 50 |
| `container_images` | `ContainerImages` | sim | imagens, repositório, digest, software e vulnerabilidades | 50 |
| `virtual_machine_fix_versions` | `VirtualMachines` | não | `FixedBy` por software e vulnerabilidade em VM | 50 |
| `container_image_fix_versions` | `ContainerImages` | não | `FixedBy` por software e vulnerabilidade em imagem | 50 |
| `compute_ips` | `Entities` | não | endereços de recursos computacionais suportados | 100 |
| `inventory` | `Entities` | não | inventário, provedor, região, conta, tags e sincronização | 100 |
| `findings` | `Findings` | não | postura, política, severidade, estado e recursos | 100 |
| `vulnerability_lifecycle` | `VulnerabilityInstances` | não | primeira observação, resolução e tempo de remediação | 50 |
| `vulnerability_details` | `VulnerabilityInstances` | não | descrição de candidatas críticas ao detalhamento | 20 |
| `vulnerability_remediations` | `Findings` | não | passos de remediação correlacionáveis por recurso e CVE | 50 |

`virtual_machines` e `container_images` são a base mínima do relatório Cloud. A
falha de qualquer uma encerra somente o componente Cloud; VM e WAS continuam
independentes. Fontes opcionais falhas recebem `UNAVAILABLE`, geram aviso e não
transformam ausência de cobertura em zero.

A consulta padrão de `vulnerability_remediations` usa os tipos de finding de VM.
Tipos adicionais observados em um tenant, inclusive tipos de container, não entram
automaticamente no contrato. Antes de incorporá-los é necessário validar schema,
permissão, cardinalidade e correlação em fixtures sanitizadas.

## Paginação, retry e proteção de contrato

Todas as fontes usam paginação por cursor:

- `first` controla o tamanho da página;
- `after` recebe o cursor anterior;
- `pageInfo.hasNextPage` informa continuidade;
- `pageInfo.endCursor` precisa existir e avançar;
- cursor repetido ou ausente encerra a coleta como erro de contrato.

O cliente usa timeout padrão de 180 segundos por requisição e quatro retries, o
que permite no máximo cinco tentativas por chamada. Erros de transporte e HTTP
`429`, `500`, `502`, `503` e `504` usam backoff exponencial de 1, 2, 4, 8 segundos,
limitado a 60 segundos. Quando `Retry-After` é válido, ele prevalece até o teto de
300 segundos. Códigos GraphQL de rate limit e falha temporária seguem a mesma
política.

HTTP `401`/`403` e códigos GraphQL de autenticação são falhas terminais de
credencial ou permissão. Consulta rejeitada por complexidade pode reduzir a página
pela metade até o mínimo de cinco registros. Outros erros de schema ou contrato
não são mascarados por retry.

## Persistência e retomada Cloud

Cada fonte mantém um checkpoint próprio no staging. O checkpoint registra:

- cliente, tenant, `run_id`, fonte e endpoint;
- versão da consulta e raiz GraphQL;
- estado `PROCESSING` ou `COMPLETE`;
- cursor, páginas e registros concluídos;
- páginas locais e SHA-256 de cada página;
- horário da fotografia;
- SHA-256 do JSONL consolidado quando completo.

Cada página é gravada atomicamente antes do avanço do cursor. Em reinício, a
retomada valida identidade e hashes e continua do último cursor confirmado. Um
checkpoint incompatível ou corrompido não é reutilizado; a substituição é
registrada antes de começar. Ao finalizar, o manifesto Cloud contém estado e
proveniência de todas as fontes, e páginas temporárias completas podem ser
recicladas sem perder o JSONL consolidado.

Uma fotografia Cloud representa o estado no instante da coleta. Informar uma
competência passada não reconstrói retroativamente aquele fechamento. Evolução ou
replay histórico exige snapshot Cloud compatível já preservado.

## Correções, `FixedBy` e containers

`FixedBy` é uma fonte opcional e permanece evidência interna. O DOCX não apresenta
essa propriedade em coluna. Um item corrigível só é aceito quando existe:

1. `FixedBy` estruturado para a combinação de recurso, CVE e software; ou
2. remediação correlacionada exatamente ao mesmo `resource_id + CVE`.

A correlação precisa resolver um único recurso do mesmo tipo. Nome, IP,
repositório, digest ou semelhança textual não substituem o identificador. Colisão
entre VM e imagem de container é rejeitada.

Os rankings registram cobertura `COMPLETE`, `PARTIAL` ou `UNAVAILABLE`. Fonte
indisponível significa que o tenant não forneceu evidência suficiente; não prova
que a organização possui zero vulnerabilidades corrigíveis.

## Fontes oficiais de licenciamento

### Container comum Tenable

O endpoint público abaixo expõe propriedades do container:

```text
GET https://cloud.tenable.com/server/properties
```

Segundo a especificação oficial, requer perfil Basic e pode retornar `license`
com limites gerais e um mapa `apps` por código de aplicação. Códigos observados no
contrato incluem `tio`, `was`, `apa`, `cns`, `cnapp`, `one`, `lumin`, `asm` e
outros. Cada aplicação pode expor, conforme o produto, campos como:

- `enabled`, `expired`, `expiration_date` e `mode`;
- `assets`, `agents`, `users`, `web_assets` e `resources`;
- `ratio`, `asset_limit`, `max_active_user` e `concurrent_scan_limit`;
- cotas específicas do produto.

Referências oficiais:

- [OpenAPI Tenable Platform & Settings](https://developer.tenable.com/openapi/tenable-platform-settings.json)
- [API Explorer Tenable](https://developer.tenable.com/reference/navigate)
- [Roles e privilégios](https://developer.tenable.com/docs/roles)

O mesmo payload pode conter `activation_code` e identificadores internos. Uma
implementação futura deve usar seleção positiva de campos, descartar esses valores
antes da persistência e nunca registrar a resposta bruta.

### Container parceiro MSSP

Somente um container parceiro MSSP pode usar os endpoints MSSP para inspecionar
contas-filhas. As rotas documentadas incluem:

```text
GET https://cloud.tenable.com/mssp/dashboard?widget=customer_licensing_data
GET https://cloud.tenable.com/mssp/accounts/{account_uuid}
```

Elas podem retornar `licensed_assets`, `licensed_assets_limit`,
`license_utilization`, `licensed_apps` e expiração. Esses valores são diretos para
o contexto MSSP, mas não devem ser presumidos disponíveis nas chaves de um tenant
cliente comum.

Referências oficiais:

- [Get widget details](https://developer.tenable.com/reference/io-mssp-dashboard-details)
- [Get child account details](https://developer.tenable.com/reference/io-mssp-accounts-details)

## Matriz de uso de licença

Todo indicador futuro deve declarar uma das classificações abaixo:

| Estado | Significado |
|---|---|
| `DIRECT` | valor retornado por endpoint público com semântica de uso explícita |
| `DERIVED` | cálculo local baseado em atributos oficiais, com regra e horário registrados |
| `CONSOLE_ONLY` | visível na console, mas sem paridade pública confirmada |
| `UNAVAILABLE` | credencial, produto, campo ou fonte não permitiu confirmação |

| Produto | Limite/entitlement | Uso atual | Regra segura atual |
|---|---|---|---|
| VM | `/server/properties`, aplicação `tio` e limites gerais | derivável pelos ativos licenciados | contar UUIDs de ativos cuja evidência oficial de licenciamento continua válida; comparar com a console antes de publicar como total oficial |
| WAS | `/server/properties`, aplicação `was`, inclusive `web_assets` quando presente | não há contador público universal confirmado no projeto | derivação por aplicação web licenciada exige validação contra a console e não pode contar scans ou findings como licenças |
| Attack Path | presença e metadados da aplicação `apa` | não confirmado por endpoint público específico | não usar quantidade de paths, findings ou vetores como consumo de licença |
| Cloud Exposure/CNAPP | aplicações `cns`/`cnapp` quando retornadas | detalhamento por recurso ainda não confirmado em API pública do projeto | inventário GraphQL do relatório Cloud não equivale automaticamente ao contador de licença |
| Tenable One | aplicação `one`, alocação e `ratio` quando retornados | depende das regras e proporções do contrato | não somar contagens brutas de produtos diferentes |
| MSSP | endpoints MSSP retornam limite e uso agregado | `DIRECT` no container parceiro | não usar em tenant comum nem misturar contas-filhas |

Para VM, a documentação da Tenable considera licenciado o ativo com resultado de
plugin não discovery dentro da janela aplicável, normalmente 90 dias. O Asset
Export fornece `last_licensed_scan_date`, mas a plataforma também aplica identidade,
deduplicação, age-out, produtos e proporções. Por isso uma soma local é
`DERIVED` até ser reconciliada com a tela License Information.

Referências oficiais:

- [Download assets chunk](https://developer.tenable.com/reference/exports-assets-download-chunk)
- [Manage unassessed assets](https://developer.tenable.com/docs/manage-unassessed-assets-tio)
- [License Information](https://docs.tenable.com/vulnerability-management/Content/Settings/License/ViewLicenseInformation.htm)

## Protocolo para validar licenciamento

Uma validação autenticada futura deve ser somente leitura e seguir esta ordem:

1. confirmar o cliente, a organização/tenant visível e o tipo de credencial;
2. registrar horário UTC e ambiente;
3. consultar `/server/properties` selecionando somente campos permitidos;
4. classificar cada produto como habilitado, expirado ou ausente;
5. calcular separadamente qualquer valor `DERIVED` e registrar a fórmula;
6. comparar com License Information na console;
7. registrar divergência, atualização da console, proporção e arredondamento;
8. manter `UNAVAILABLE` quando a semântica não puder ser comprovada.

O teste não deve ativar trial, mudar alocação, solicitar privilégio, alterar
política ou persistir resposta bruta. Uma consulta aceita pela API não comprova que
o valor possui a mesma semântica visual do painel.

## Health Check futuro e complementação pela console

O relatório Health Check ainda não faz parte do pipeline deste repositório. O
modelo editorial e as métricas serão definidos em ciclo próprio quando o documento
base e os requisitos forem fornecidos e aprovados.

A direção já acordada é separar:

- **núcleo reproduzível:** APIs públicas ou contratos autenticados validados;
- **complemento assistido:** valores e capturas sem paridade pública suficiente;
- **apresentação:** montagem do relatório somente após validar a origem de cada
  item.

Na complementação assistida, o operador faz login e MFA diretamente no site
oficial. O agente trabalha em modo leitura, confirma a organização antes de cada
captura e registra URL, horário, filtro e período. Não guarda senha, cookie, token
ou sessão e não ativa trials, políticas, respostas ou novos privilégios.

Chamadas internas observadas no navegador podem ajudar a entender a console, mas
não viram dependência regular apenas por terem funcionado uma vez. Até existir
documentação oficial ou contrato autenticado estável, elas permanecem evidência
exploratória e o dado é classificado como `CONSOLE_ONLY`.

Cada item do Health Check deverá terminar em um estado explícito, como
`PROVIDED`, `UNAVAILABLE`, `OMITTED` ou `PENDING`. Ausência e indisponibilidade não
podem ser convertidas em zero, e uma captura nunca pode ser reutilizada entre
clientes ou períodos.

## Mapa do código e dos testes

| Responsabilidade | Arquivo principal |
|---|---|
| endpoints e consultas | `src/tenable_reports/infrastructure/tenable_cloud/queries.py` |
| transporte, retry e paginação | `src/tenable_reports/infrastructure/tenable_cloud/client.py` |
| probe de capacidades | `src/tenable_reports/application/cloud_contract.py` |
| checkpoints e coleta | `src/tenable_reports/application/collect_cloud.py` |
| normalização | `src/tenable_reports/application/normalize_cloud.py` |
| correlação de correções | `src/tenable_reports/application/cloud_corrections.py` |
| dataset do relatório | `src/tenable_reports/application/cloud_report_dataset.py` |
| persistência histórica | `src/tenable_reports/infrastructure/cloud_snapshots_postgresql.py` |
| cliente e contrato | `tests/test_cloud_client.py`, `tests/test_cloud_contract.py` |
| coleta e retomada | `tests/test_cloud_collection.py`, `tests/test_cloud_execution.py` |
| métricas e correções | `tests/test_cloud_report_dataset.py`, `tests/test_cloud_corrections.py` |

## Limites confirmados

- O schema GraphQL completo não é versionado no projeto e pode variar por tenant.
- Campos opcionais só são usados depois do probe; rejeição não é convertida em
  valor vazio.
- O relatório Cloud não reconstrói um ponto passado sem snapshot compatível.
- O inventário Cloud não é, por si só, consumo de licença.
- `/server/properties` informa licenciamento e aplicações, mas a semântica de uso
  de cada campo ainda precisa ser validada contra o painel antes de publicação.
- Endpoints MSSP dependem de uma conta parceira e não são fallback para cliente
  comum.
- Attack Path findings não medem consumo de licença.
- APIs internas da console não são contrato estável até validação específica.

## Manutenção deste guia

Atualize este documento quando mudar endpoint, autenticação, versão de consulta,
fonte obrigatória/opcional, paginação, retry, persistência, métrica ou classificação
de licenciamento. Toda mudança executável exige teste correspondente. Evidência
autenticada deve ser sanitizada antes de virar fixture ou documentação.
