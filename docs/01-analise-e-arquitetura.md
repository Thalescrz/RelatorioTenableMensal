# Fase 1 - Análise e arquitetura

**Status:** concluída em 2026-08-12  
**Escopo desta versão:** scripts, documentação oficial e quatro relatórios DOCX representativos  
**Resultado:** contrato funcional e arquitetural fechado; implementação do produto ainda não iniciada

## 1. Resumo executivo

A solução deve ser construída como uma plataforma de relatórios, não como mais um script monolítico. O primeiro produto será o relatório técnico de vulnerabilidades, mas coleta, modelos de dados, histórico, perfis de clientes, validação e orquestração devem ser reutilizáveis pelos relatórios Cloud e futuros.

A análise comparativa levou a uma separação editorial explícita: cada execução produzirá um DOCX-base estável e um segundo DOCX modular de inteligência/customizações. Ambos serão derivados do mesmo snapshot imutável e identificados pelo mesmo `run_id`. O contrato exato está em `docs/04-matriz-e-contrato-dos-relatorios.md` e as regras de histórico e dos módulos críticos em `docs/05-historico-regras-criticas-e-traducao.md`.

Os materiais existentes oferecem dois conjuntos úteis e distintos:

1. O relatório executivo comprova o fluxo assíncrono de exportação do Tenable Vulnerability Management e contém regras aproveitáveis de normalização, exposição atual, remediação no período, SLA, aging, VPR, CVSS e exploitabilidade.
2. O relatório Cloud contém um cliente GraphQL mais robusto, com validação de TLS, retries, respeito parcial a falhas transitórias, paginação por cursor, redução adaptativa de página, isolamento de fontes opcionais e testes automatizados.

Nenhum deles deve ser adotado como base visual do novo relatório. Ambos misturam responsabilidades em um arquivo grande; no script executivo também existem quinze funções redefinidas por blocos de override, o que aumenta o risco de alterações acidentais.

A documentação oficial atual reforça uma decisão arquitetural essencial: findings de vulnerabilidade devem ser sincronizados com um inventário de ativos independente. O objeto de ativo embutido no finding pode estar incompleto ou desatualizado; o vínculo recomendado é `finding.asset.uuid -> asset.id`. A identidade estável de um finding de infraestrutura deve considerar, no mínimo, `asset.uuid + plugin.id + port.port + port.protocol`, e não apenas plugin ou ativo.

## 2. Inventário dos materiais

| Material | Local | Papel na análise | Situação |
|---|---|---|---|
| Especificação da solução | Anexo `pasted-text.txt` | Requisitos, restrições e resultado esperado | Analisado |
| README do relatório executivo | `C:/Codex/RelatorioExecutivoTenable/README.md` | Semântica, filtros e operação do relatório executivo | Analisado |
| Script do relatório executivo | `C:/Codex/RelatorioExecutivoTenable/tenable_report_executivo.py` | Cliente VM, normalização, métricas, gráficos e saídas Office | Analisado estaticamente |
| README do relatório Cloud | `C:/Codex/RelatorioCloudTenable/README.md` | Operação e escopo do coletor GraphQL | Analisado |
| Script do relatório Cloud | `C:/Codex/RelatorioCloudTenable/tenable_cloud_graphql_report.py` | Cliente GraphQL, paginação, tolerância a falhas e agregações | Analisado estaticamente |
| Referência GraphQL Cloud | `C:/Codex/RelatorioCloudTenable/TENABLE_CLOUD_SECURITY_API_GRAPHQL.md` | Contrato conhecido, consultas e troubleshooting | Analisado |
| Testes do relatório Cloud | `C:/Codex/RelatorioCloudTenable/tests/test_report.py` | Evidência de comportamento e exemplos sanitizados | Analisado e executado: 10/10 testes passaram |
| Arquivos auxiliares Cloud | `.env.example` e BAT | Configuração e execução local | Inventariados |
| Relatório Cliente Y | DOCX fornecido | Estrutura, conteúdo, identidade visual e variações mensais | Analisado: 41 páginas |
| Relatório Cliente X | DOCX fornecido | Variante compacta e conteúdo Cloud | Analisado: 22 páginas |
| Relatório Cliente Z | DOCX fornecido | Comparativos, Top 5 WEB e Container Images | Analisado: 38 páginas |
| Relatório Cliente A | DOCX fornecido | Evolução, software sem suporte, Top 5 WEB e Output | Analisado: 70 páginas |

