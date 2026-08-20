# Histórico, regras críticas e tradução

**Status:** contrato funcional para orientar a implementação  
**Princípio:** toda tabela deve declarar população, grão, filtros, definição e comportamento de ausência

## 1. Histórico e identificação do cliente

O sistema não deve “adivinhar” um cliente pelo nome do arquivo Word. A execução é iniciada com um `client_id` opaco e estável, associado a tenant, credencial referenciada, escopos e perfil de módulos. Nomes de exibição e contatos são metadados separados e sensíveis.

### Registro mínimo de snapshot

```text
Snapshot
  snapshot_id
  run_id
  client_id
  tenant_id
  period_start / period_end / cutoff_at
  source_systems[]
  scope_hash
  metric_definition_version
  normalization_schema_version
  collector_versions{}
  completed_at / published_at
  completeness_by_source{}
  raw_manifest_uri / normalized_manifest_uri
  checksums{}
```

Além dos agregados usados no Word, devem ser preservadas instâncias normalizadas suficientes para reconstruir estados, ressurgimento, aging, hosts afetados e movimentos entre períodos. Guardar apenas uma planilha final impediria auditoria e comparativos confiáveis.

### Seleção do relatório anterior

O predecessor é o snapshot com maior `period_end` anterior ao período corrente que satisfaça simultaneamente:

- mesmo `client_id` e `tenant_id`;
- mesmo `scope_hash`;
- mesma versão de definição métrica e grão;
- fontes necessárias completas;
- publicação aprovada;
- período estritamente anterior.

Preferir período imediatamente adjacente. Se houver lacuna, comparar apenas quando semanticamente válido e rotular a distância. Se o escopo ou a definição mudou, bloquear o delta ou aplicar migração explícita; a diferença não pode ser apresentada como evolução operacional.

### Armazenamento recomendado

Começar com **SQLite** atrás da interface `SnapshotRepository`, e não com CSV como fonte primária. SQLite oferece transação, unicidade, índices, seleção segura do predecessor e migrações; CSV permanece como formato de importação/exportação e ponte para planilhas existentes. A interface permite migrar depois para PostgreSQL sem alterar métricas nem apresentação.

Artefatos brutos grandes podem ficar em armazenamento de arquivos/objetos com checksum e criptografia; o banco registra manifesto e metadados. Não guardar segredos de API nos snapshots.

### Primeiro mês e legado

- Sem predecessor: “sem histórico comparável”; deltas e setas ficam ausentes.
- CSV ou relatório legado importado: registrar origem, versão do importador, granularidade e nível de confiança.
- Dados legados agregados podem alimentar uma série simples, mas não reconstituem hosts ou transições que não foram preservados.

## 2. Principais ativos vulneráveis — coluna `Exploitable`

A última coluna da tabela-base será `Exploitable`.

### Semântica

- Conta instâncias **não mitigadas** (`OPEN`/`REOPENED`) do ativo com evidência de exploit conhecida.
- Usa exatamente a mesma população, período, escopo, deduplicação e grão das colunas de severidade e de `Total`.
- É um subconjunto de `Total`; portanto, **não é somada ao Total**.
- Deduplica pela identidade do finding, não pelo número de CVEs nem apenas por plugin.
- Se a fonte de exploitabilidade não tiver sido coletada ou validada, exibe indisponível e falha o gate da tabela; não converte ausência em zero.

### Sinal proposto

No contrato normalizado:

```text
finding.exploitable =
    plugin.exploit_available == true
```

VPR alto, CVSS alto, presença de CVE, vetor `AV:N`, sinal de malware,
`exploitability_ease` ou flag de framework não substituem o indicador direto. Os
frameworks são contabilizados separadamente na matriz própria e uma vulnerabilidade
pode pertencer a mais de um deles.

### Invariantes

- `0 <= Exploitable <= Total` por ativo.
- Soma por severidade = `Total`, se `Total` for definido como soma das quatro severidades exibidas.
- A seleção dos dez ativos usa uma regra versionada e desempate estável.
- Qualquer ativo sem identidade estável é separado em qualidade de dados, não mesclado silenciosamente pelo nome/IP.

