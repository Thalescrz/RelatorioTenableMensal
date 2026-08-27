# Catálogo de dados e métricas

## Fontes

O projeto usa Tenable Vulnerability Management para ativos, findings VM, TAGs e
metadados de plugins. Tenable Web App Scanning fornece aplicações e findings WEB
quando o produto e as permissões estão disponíveis. Tenable Cloud Security fornece
uma fotografia GraphQL independente de workloads, imagens, ocorrências,
inventário, postura e ciclo de vida quando habilitado. Consulte o
[catálogo detalhado de APIs](02-catalogo-apis-tenable.md) para endpoints e contratos.

O PostgreSQL não substitui a Tenable como origem dos findings. Ele mantém estado
operacional, histórico compacto, tentativas, documentos e a referência `MAIN`.

## Ativo normalizado

Um ativo é identificado internamente por `asset_key`, derivada da origem e do UUID.
O modelo conserva:

- identificadores de origem, cliente e ciclo de vida;
- nome exibido, tipos e nomes de fontes;
- hostnames, FQDNs, IPv4, IPv6 e endereços MAC;
- sistemas operacionais, rede e identificador de rede;
- primeira e última varredura, última varredura autenticada;
- criação, atualização, exclusão e encerramento;
- ACR e AES quando fornecidos.

O nome apresentado nunca deve ficar vazio. A precedência é: nome explícito do
ativo, hostname, FQDN, NetBIOS, IPv4, IPv6 e, somente como último recurso, UUID da
origem. O replay de snapshots compactos antigos reaplica essa regra aos aliases que
ainda estejam preservados. Se o snapshot não contiver nenhum nome ou alias, o IP ou
UUID é exibido; o sistema não inventa um hostname perdido.

Hostname e IP podem mudar ou ser reutilizados. Eles servem somente para
apresentação; a reconciliação entre ativo e finding continua exclusivamente por
UUID.

## Finding VM normalizado

O modelo conserva:

- identificadores do finding, ativo, cliente e origem;
- plugin: ID, nome e família;
- CVEs e referências;
- sinopse, descrição, solução e `Plugin Output` opcional;
- porta, protocolo e serviço;
- estado e severidade;
- primeira identificação, última identificação, correção e ressurgimento;
- CVSS v2/v3, VPR e vetor de ataque;
- patch disponível;
- indicador geral `exploitable`;
- indicador direto `exploited_by_malware`;
- indicadores segregados por framework de exploração.

`Plugin Output` pode conter dados sensíveis e aumentar bastante o payload. Ele só é
coletado e exibido quando o perfil autoriza a coluna opcional.

O VPR permanece como número ou nulo no dado normalizado. Nas tabelas compactas
de vulnerabilidades, o relatório exibe 0 quando o plugin não possui VPR atribuído;
essa convenção é apenas visual e não altera ranking, faixas VPR ou histórico.

## Finding WAS normalizado

O modelo WEB conserva:

- aplicação, URI afetada e identificadores de origem;
- plugin, CVEs, referências e classificação OWASP;
- sinopse, descrição e solução;
- output, prova e payload quando disponibilizados e autorizados;
- método HTTP, parâmetro de entrada e dados de serviço;
- estado, severidade e datas de identificação/correção;
- CVSS v3 e VPR.

Ausência de licença, permissão ou achados WAS não deve invalidar o dataset VM.

## Fotografia Cloud normalizada

O Cloud mantém populações separadas para máquinas virtuais, imagens de contêiner,
ocorrências de vulnerabilidade, recursos de inventário e findings de postura. A
identidade usa o ID estável retornado pela API e inclui o tipo do recurso; nome,
IP, conta, repositório e digest são atributos de exibição.

O dataset `cloud-metrics-v1` conserva:

- contexto da fotografia, horário de coleta, competência e aviso histórico;
- totais de ativos, workloads, imagens, CVEs e ocorrências por severidade;
- Top 5 de CVEs críticas com VPR, CVSS, componentes e ativos afetados;
- Top 10 com correção, tipo, origem da classificação e ação recomendada;
- aging, resolvidas e tempo médio de remediação quando o ciclo de vida existe;
- inventário por provedor e região;
- findings de postura e capacidades observadas no tenant;
- proveniência das tabelas, qualidade, fontes completas ou indisponíveis;
- série mensal compacta proveniente de fotografias anteriores compatíveis.

VPR `0` é zero e aparece como `0`. VPR ausente é `N/D` e não recebe pontuação
inventada no ranking. O tipo de correção prefere campo explícito; a regra local
determinística registra sua origem e, sem evidência, retorna `Não determinado`.

## Janela temporal

Todos os cálculos usam intervalo semiaberto `[início, fim)`: o início pertence ao
relatório e o instante final pertence ao período seguinte.

| Conjunto | Estados | Campo temporal |
|---|---|---|
| Não mitigadas | `OPEN`, `REOPENED` | `last_found` |
| Mitigadas | `FIXED` | `last_fixed` |
| Novas | ativas cuja primeira ocorrência está no período | `first_found` |
| Ressurgidas | `REOPENED` | `resurfaced_at` |

O filtro inferior enviado à API é seguido por validação local das duas fronteiras.
`Informational` é excluída; as severidades consideradas são Critical, High, Medium
e Low.

## Métricas principais