O workspace de destino estava vazio no início da análise.

## 3. Entendimento do processo atual

O processo alvo ainda é predominantemente manual e varia por cliente. A equipe consulta ou exporta dados Tenable, aplica filtros e regras de negócio, calcula indicadores e monta documentos Word com identidade visual corporativa. Os scripts existentes automatizam partes adjacentes, mas não o relatório técnico de vulnerabilidades solicitado:

- o script executivo consolida exposição atual e remediação recente em materiais para board;
- o script Cloud coleta inventário, vulnerabilidades e postura do Tenable Cloud Security e gera dados tabulares;
- os relatórios Word de referência continuam sendo a fonte de verdade para o conteúdo e a apresentação do novo relatório técnico.

Os quatro DOCX foram inventariados estruturalmente, exportados para PDF e inspecionados visualmente. Os valores ocultados foram classificados como anonimização intencional, nunca como falha de preenchimento. A matriz completa separa recorrência factual de decisão de produto: o Top 5 WEB, por exemplo, aparece apenas em dois modelos, mas foi promovido ao documento-base por solicitação explícita.

## 4. Mapeamento dos relatórios e matriz comparativa

### 4.1 Estado atual

| Entrega | Estado |
|---|---|
| Inventário estrutural dos quatro DOCX | Concluído |
| Renderização e inspeção visual | Concluída: 171 páginas |
| Conteúdo estático, dinâmico e sensível | Classificado |
| Padrão global e decisão do DOCX-base | Fechados |
| Catálogo unificado de customizações | Fechado em nível funcional |
| Campos e regras críticas | Especificados; contratos de tenant ainda devem ser testados antes da implementação |
| Matriz Cliente Y x X x Z x A | Documentada em `docs/04-matriz-e-contrato-dos-relatorios.md` |

### 4.2 Regra de classificação que será usada

- **Padrão:** presente em todos ou quase todos os relatórios e semanticamente equivalente.
- **Comum:** presente em mais de um relatório, sem cobertura suficiente para ser padrão.
- **Customização:** presente em um subconjunto ou com regra/configuração diferente.
- **Candidato a novo padrão:** não é recorrente hoje, mas melhora clareza, completude, priorização ou auditabilidade e possui fonte de dados sustentável.
- **Pendente de investigação:** não foi possível comprovar conteúdo, origem, semântica ou disponibilidade pela API.

O protocolo detalhado de ingestão e comparação dos DOCX está em `docs/03-protocolo-analise-docx.md`.

## 5. Mapeamento preliminar de dados

Esta tabela combina os requisitos observados nos quatro relatórios com as capacidades confirmadas nas APIs. Os nomes exatos dos campos continuam sujeitos a teste de contrato no tenant.