## 3. Top 5 VM não mitigadas

Esta seção é crítica e deve ser gerada de modo determinístico a partir dos mesmos dados da tabela “Vulnerabilidades Não Mitigadas”.

### População e agrupamento

1. Filtrar findings correntes `OPEN`/`REOPENED` no escopo do relatório.
2. Deduplicar instâncias pela chave estável `asset.uuid + plugin.id + port.port + port.protocol`.
3. Agrupar por `plugin.id`.
4. Calcular `affected_assets = count(distinct asset.id)` e `finding_instances`.

### Ordenação proposta

1. VPR decrescente;
2. severidade decrescente;
3. exploitável primeiro;
4. quantidade de ativos afetados decrescente;
5. maior idade decrescente;
6. `plugin.id` crescente como desempate estável.

O Top 5 detalhado e a tabela resumida devem usar o mesmo resultado materializado. Se divergirem, a publicação é bloqueada.

### Campos publicados

- Plugin ID e nome;
- família;
- severidade e VPR com versão do modelo;
- descrição em português e original preservado no snapshot;
- solução, workaround/contramedida e links;
- CVEs quando disponíveis;
- tabela de hosts: `Asset Name`, `IP`, `Port`, `Protocol`;
- `Output` somente se habilitado.

Hosts são obtidos das instâncias que compõem o grupo. Um plugin não pode receber hosts vindos de outra população, de tabela antiga ou de busca textual.

## 4. Top 5 WEB padronizado

O Top 5 WEB passa a integrar o DOCX-base sempre que WAS estiver disponível.

### População e ranking

- Achados WAS abertos no período e no conjunto de aplicações definido pelo perfil.
- Identidade e deduplicação conforme o contrato WAS validado; URI/aplicação não deve ser reduzida a IP.
- Agrupar por plugin/definição e classificar por VPR, severidade, exploitabilidade quando disponível, quantidade de aplicações, quantidade de instâncias e idade, com ID estável no desempate.

### Campos

- Plugin ID, nome, família, severidade, VPR;
- descrição, solução/contramedida e referências;
- aplicações e URIs afetadas, respeitando política de mascaramento;
- `Plugin Output` opcional e desligado por padrão.

Ausência de WAS deve gerar status tipado `NOT_LICENSED`, `NOT_CONFIGURED`, `SOURCE_FAILED` ou `NO_DATA`, e não uma seção vazia sem explicação.

## 5. Tradução fragmentada de textos longos

O objetivo não é traduzir apenas um trecho: é obter a tradução integral sem ultrapassar o limite de caracteres do mecanismo.

### Campos traduzíveis

Nome/título quando útil, synopsis, description, solution e workaround. Identificadores, caminhos, CVEs, KBs, URLs, comandos, hashes, versões, portas e nomes de produto ficam intactos. `Plugin Output` não é traduzido por padrão.

### Pipeline

1. Normalizar quebras e detectar idioma.
2. Proteger tokens técnicos com placeholders determinísticos.
3. Dividir semanticamente por parágrafos e sentenças; se ainda exceder o limite, dividir por cláusulas.
4. Reservar margem de segurança para instruções/metadados do provedor.
5. Traduzir fragmentos numerados com contexto mínimo comum, sem incluir dados de cliente/host.
6. Restaurar placeholders e remontar na ordem original.
7. Validar completude e linguagem; repetir apenas fragmentos que falharam.
8. Armazenar original, tradução, hashes, mecanismo/modelo e versão do prompt.

Chave de cache sugerida:

```text
sha256(normalized_source + source_lang + target_lang + engine + model + prompt_version)
```

### Gates de qualidade e privacidade

- mesma quantidade e ordem de placeholders antes/depois;
- nenhum fragmento ausente, duplicado ou vazio;
- URLs, CVEs, KBs, comandos e versões preservados;
- tradução final em português;
- texto original sempre recuperável;
- PII, hostname, IP, e-mail, URI interna e Output nunca enviados a serviço externo;
- se a tradução integral falhar, sinalizar `TRANSLATION_FAILED`; não publicar meia tradução como se estivesse completa.