- Vulnerabilidades não mitigadas: quantidade de findings ativos no período.
- Vulnerabilidades mitigadas: quantidade de findings corrigidos no período.
- Vulnerabilidades novas: findings cuja primeira identificação ocorreu no período.
- Vulnerabilidades ressurgidas: findings reabertos com data de ressurgimento no
  período.
- Ativos vulneráveis: ativos distintos associados aos findings do conjunto.
- Principais ativos vulneráveis: ranking por contagem de findings e severidade,
  mantendo IP e nome somente como atributos de exibição.
- `Exploitable` por ativo: quantidade de findings do ativo com
  `plugin.exploit_available` verdadeiro.
- Matriz de explorabilidade: exibe `Exploitable`, `Malware`, `Core Impact`,
  `Canvas`, `D2 Elliot`, `ExploitHub` e `Metasploit`, nessa ordem. A linha
  `Malware` exige simultaneamente `Exploit Available = true` e
  `Exploited By Malware = true`. As cinco linhas de framework usam seus
  indicadores específicos e não devem ser inferidas apenas do indicador geral.
  Se alguma linha tiver dados, as sete aparecem, inclusive as de valor zero.
- Panorama por sistema operacional: para manter compatibilidade com o quadro ITP,
  classifica as instâncias pelas famílias de plugin nas linhas `Windows`, `Mac OS X`,
  `Linux/Unix` e `WEB`; `Devices/Services` usa nome de plugin contendo
  `service`. As cinco linhas são sempre exibidas. As categorias são filtros
  independentes e podem se sobrepor; os nomes completos dos sistemas operacionais
  dos ativos não criam linhas adicionais.
- Top 5 VM: ranking local de findings não mitigados usando VPR, severidade e ativos
  afetados, seguido do detalhamento e conjunto de hosts.
- Top 5 WAS: ranking equivalente no conjunto WEB suportado.
- OWASP Top 10: distribuição apenas dos achados que possuem classificação mapeável;
  categorias sem ocorrências podem permanecer zeradas, acompanhadas de texto quando
  todo o quadro estiver vazio.

## Métricas Cloud

- Principais hosts e imagens: ranking por ocorrências e severidade dentro da
  fotografia, sem misturar as duas populações.
- Top 5 críticas: CVEs críticas ordenadas por VPR real, CVSS e ativos afetados,
  acompanhadas de descrição, correção e tabela de ativos.
- Top 10 com correção: apenas vulnerabilidades com remediação correlacionada e tipo
  de correção rastreável; o documento não repete a ação recomendada extensa nessa
  tabela.
- Postura Cloud: findings não relacionados a vulnerabilidade, somente quando a
  capacidade e a população são confirmadas.
- Aging: ocorrências abertas por idade; data ausente permanece em faixa própria.
- Remediação: resoluções dentro do intervalo e média de dias somente para registros
  com datas válidas.
- Evolução mensal: comparação de fotografias Cloud compactas e compatíveis; mês
  indisponível permanece como lacuna, nunca zero artificial. Quando só existe a
  fotografia atual, a tabela e o gráfico exibem um único ponto real e o texto deixa
  explícito que ainda não há comparação temporal.

## TAGs

As TAGs são descobertas no ambiente e associadas aos UUIDs de ativos. Há duas
seleções independentes no perfil:

- TAGs que geram relatório operacional;
- TAGs cujo relatório inclui comparativo temporal.

O recorte acontece depois da coleta e normalização gerais. Os números gerais não
mudam. O histórico de uma TAG só é comparado com a mesma categoria e valor em
período anterior compatível.

## Séries históricas

O histórico compacto conserva as agregações necessárias para tabelas e gráficos,
incluindo totais por severidade/estado, ativos, novas, mitigadas e não mitigadas.
Ele não depende de reter permanentemente todos os chunks e findings raw.

Para Cloud, cada fotografia compacta preserva indicadores, rankings e capacidades;
as respostas GraphQL completas continuam transitórias.

Se não houver referência anterior, os módulos correntes ainda são gerados e o
comparativo informa que não existe base compatível. Uma ausência não pode resultar
em documento customizado visualmente vazio.

## Conferência na Tenable

Quando habilitados, os filtros de validação aparecem discretamente abaixo de cada
tabela. Eles descrevem caminho, estados, severidades, campo de data e período; não
pretendem ser uma consulta automática nem substituir a regra local de ranking.

Exemplos:

- Não mitigadas: `Explore > Findings > Vulnerabilities`; estados Active, New e
  Resurfaced; severidades Critical a Low; `Last Seen` no período.
- Mitigadas: estados Fixed; severidades Critical a Low; `Last Fixed` no período.
- Ressurgidas: estado Resurfaced/Reopened; `Resurfaced Date` no período.
- Exploráveis gerais: indicador `Exploit Available = true` dentro do conjunto e do
  período da própria tabela.

No Cloud, as notas usam a visão GraphQL, tipos de entidade, agrupamento e regra de
correlação correspondente à tabela.

Os nomes visíveis dos filtros podem variar na plataforma. A regra temporal do
dataset é a autoridade para explicar uma diferença.

## Qualidade e reconciliação

O manifesto da execução registra hashes, contagens de entrada/saída, duplicatas,
rejeições, vínculos e órfãos. A publicação deve preservar:

- identidade por UUID;
- soma coerente das severidades e totais;
- ausência de findings fora do período;
- módulos opcionais sem impacto sobre os obrigatórios;
- rastreabilidade entre dataset, documento e execução.