| Informação potencial | Fonte provável | Produto | Endpoint/fonte | Transformação | Estado |
|---|---|---|---|---|---|
| Instâncias de vulnerabilidade de infraestrutura | Export de findings | Vulnerability Management | `POST /vulns/export` + status + chunks | Normalizar estado, datas, plugin, ativo, porta e protocolo; deduplicar pela chave do finding | Confirmado como fonte base |
| Inventário e ciclo de vida de ativos | Export de ativos v2 | Vulnerability Management | `POST /assets/v2/export` + status + chunks | Vincular `finding.asset.uuid` a `asset.id`; preservar deleted/terminated | Confirmado como enriquecimento necessário |
| Descrição, sinopse e solução | Catálogo de plugins ou payload selecionado | Vulnerability Management | `GET /plugins/plugin/{id}` ou propriedades do export | Cache por plugin/versão; preservar original e tradução | Confirmado; fragmentação está especificada no contrato crítico |
| CVE, CVSS, VPR, exploit e patch | Finding/plugin | Vulnerability Management | Chunk de vulnerabilidades e catálogo de plugins | Usar `plugin.vpr` após a transição VPR v2; não usar `plugin.vpr_v2` | Confirmado |
| Software vulnerável e correções disponíveis | Export de findings | Vulnerability Management | `include_software_vulns=true` em `/vulns/export` | Normalizar pacotes e fix versions | Candidato; depende dos relatórios |
| Aging de abertas | Finding | Vulnerability Management | Export de vulnerabilidades | Data de referência configurada e documentada; tratar ausências | Confirmado como calculável |
| Corrigidas no período e tempo de remediação | Finding | Vulnerability Management | Export com `state=FIXED` e filtro temporal | Calcular somente com datas válidas e ordem temporal válida | Confirmado como calculável |
| SLA | Regra do cliente + finding | Configuração/VM | Perfil do cliente + dados normalizados | Regra versionada por severidade, VPR ou política contratual | Regra de negócio; não é KPI nativo equivalente |
| Dados WAS em volume | Export WAS | Web App Scanning | `POST /was/v1/export/vulns` + status + chunks | Adaptador e modelo de finding próprios, unidos apenas no domínio normalizado | Confirmado como fonte dedicada |
| Detalhe pontual WAS | Busca/detalhe | Web App Scanning | `POST /was/v2/vulnerabilities/search`; `GET /was/v2/vulnerabilities/{vuln_id}` | Enriquecimento sob demanda | Confirmado; necessidade pendente |
| Scans e histórico de execução | APIs de scans/metadados de ativos | Vulnerability Management/WAS | A confirmar por teste de contrato | Normalizar execução, escopo, status, autenticação e cobertura | Necessário ao módulo opcional `scan_auth_health` |
| Evidência/plugin output | Chunk do finding ou export de scan | Vulnerability Management/WAS | `include_plugin_output` ou APIs de scan | Opção explícita, desligada por padrão; sanitização, limite e retenção configuráveis | Decidido; contrato do tenant ainda deve comprovar os campos exatos |
| Histórico mês a mês | Snapshots locais | Plataforma de relatórios | Repositório interno | Comparar snapshots com chave estável e qualidade conhecida | Requer persistência desde o primeiro MVP |

## 6. Análise dos scripts existentes

### 6.1 REUTILIZAR como comportamento comprovado

Do script executivo:

- autenticação VM por `X-ApiKeys`, sem credenciais hardcoded;
- fluxo assíncrono `iniciar -> consultar status -> baixar chunks`;
- parsing de chunk em JSON, JSON Lines e gzip;
- normalização tolerante a aliases de campos;
- separação semântica entre exposição atual (`OPEN`/`REOPENED`) e remediação no período (`FIXED` com data válida);
- cálculo defensivo de aging, tempo de correção e SLA;
- distinção entre severidade nativa e faixa analítica VPR;
- possibilidade de reprocessamento por CSV sem nova chamada à API.

Do script Cloud:

- validação de endpoint HTTPS e cadeia TLS, incluindo CA corporativa;
- tratamento seguro de mensagens de erro sem expor o secret;
- retry de `429`, `500`, `502`, `503` e `504`;
- paginação GraphQL por cursor, com detecção de cursor ausente/repetido;
- redução adaptativa do tamanho de página após falhas de transporte;
- isolamento de fontes opcionais, sem preencher lacunas com estimativas;
- fixtures sanitizadas e testes de agregação, retry, paginação e geração de arquivos.

### 6.2 ADAPTAR antes de incorporar