Um mecanismo local pode ser usado para ambientes que proíbem saída de texto. Se houver fornecedor externo, ele precisa ser aprovado quanto a retenção, residência e treinamento com os dados.

## 6. Output opcional e segurança

`Output`/`Plugin Output` pode conter caminhos, versões, nomes internos, payloads, trechos binários, tokens e evidência explorável. A opção default é `false` nos módulos VM e WAS.

Quando ativado:

- autorização explícita registrada no manifesto da execução;
- sanitização por regras versionadas e redaction de segredos;
- limite por célula e por documento;
- caracteres de controle removidos;
- conteúdo não vai para logs nem tradutor;
- classificação visual de confidencialidade;
- preferência por anexo técnico protegido se o volume for grande;
- QA visual em página paisagem ou tabela dedicada.

## 7. Regras de qualidade temporal

- Datas normalizadas em UTC e apresentadas no fuso configurado.
- Intervalos são fechados/abertos de forma explícita; por exemplo `[period_start, period_end)`.
- O fluxo automático usa o mês-calendário anterior completo e deve rodar no primeiro dia do mês.
- O fluxo manual padrão usa um mês-calendário móvel até a execução; o analista também pode fornecer últimos `N` dias ou `[início, fim)` explícito.
- Artefatos automáticos e manuais ficam em raízes separadas e registram o tipo de execução no manifesto.
- Filtros remotos reduzem o volume, mas o fim exclusivo do período é reaplicado localmente.
- Um ativo só entra na população mensal com evidência de scan ou finding no período; todas as demais decisões mantêm um motivo reconciliável.
- Aging usa `first_seen` válido; valores futuros/ausentes vão para qualidade de dados.
- “Mitigado no período” usa estado e data de correção, não simplesmente a ausência no snapshot atual.
- Na Fase 4, “ressurgido” exige `state=REOPENED` e `resurfaced_at` no período; a futura série histórica poderá validar a transição também contra o predecessor.
- Coleta antes do fechamento é erro; coleta depois da tolerância é aviso de deriva histórica.
- Alterações da Tenable, como versões de VPR, entram em `metric_definition_version` e podem tornar períodos não comparáveis.

## 8. Interfaces que preservam evolução futura

```text
ClientProfileRepository
SourceCollector (VM, WAS, Cloud)
Normalizer
SnapshotRepository (SQLite -> PostgreSQL)
MetricEngine
ModuleRegistry
TranslationProvider
RedactionPolicy
DocumentRenderer (base, intelligence)
PublicationGate
```

A CLI, uma aplicação web e uma aplicação desktop devem chamar os mesmos casos de uso. A interface não contém regra métrica; apenas seleciona cliente, período, escopo, módulos e opções seguras.

## 9. Fontes oficiais que sustentam o contrato

- [Export vulnerabilities](https://developer.tenable.com/reference/exports-vulns-request-export)
- [Status do export VM](https://developer.tenable.com/reference/exports-vulns-export-status)
- [Download de chunk VM](https://developer.tenable.com/reference/exports-vulns-download-chunk)
- [Integrações VM e WAS](https://developer.tenable.com/docs/vm-and-was-integrations)
- [Export de ativos v2](https://developer.tenable.com/reference/export-assets-v2)
- [Detalhes de plugin](https://developer.tenable.com/reference/io-plugins-details)
- [Atributos de plugin](https://developer.tenable.com/docs/tenable-plugin-attributes)
- [Formato dos arquivos exportados](https://developer.tenable.com/docs/export-file-formats)
- [Export WAS findings](https://developer.tenable.com/reference/was-export-findings)
- [Busca de vulnerabilidades WAS](https://developer.tenable.com/reference/was-v2-vulns-search)
- [Detalhes de vulnerabilidade WAS](https://developer.tenable.com/reference/was-v2-vulns-details)
- [Rate limiting](https://developer.tenable.com/docs/rate-limiting)
- [Transição para VPR v2](https://developer.tenable.com/changelog/vulnerability-priority-rating-transition-to-version-2)