- Extrair clientes HTTP, coletores, normalizadores, métricas e renderizadores para módulos independentes.
- Fazer o cliente VM respeitar `Retry-After`; hoje ele aplica apenas backoff local.
- Tratar exceções de transporte do `requests`, não somente respostas HTTP.
- Tratar `409 Conflict` de export duplicado e, quando aplicável, aproveitar com segurança o `active_job_id` devolvido.
- Representar export concluído sem chunks como resultado vazio válido; o script atual pode aguardar até timeout quando o status termina com zero findings.
- Usar o formato oficial de `User-Agent` com fornecedor, produto e versão.
- Trocar argumentos específicos de cliente na CLI por perfis declarativos validados.
- Transformar os dicionários/dataframes em modelos de domínio com tipos, proveniência e estado de disponibilidade.
- Separar dados sensíveis brutos das saídas publicáveis e aplicar política de retenção.
- Aproveitar cálculos somente após definir unidade de contagem, chave de deduplicação, datas de referência e regras contratuais.
- Migrar campos VPR para o contrato atual `plugin.vpr`; `plugin.vpr_v2` foi descontinuado em 2026-07-01.

### 6.3 DESCARTAR para o novo relatório

- layout, gráficos e narrativa do relatório executivo;
- geração de Word e PowerPoint embutida no mesmo arquivo da coleta;
- dependência de overrides tardios que redefinem funções já declaradas;
- regras visuais hardcoded como contrato global do produto;
- uso do nome do cliente como valor livre em toda a base, sem `client_id` imutável;
- qualquer inferência de tendência histórica sem snapshots reais;
- preenchimento de dado ausente com zero quando zero e indisponível têm significados diferentes.

### 6.4 INVESTIGAR/VALIDAR NO TENANT

- semântica operacional de `last_found=0`, usada pelo script e não descrita de modo inequívoco na página pública atual;
- aceitação de todos os filtros e propriedades selecionados no tenant real;
- contrato de campos do chunk, especialmente aliases históricos `definition.*` versus o contrato atual `plugin.*`;
- cobertura de `include_unlicensed=true` e impacto contratual no total reportado;
- permissões mínimas e visibilidade por tags/grupos de ativos;
- endpoint GraphQL operacional do tenant Cloud. A documentação pública usa `/graphql`, enquanto o material local validado usa `https://app.tenable.com/api/graph` com documentação autenticada;
- identidade/deduplicação WAS, exploitabilidade e plugin output usados nas seções críticas;
- fonte operacional de autenticação/cobertura de scans e campos de ciclo de vida de software.

## 7. Arquitetura proposta

### 7.1 Princípios

1. **Domínio independente da Tenable:** o relatório consome modelos internos, não respostas HTTP.
2. **Adaptador por produto:** VM, WAS e Cloud possuem contratos e semânticas distintas.
3. **Configuração declarativa:** nenhum `if cliente == ...`.
4. **Snapshots reproduzíveis:** todo relatório aponta para uma coleta identificada e reprocessável.
5. **Incerteza explícita:** indisponível, não coletado, não aplicável e zero são estados diferentes.
6. **Validação antes de publicar:** falha parcial não pode virar relatório aparentemente completo.
7. **Template Word como ativo:** apresentação não conhece autenticação, endpoint ou regra de coleta.

### 7.2 Componentes

```text
CLI / futuro agendador / futura UI
              |
       Application services
       generate / collect / validate
              |
  +-----------+-------------+
  |           |             |
Collectors  Processing   Presentation
VM/WAS/Cloud metrics/QA  DOCX/charts/PDF
  |           |             |
Normalizers -> Domain models <- Report definition
              |
      Snapshot repositories
 raw + normalized + run catalog
```

### 7.3 Estrutura inicial sugerida

```text
src/tenable_reports/
  cli.py
  application/
    collect.py
    generate.py
    validate.py
  domain/
    models.py
    availability.py
    identities.py
    repositories.py
  infrastructure/
    tenable_vm/
      client.py
      vulnerability_export.py
      asset_export.py
      plugins.py
    tenable_was/
      client.py
      findings_export.py
    tenable_cloud/
      graphql_client.py
      queries/
    persistence/
      snapshots.py
      run_catalog.py
    documents/
      base_docx_renderer.py
      intelligence_docx_renderer.py
      visual_validation.py
  reports/
    vulnerabilities/
      definition.py
      dataset.py
      metrics.py
      validations.py
      sections/
    cloud/
  config/
    schema.py
clients/
  examples/
templates/
  corporate/
  clients/
tests/
  unit/
  contract/
  fixtures/
docs/
```

`reports/cloud/` é apenas ponto de extensão nesta fase; não deve ser implementado antes da estabilização do relatório de vulnerabilidades.

### 7.4 Modelo de domínio inicial

| Entidade | Identidade/regra principal |
|---|---|
| `ClientProfile` | `client_id` imutável; nome de exibição é atributo versionado |
| `ReportRun` | `run_id`, cliente, tipo, período, configuração, timestamps e status |
| `SourceSnapshot` | fonte, consulta/filtros higienizados, completude, contagem, checksum e localização |
| `Asset` | `client_id + source + source_asset_id`; aliases de rede não são identidade primária |
| `VulnerabilityDefinition` | fonte + plugin/vulnerability ID; CVE é relação, não necessariamente chave única |
| `Finding` | chave específica da fonte; em VM: ativo + plugin + porta + protocolo |
| `FindingObservation` | estado e métricas observados em um snapshot |
| `ScanExecution` | produto, scan/schedule, início, fim, status e escopo |
| `MetricResult` | definição versionada, valor, unidade, população e snapshot de origem |
| `DataQualityIssue` | regra, severidade, fonte, impacto e evidência higienizada |
| `ReportArtifact` | arquivo, hash, template, versão, validações e classificação |

### 7.5 Configuração por cliente

O perfil deve ser YAML ou JSON validado por schema e armazenar somente configuração não secreta:

```yaml
schema_version: 1
client_id: cliente_exemplo
display_name: Cliente Exemplo
report:
  type: vulnerabilities
  templates:
    base: corporate/base-v1
    intelligence: corporate/intelligence-v1
  base_modules: [summary, infrastructure, remediation, was, was_remediation]
  intelligence_modules: [vm_monthly_volume, vm_cvss_vpr_matrix]
scope:
  vm:
    tags: []
    asset_groups: []
  was:
    enabled: false
rules:
  sla_profile: default-v1
  include_accepted_risk: false
presentation:
  locale: pt-BR
  vm_top5_include_output: false
  was_top5_include_output: false
  custom_texts: {}
```

O perfil referencia secrets por nome lógico; nunca contém chaves. Overrides visuais e módulos habilitados são dados, não condicionais no código.

### 7.6 Persistência e histórico

Recomendação incremental:

- **raw imutável:** chunks originais em `jsonl.gz`, com checksum e metadados de coleta;
- **normalizado:** snapshots colunares particionados por `client_id/source/date` para comparação e reprocessamento;
- **catálogo local inicial:** SQLite para runs, fontes, artefatos, qualidade e índices de localização;
- **interface de repositório:** permite migrar para PostgreSQL e object storage sem alterar métricas ou renderização.

Essa abordagem evita uma infraestrutura de servidor no MVP, mas preserva histórico real. Dados reais, snapshots e artefatos devem ficar fora do Git, em diretório com ACL restrita e política de retenção. Antes de escolher Parquet como formato normalizado, a primeira fase técnica deve confirmar disponibilidade e governança da dependência; JSONL comprimido permanece o fallback portátil.

### 7.7 Apresentação e Word

- A família editorial terá dois DOCX versionados, base e inteligência, cada um com manifesto de placeholders, estilos, seções, imagens e regras de quebra.
- O gerador recebe apenas um `ReportDataset` validado e uma `ReportDefinition`.
- Os dois documentos apontam para o mesmo `run_id` e conjunto de snapshots; não podem misturar coletas.
- Gráficos são gerados como artefatos determinísticos com dados e metadados de origem.
- Campos, cabeçalhos, rodapés, numeração, TOC e seções devem ser preservados via `python-docx` e, quando necessário, OOXML controlado.
- Toda saída DOCX deverá ser renderizada em PNG/PDF e todas as páginas inspecionadas; validação estrutural isolada não é suficiente.
- A comparação inicial dos relatórios usará extração estrutural mais renderização visual, nunca apenas o XML interno.

### 7.8 Orquestração e observabilidade

Cada execução terá contexto estruturado:

```text
run_id, client_id, report_type, stage, source, endpoint_name,
started_at, duration_ms, attempt, records, warnings, status, artifact
```

Os logs serão JSON, com filtro central de secrets e sem payloads de findings. Estados de run sugeridos: `created`, `collecting`, `normalizing`, `processing`, `validating`, `rendering`, `completed`, `completed_with_warnings`, `failed`.

### 7.9 Gates de qualidade

1. **Contrato:** autenticação, endpoint, schema e uma página/chunk mínimo válidos.
2. **Coleta:** todas as fontes obrigatórias encerradas; chunks esperados contabilizados; truncamentos registrados.
3. **Normalização:** chaves, estados, severidades e datas válidos; rejeições quantificadas.
4. **Reconciliação:** total normalizado reconciliado com raw por fonte e população.
5. **Métrica:** soma de subtotais, denominadores, datas de corte e unidade de contagem consistentes.
6. **Documento:** valores de tabelas/gráficos reconciliados com `MetricResult`.
7. **Visual:** todas as páginas renderizadas e inspecionadas, sem clipping, sobreposição ou quebras inadequadas.

Fontes opcionais indisponíveis podem permitir `completed_with_warnings` somente se nenhum indicador obrigatório depender delas. Caso contrário, a publicação deve falhar.

## 8. Riscos e pontos de atenção

| Risco | Impacto | Tratamento |
|---|---|---|
| Deriva entre modelos DOCX | Seções ou números deixam de ser comparáveis | Manifesto de módulos, definição versionada e testes visuais por perfil |
| Chave de finding incorreta | Dupla contagem ou perda de instâncias | Chave por fonte; VM usa ativo + plugin + porta + protocolo |
| Ativo embutido no finding desatualizado | IP/hostname/estado incorretos | Sincronizar export de ativos v2 |
| Deleted/terminated sem fechamento de finding | Histórico incorreto | Processar ciclo de vida do ativo e não inferir `FIXED` |
| Export sem filtro temporal | Janela padrão de 30 dias | Exigir política temporal explícita e registrar filtros |
| Export duplicado (`409`) | Execução falha ou duplica carga | Reconciliar `active_job_id` com segurança |
| Chunks expiram | Reprocessamento impossível | Persistir imediatamente; adotar janela conservadora de 24 horas |
| VPR v2 pós-2026-07-01 | Mudança de score e campos legados | Usar `vpr_score` e `plugin.vpr`; versionar métricas |
| Limite de plugin output | Evidência truncada | Registrar truncamento; não prometer evidência completa |
| Regras de SLA divergentes da Tenable | Comparação enganosa com cards nativos | Definição versionada e texto metodológico explícito |
| Falha parcial silenciosa | Relatório incompleto publicado | Gates por fonte e disponibilidade tipada |
| Dados multicliente | Vazamento de informações | Isolamento por `client_id`, ACL, paths seguros e testes negativos |
| Template frágil | Documento abre, mas visualmente quebra | Renderização e inspeção de todas as páginas |
| Monólitos de referência | Regressões e baixa testabilidade | Reusar comportamento, não copiar estrutura |

## 9. Roadmap incremental

### Fase 1A - Discovery técnico

**Concluído:** inventário disponível, análise dos scripts, execução dos testes Cloud e validação preliminar de documentação oficial.  
**Validar:** achados desta análise com o responsável técnico.

### Fase 1B - Discovery dos documentos

**Concluída:** quatro DOCX, 171 páginas, inventário estrutural, renderização, matriz comparativa, catálogo de indicadores/textos e mapa visual.  
**Gate atingido:** nenhum arquivo representativo ficou sem análise.

### Fase 1C - Fechamento do contrato do relatório

**Concluída em nível funcional:** dois artefatos DOCX, módulos padrão/customizáveis, regras de ausência, Top 5 VM/WAS, coluna Exploitable, Output opcional, tradução fragmentada e histórico comparável.  
**Próximo gate:** converter o contrato funcional em schemas executáveis e validar os campos exatos no tenant antes de qualquer relatório real.

### Fase 2 - Fundação mínima e cliente VM

**Concluída em 2026-08-12:** pacote, schema de configuração, IDs de execução, cliente HTTP e fluxo assíncrono de exportação de vulnerabilidades com fixtures. O contrato mínimo foi validado no tenant com 37 chunks; `Plugin Output` permaneceu desligado. O uso de `properties` ficou experimental porque os jobs seletivos testados falharam em todos os chunks. Sem Word e sem métricas de negócio.

### Fase 3 - Ativos e modelo normalizado

**Concluída em 2026-08-12:** export de ativos v2, identidades estáveis, vínculo exclusivo `finding.asset.uuid == asset.id`, raws imutáveis e snapshot normalizado em JSONL com manifesto, hashes, reconciliação e controles de qualidade. A execução autenticada completa reconciliou 3.173 ativos e 148.901 findings, com 100% dos findings vinculados, zero órfãos, zero rejeições e zero duplicatas. Nenhum fallback por IP/hostname foi permitido.

### Fase 4 - Dataset mínimo de vulnerabilidades

**Concluída em 2026-08-13:** `ReportDefinition v1.1` materializado com mês-calendário anterior para automação, um mês móvel como padrão manual, modos explícitos `--days` e `[início, fim)`, pastas separadas, duas barreiras temporais, classificação auditável de ativos “fantasmas”, reconciliação de populações, disponibilidade tipada e aviso de coleta tardia. O dataset contém não mitigadas, mitigadas, ressurgidas, aging, patch acima de 30 dias, matriz por sistema operacional, Top 10 de ativos com `Exploitable` e Top 5 VM detalhados com hosts, referências e `Output` opcional. A coleta autenticada mensal reconciliou 2.552 ativos e 60.057 findings sem órfãos; depois dos gates, foram incluídos 840 ativos e 14.091 findings do período. Severidade informativa permanece desativada em todos os perfis conhecidos.

### Fase 5 - Template Word mínimo

**Concluída em 2026-08-13:** template A4 controlado `base-docx-v0.1`, capa/período automatizados e seção de prova “Principais Ativos Vulneráveis” consumindo somente `report-dataset.json`. A coluna `Exploitable` é a última e validada como subconjunto de `Total`; `Output` permanece fora do padrão. A prova sanitizada mascara IP/hostname, possui cabeçalho, rodapé, paginação, texto alternativo e passou por 56 testes, inspeção estrutural sem avisos e renderização integral de duas páginas no Microsoft Word.

### Fase 6 - Primeiro relatório completo

**Concluída em 2026-08-13:** gerador `base-docx-v1.0` consumindo somente perfil e dataset versionado, com controle, sumário de nove seções principais, escopo, infraestrutura, KPIs, gráficos de severidade e aging, Top 10 de ativos com `Exploitable` na última coluna, mitigadas/ressurgidas, sistemas operacionais, Top 5 VM detalhado, bloco WEB tipado, metodologia, qualidade, rastreabilidade e contracapa. Descrições extensas são fragmentadas sem truncamento; hosts/IPs podem ser mascarados. A prova sanitizada passou por 59 testes, auditoria sem achados altos, Office MCP, varredura de PII/metadados e preservou o hash do template de referência.

### Fase 7 - Perfis e variações de clientes

**Concluída em 2026-08-13:** o núcleo do primeiro DOCX foi fixado como conjunto
obrigatório, os módulos do segundo DOCX passaram a usar IDs declarativos validados,
WAS e Cloud Security receberam gates explícitos de capacidade, `Output` falha quando
solicitado sem ter sido coletado e omissões por falta de dados/histórico são registradas
pela CLI. Dois perfis contrastantes foram executados sobre o mesmo dataset canônico;
as 13 páginas dos relatórios-base ficaram pixel a pixel idênticas, sem condicionais por
cliente no gerador. A prova passou por 82 testes, Office MCP e renderização integral no
LibreOffice.

### Fase 8 - WAS

**Concluída em 2026-08-13:** adaptador assíncrono, raw imutável, normalização
dedicada, reconciliação e integração ao `ReportDataset` foram implementados. Um
export autenticado retornou 3.304 findings; o recorte mensal de julho incluiu 1.151
instâncias (952 abertas e 199 corrigidas) em 20 aplicações. O primeiro DOCX passou a
receber aplicações, plugins, OWASP e Top 5 WEB detalhado diretamente do snapshot,
com URI mascarável, `Output` opcional e indisponibilidade tipada. Nenhum dado foi
inferido para métricas ausentes da API.

### Fase 9 - Histórico e tendências

**Concluída em 2026-08-13:** snapshots mensais são persistidos em SQLite atrás da
interface `SnapshotRepository`, com seleção determinística do predecessor por cliente,
tenant, tipo/modo do período, timezone, escopo e definição métrica. O dataset
enriquecido fornece séries mensais, overview anterior, transições por identidade
estável e comparativos da mesma tag em momentos diferentes. CSV funciona como ponte
agregada de importação/exportação; ausência de predecessor permanece explícita.

### Fase 10 - Orquestração e distribuição

**Concluída em 2026-08-13:** `run-client` publica o ciclo completo de um cliente e
`orchestrate` coordena carteiras automáticas ou pontuais em processos isolados.
Foram adicionados manifestos com hashes, logs JSONL, eventos locais de notificação,
planejamento de retenção com aplicação explícita, scripts PowerShell e instalador
opt-in da tarefa mensal no dia 1. A entrega externa permanece controlada e não é
executada sem uma integração autorizada.

## 10. Próxima pequena etapa prática recomendada

Executar um piloto automático com dois perfis reais, revisar tempos/limites de API e
definir o primeiro canal externo autorizado para consumir `notifications.jsonl` e
os manifestos de publicação.

## 11. O que permanece para os próximos incrementos

- Escopo de cada perfil: redes, tags, scanners, aplicações, repositórios e fontes licenciadas.
- Definições métricas versionadas, especialmente “mitigado”, “ressurgido”, “patch >30 dias” e contagens de instâncias versus plugins.
- Template corporativo aprovado e política de campos sensíveis para cada audiência.
- Política do mecanismo de tradução: execução local ou fornecedor aprovado, limite por requisição, retenção e proibição de PII/evidência.
- Classificação dos quatro modelos como atuais, legados ou excepcionais antes de reproduzir frases editoriais literalmente.

## 12. Referências oficiais principais

- [Export vulnerabilities](https://developer.tenable.com/reference/exports-vulns-request-export)
- [Retrieve Vulnerability Data from Vulnerability Management](https://developer.tenable.com/docs/retrieve-vulnerability-data-from-tenableio)
- [Vulnerability Management and Web App Scanning integration guidance](https://developer.tenable.com/docs/vm-and-was-integrations)
- [Export assets v2](https://developer.tenable.com/reference/export-assets-v2)
- [Get plugin details](https://developer.tenable.com/reference/io-plugins-details)
- [Tenable Plugin Attributes](https://developer.tenable.com/docs/tenable-plugin-attributes)
- [Export WAS findings](https://developer.tenable.com/reference/was-export-findings)
- [Search WAS vulnerabilities](https://developer.tenable.com/reference/was-v2-vulns-search)
- [Tenable Cloud Exposure integration guidance](https://developer.tenable.com/docs/cloud-security-integrations)
- [Rate Limiting](https://developer.tenable.com/docs/rate-limiting)
- [User-Agent Header](https://developer.tenable.com/docs/user-agent-header)
- [VPR v2 transition](https://developer.tenable.com/changelog/vulnerability-priority-rating-transition-to-version-2)
