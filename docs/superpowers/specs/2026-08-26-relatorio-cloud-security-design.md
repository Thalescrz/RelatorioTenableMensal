# Relatório Tenable Cloud Security — Especificação de design

**Data:** 2026-08-26  
**Status:** aprovado para planejamento e implementação  
**Projeto:** RelatorioTenableMensalv2
> **Estado atual (2026-08-27):** este documento preserva a decisão histórica de
> homologar dois modelos. O produto atual gera somente o antigo modelo ampliado,
> agora denominado relatório Cloud padrão, sem seletor ou sufixo de variante. O
> contrato vigente está em `docs/19` a `docs/23`.


## 1. Objetivo

Integrar à aplicação um relatório mensal de Tenable Cloud Security baseado na API
GraphQL do produto. Quando habilitado no perfil do cliente, o relatório Cloud é
iniciado na mesma execução dos relatórios VM, WAS, customizado e por TAG, mas possui
coleta, credencial, progresso, falha e retentativa isolados.

O documento preserva a identidade visual e os textos aprovados do modelo Cloud
fornecido fora do repositório. A primeira validação produz duas versões editoriais
a partir da mesma fotografia Cloud:

1. `Modelo Base`, limitado à estrutura inicialmente aprovada;
2. `Modelo Ampliado`, com indicadores operacionais e seções adicionais.

Depois da revisão visual pelo usuário, somente o modelo escolhido permanece como
padrão. A geração dupla é uma etapa de homologação, não o comportamento definitivo.

## 2. Decisões aprovadas

- O relatório Cloud é opcional por cliente.
- Quando habilitado, ele sempre é iniciado junto com os demais relatórios da
  execução; não exige uma geração manual separada.
- Uma falha Cloud não invalida documentos VM, WAS, customizado ou por TAG. A
  execução termina como `sucesso parcial` e permite retentar somente o componente
  Cloud.
- A credencial Cloud é um segredo próprio de conta de serviço e não reutiliza as
  access/secret keys de VM.
- O token nunca retorna ao navegador, não aparece em argumentos de linha de
  comando e não é registrado em logs.
- A coleta representa uma fotografia do estado Cloud no momento da execução.
- Fotografias mensais compactas e compatíveis alimentam comparações futuras.
- Uma execução histórica sem fotografia preservada não é apresentada como se
  reproduzisse exatamente o fechamento passado.
- O Top 5 de CVEs críticas mantém a tabela-resumo e recebe um subitem detalhado por
  CVE, com pontuações, descrição, remediação e ativos afetados.
- O relatório recebe um Top 10 de vulnerabilidades com correção disponível.
- O tipo de correção usa classificação híbrida: campo explícito quando disponível
  e classificação local determinística quando necessário.
- A origem da classificação é preservada. Ausência de evidência resulta em
  `Não determinado`, sem inferência livre ou texto gerado.
- Textos extensos em inglês podem ser traduzidos em partes, usando o mesmo contrato
  editorial e de cache adotado nos relatórios VM.
- Tabela sem população recebe mensagem explícita; fonte indisponível não é exibida
  como zero.
- O primeiro piloto gera o Modelo Base e o Modelo Ampliado a partir do mesmo
  dataset normalizado e da mesma fotografia.
- Seções de compliance, IAM, exposição pública, Kubernetes, IaC, runtime, malware,
  toxic combinations e caminhos de ataque são condicionais à capacidade realmente
  observada no tenant.

## 3. Limites do escopo

### Incluído

- configuração Cloud por cliente na interface web;
- armazenamento local seguro do segredo;
- teste individual da conexão GraphQL;
- teste de contrato mínimo antes da coleta completa;
- coleta paginada de máquinas virtuais, imagens, vulnerabilidades, inventário,
  findings e ciclo de vida;
- normalização, fotografia mensal e histórico compacto no PostgreSQL;
- geração dos dois DOCX de homologação;
- detalhamento das Top 5 CVEs críticas;
- Top 10 de vulnerabilidades com correção;
- seções padrão operacionais do modelo ampliado;
- módulos condicionais descobertos com segurança;
- progresso, alerta, sucesso parcial e retentativa Cloud na interface;
- descarte dos intermediários pesados depois da publicação validada.

### Não incluído nesta entrega

- reprodução histórica exata sem fotografia Cloud anterior;
- consulta a campos GraphQL não documentados sem teste de contrato;
- geração independente do Cloud fora de uma execução de cliente;
- bloqueio dos demais documentos por falha Cloud;
- armazenamento permanente de respostas GraphQL pesadas;
- ativação automática de módulos não licenciados;
- reconstrução de Attack Path quando a API/tenant não expuser esse contrato;
- envio de dados ou documentos para serviços externos;
- criação de novo texto editorial com estilo generativo.

## 4. Configuração e credenciais

O perfil do cliente passa a distinguir a capacidade Cloud:

```json
{
  "scope": {
    "cloud_security": {
      "enabled": true,
      "environment": "global"
    }
  }
}
```

O segredo permanece em `credentials/<client_id>.env`, ignorado pelo Git:

```dotenv
TCS_API_SECRET=
```

Regras da interface:

- o controle passa a se chamar `Gerar relatório Cloud Security`;
- o campo do token é `password` e só aparece quando Cloud está habilitado;
- campo vazio ao editar preserva o segredo existente;
- o backend nunca devolve o token salvo;
- o card diferencia `Credenciais VM` e `Credencial Cloud`;
- `Testar API Cloud` é separado do teste VM;
- o ambiente comum oferece `Global` e, em configuração avançada, `US Gov`;
- URLs arbitrárias não são aceitas na interface comum.

O conector legado usa `TCS_API_SECRET`, com fallback histórico para
`TCS_API_KEY`. O código integrado grava e documenta somente `TCS_API_SECRET`; o
alias legado permanece restrito à leitura durante a transição.

## 5. Endpoint, autenticação e teste de contrato

A documentação pública atual apresenta um endpoint GraphQL único em `/graphql`,
enquanto o coletor legado validado anteriormente usa `/api/graph`. Nenhum caminho
é substituído apenas por suposição.

O teste de contrato:

1. usa `Authorization: Bearer` sem registrar o segredo;
2. envia `User-Agent` identificável e versionado;
3. tenta somente os endpoints permitidos para o ambiente configurado;
4. executa consultas mínimas com `first: 1`;
5. confirma conexão, paginação e campos essenciais;
6. registra somente capacidades e erros sanitizados;
7. não inicia a coleta completa.

O resultado fica associado ao cliente e à versão do conector. Uma mudança de
endpoint, schema ou credencial invalida a capacidade em cache e exige novo teste.

A coleta segue paginação por cursor com `first`, `after`,
`pageInfo.hasNextPage` e `pageInfo.endCursor`. Consultas de metadados usam páginas
menores que as consultas de vulnerabilidades. O coletor reduz a página quando a
complexidade do tenant exigir, preservando checkpoint e aviso.

Automação respeita no máximo uma coleta Cloud completa por dia. Execuções manuais
podem reutilizar uma fotografia exata; uma nova coleta antes do intervalo seguro é
explicitamente sinalizada e não ocorre silenciosamente.

Referência técnica:
<https://developer.tenable.com/docs/cloud-security-integrations>.

## 6. Fontes de dados

### Obrigatórias

#### Máquinas virtuais

Campos mínimos:

- ID e nome do recurso;
- conta;
- software/componente;
- ID da vulnerabilidade;
- severidade;
- CVSS;
- VPR e severidade VPR.

#### Imagens de contêiner

Além dos campos de vulnerabilidade:

- ID e nome da imagem;
- digest;
- URI do repositório;
- conta.

Falha em uma dessas fontes impede publicar o relatório Cloud, mas não os demais
documentos da execução.

### Enriquecimentos isolados

- `Entities` compute para IPs das máquinas virtuais;
- `Entities` geral para provedor, conta, região, tipo, tags, labels e sync;
- `Findings` para postura, política, categoria, status, datas e remediação;
- `VulnerabilityInstances` para aging e performance de remediação;
- descrição e remediação seletivas para os CVEs que serão publicados.

Cada fonte opcional possui estado `completo`, `parcial`, `indisponível` ou
`não aplicável`. Sua falha não é convertida em conjunto vazio.

### Capacidades condicionais

Depois do teste de contrato, o tenant pode habilitar datasets adicionais:

- compliance e benchmarks;
- identidades sem MFA, inativas ou excessivamente privilegiadas;
- recursos e serviços expostos à internet;
- dados, armazenamentos ou secrets expostos;
- Kubernetes e Infrastructure as Code;
- sinais de runtime ou malware;
- toxic combinations;
- caminhos e técnicas de ataque.

Capacidade de produto não implica disponibilidade pela API do tenant. O relatório
só cria a seção quando o contrato e a população forem comprovados.

## 7. Normalização e identidade

Os dados GraphQL são transformados em modelos de domínio antes de qualquer cálculo
ou renderização. O DOCX não calcula métricas diretamente.

Regras:

- ativo é identificado pelo ID estável retornado pela API;
- nome, IP, URI, digest, conta e região são atributos, não chaves isoladas;
- máquina virtual e imagem possuem tipos distintos;
- CVE é normalizada somente quando o ID satisfaz o padrão esperado;
- uma CVE em vários ativos representa uma vulnerabilidade única e várias
  instâncias vulnerabilidade-ativo;
- findings de configuração, identidade e compliance não são contados como CVEs;
- dados ausentes permanecem ausentes;
- valor numérico zero permanece zero;
- descrições e remediações guardam texto original, tradução e proveniência;
- toda métrica registra fonte, definição e versão.

Fallback por nome pode ser usado apenas para apresentação de IP quando o nome for
único dentro da conta. Ele nunca muda a identidade nem a contagem do ativo.

## 8. Fotografia mensal e período

A fotografia Cloud registra o estado observado no momento da coleta. Ela contém:

- cliente, tenant, run e tentativa;
- instante da coleta e timezone;
- período editorial `[início, fim)`;
- versão do conector, do schema normalizado e das métricas;
- hash de escopo e capacidades observadas;
- contagens de cobertura e rejeição;
- indicadores compactos;
- rankings necessários ao relatório;
- metadados para comparação futura.

Comportamento:

- a execução automática no primeiro dia do mês cria a fotografia associada à
  competência encerrada;
- uma execução manual atual apresenta a data real da fotografia;
- uma reexecução histórica usa fotografia íntegra anteriormente armazenada;
- sem fotografia histórica, o documento informa que os valores representam o
  estado atual e não afirma reconstrução exata;
- somente fotografias `MAIN` imediatamente anteriores e compatíveis entram no
  comparativo;
- períodos incompatíveis, tenants diferentes e versões métricas incompatíveis não
  são combinados.

Dados resolvidos usam `ResolutionTime` dentro de `[início, fim)`. A exposição
aberta é apresentada como estado da fotografia, não como volume descoberto durante
todo o período.

## 9. Estrutura do Modelo Base

O Modelo Base preserva a estrutura inicialmente aprovada:

1. capa;
2. sumário;
3. controle do documento;
4. objetivo;
5. visão geral do ambiente Cloud;
6. introdução;
7. Top 10 de hosts vulneráveis;
8. Top 10 de imagens vulneráveis;
9. Top 5 de CVEs críticas;
10. cinco subitens de detalhamento, quando houver população;
11. Top 10 de vulnerabilidades com correção disponível;
12. dashboard automatizado;
13. conclusão;
14. contracapa.

Datas, período, sumário, títulos e numeração são atualizados automaticamente. A
identidade corporativa, imagens e textos aprovados são preservados. Afirmações
incompatíveis com um DOCX estático, como chamar imagens de interativas, só são
corrigidas mediante revisão editorial explícita.

## 10. Top 5 de CVEs críticas detalhadas

A tabela-resumo mantém:

- ranking;
- CVE;
- VPR;
- severidade;
- componente/produto.

Cada subitem inclui:

- CVE e título identificável;
- VPR, CVSS e severidade;
- componente/produto;
- quantidade de máquinas virtuais e imagens afetadas;
- descrição original e tradução aprovada;
- correção ou contramedida;
- tabela de ativos afetados.

A tabela usa `Ativos afetados`, pois uma CVE pode atingir máquinas virtuais e
imagens. A apresentação diferencia tipo, nome, IP ou repositório/digest, conta,
provedor, região e componente conforme disponibilidade. Para caber em A4 retrato,
identificadores secundários podem ocupar a segunda linha da célula do ativo.

Descrições não são adicionadas a todas as linhas da consulta pesada. O conector
faz enriquecimento seletivo somente para as CVEs efetivamente escolhidas. Campo
documentado indisponível produz mensagem explícita; nunca é inventado.

## 11. Top 10 com correção disponível

Título:

`Principais Vulnerabilidades com Correção Disponível (TOP 10)`

Colunas:

- ranking;
- CVE e componente;
- severidade;
- VPR/CVSS;
- ativos afetados;
- tipo de correção;
- ação recomendada resumida.

Elegibilidade:

- vulnerabilidade aberta e correlacionada a uma CVE;
- remediação não vazia ligada à CVE e ao recurso;
- máquinas virtuais e imagens participam da mesma população;
- findings genéricos de configuração não entram nesta tabela.

Ranking determinístico:

1. VPR informado, decrescente;
2. quantidade de ativos afetados, decrescente;
3. CVSS, decrescente;
4. CVE, crescente.

VPR zero é exibido como `0`. VPR ausente permanece `N/D` e é ordenado depois dos
valores informados. CVEs críticas sem VPR permanecem elegíveis.

### Classificação do tipo de correção

Categorias canônicas:

- `Patch/Atualização`;
- `Upgrade de versão`;
- `Alteração de configuração`;
- `Remoção/Substituição de componente`;
- `Mitigação/Contramedida`;
- `Correção manual`;
- `Não determinado`.

Ordem de decisão:

1. usar tipo explícito e documentado quando a API o fornecer;
2. caso contrário, aplicar regras determinísticas e versionadas sobre o texto das
   etapas de remediação;
3. persistir `api_explicit` ou `local_rule` como proveniência;
4. em conflito ou evidência insuficiente, usar `Não determinado`.

As regras locais não usam modelo generativo. Cada regra possui teste com frases
positivas, negativas e ambíguas. O texto integral fica fora da tabela e pode ser
apresentado abaixo ou em apêndice quando necessário.

## 12. Estrutura do Modelo Ampliado

O Modelo Ampliado contém todo o Modelo Base e acrescenta as seções padrão:

### Resumo da exposição Cloud

- ativos vulneráveis;
- instâncias abertas;
- instâncias críticas e altas;
- CVEs críticas únicas;
- ativos com vulnerabilidade crítica;
- findings de postura críticos e altos;
- exposições abertas há mais de 90 dias;
- mediana de remediação;
- recursos sem tags ou com sync antigo.

### Exposição por tipo de ativo

- máquinas virtuais;
- imagens de contêiner;
- ativos vulneráveis;
- CVEs únicas;
- instâncias por severidade;
- ativos com crítica e respectivo percentual.

### Componentes e produtos em maior risco

- componente/produto;
- críticas e altas;
- vulnerabilidades únicas;
- ativos afetados;
- VPR máximo;
- principais CVEs.

### Postura e configuração Cloud

- política e categoria;
- severidade;
- provedor e conta;
- findings abertos;
- recursos afetados;
- idade do finding mais antigo;
- quantidade com orientação de remediação.

### Aging

Faixas canônicas:

- 0–30 dias;
- 31–60 dias;
- 61–90 dias;
- 91–180 dias;
- acima de 180 dias;
- data indisponível.

Vulnerabilidades usam `FirstScanTime`. Findings usam `OpenTime`, com fallback para
`CreationTime`. As duas fontes permanecem separadas.

### Performance de remediação

- resolvidas no período;
- quantidade com duração válida;
- tempo médio;
- mediana;
- P90 por severidade e total.

A duração é `ResolutionTime - FirstScanTime`, somente quando ambas as datas forem
válidas e a resolução pertencer ao período.

### Inventário e qualidade da cobertura

- provedor;
- conta/subscription/projeto;
- região;
- tipo de recurso;
- total de recursos;
- recursos com e sem tags/labels;
- cobertura de tags/labels;
- sync acima do limite operacional configurado;
- sync indisponível;
- estado de cada fonte opcional.

O limite de sync é uma regra operacional da aplicação e não é apresentado como SLA
da Tenable.

### Evolução mensal

Depois de existirem duas fotografias compatíveis:

- ativos vulneráveis;
- instâncias críticas e altas;
- findings de postura;
- vulnerabilidades resolvidas;
- mediana de remediação;
- novas exposições e exposições encerradas entre fotografias.

Sem histórico suficiente, o bloco recebe mensagem explícita e não cria tendência
artificial.

## 13. Módulos condicionais

O Modelo Ampliado pode receber, sem alterar o Modelo Base:

- compliance e aderência a benchmarks;
- IAM e permissões excessivas;
- exposição pública e serviços acessíveis pela internet;
- Kubernetes e IaC;
- dados sensíveis e secrets;
- runtime e malware;
- toxic combinations;
- caminhos e técnicas de ataque.

Cada módulo declara:

- capacidade do tenant;
- população e filtros;
- cobertura;
- campos usados;
- limitações de licença ou schema;
- mensagem de ausência apropriada.

Módulo não disponível não cria título, página ou gráfico vazio.

## 14. Critérios das métricas

| Seção | População e regra |
|---|---|
| Top 10 hosts | VMs com vulnerabilidades abertas na fotografia; CVE deduplicada por ID do ativo |
| Top 10 imagens | Imagens com vulnerabilidades abertas; deduplicação por ID da imagem |
| Top 5 CVEs críticas | CVEs críticas; VPR, ativos afetados, CVSS e CVE |
| Top 10 com correção | CVEs abertas com remediação correlacionada |
| Componentes em risco | CVEs únicas e ativos afetados por componente/produto |
| Postura Cloud | Findings abertos, sem mistura com vulnerabilidades de software |
| Aging | Vulnerabilidade por `FirstScanTime`; postura por `OpenTime`/`CreationTime` |
| Resolvidas no período | `Resolved=true` e `ResolutionTime` em `[início, fim)` |
| Tempo de remediação | `ResolutionTime - FirstScanTime`, somente datas válidas |
| Inventário | Recursos distintos pelo ID da API |
| Evolução | Fotografias `MAIN` compatíveis do mesmo cliente e tenant |

Os rankings validam primeiro a população e depois a ordenação. Zero legítimo,
ausência de população, campo não coletado e falha de fonte são estados diferentes.

## 15. Filtros e proveniência para validação

Cada tabela pode exibir uma linha curta e opcional com:

- fonte GraphQL;
- população;
- estados e severidades;
- campo temporal;
- instante da fotografia;
- regra resumida de ranking.

O manifesto técnico conserva a versão completa, incluindo:

- consultas e variáveis sem segredo;
- cursores e páginas processadas;
- contagens brutas e normalizadas;
- rejeições, duplicidades e campos ausentes;
- versão das métricas;
- capacidades indisponíveis;
- hashes dos datasets e documentos.

O DOCX não recebe dumps, UUIDs internos desnecessários nem textos técnicos longos.

## 16. Orquestração, progresso e falhas

Quando Cloud está habilitado, a execução do cliente inclui o job Cloud no mesmo
conjunto. O progresso informa, no mínimo:

- validação de credencial;
- teste de contrato ou capacidade em cache;
- máquinas virtuais;
- imagens;
- inventário;
- findings;
- ciclo de vida;
- normalização;
- fotografia;
- Modelo Base;
- Modelo Ampliado;
- validação e publicação.

Falhas obrigatórias:

1. não publicam DOCX Cloud incompleto;
2. registram erro estruturado e sanitizado;
3. preservam checkpoints íntegros;
4. não interrompem os demais produtos;
5. marcam a execução como `PARTIAL_SUCCESS` ou equivalente vigente;
6. permitem `Tentar Cloud novamente`.

A retentativa reaproveita respostas e fotografia íntegras. Ela não repete VM, WAS,
customizado ou TAG. Uma nova chamada externa só ocorre para a fonte ausente ou
inválida.

## 17. Persistência e retenção

O PostgreSQL registra:

- configuração de capacidade sem segredo;
- resultado e validade do teste de contrato;
- tentativas e erros Cloud;
- fotografia compacta;
- compatibilidade e referência `MAIN`;
- indicadores e rankings históricos;
- documentos publicados e seus hashes;
- estado das fontes opcionais;
- proveniência da classificação de correção.

Respostas GraphQL completas, cache de tradução, imagens temporárias e datasets
detalhados ficam no staging da execução. A limpeza só ocorre depois de:

1. validação do dataset;
2. geração dos documentos;
3. renderização e validação dos DOCX;
4. persistência da fotografia;
5. registro dos hashes e manifesto;
6. confirmação da publicação.

Falha antes desses passos preserva os intermediários necessários à retentativa,
respeitando limites de disco e a política geral de retenção.

## 18. Publicação dos dois modelos de homologação

Nomes sugeridos:

```text
[CLIENTE] Relatório Tenable Cloud Security [PERÍODO] - MODELO BASE.docx
[CLIENTE] Relatório Tenable Cloud Security [PERÍODO] - MODELO AMPLIADO.docx
```

O manifesto comparativo informa, sem dados sensíveis:

- seções presentes em cada modelo;
- tabelas sem população;
- módulos disponíveis e indisponíveis;
- quantidade de páginas;
- tempo de geração;
- alertas de cobertura;
- hash da fotografia comum.

Os dois documentos devem ter os mesmos valores para toda métrica compartilhada.
Depois da decisão do usuário, a configuração de produção aponta para um único
layout e a geração duplicada é desativada.

## 19. Segurança e privacidade

- segredos ficam somente em arquivos locais ignorados pelo Git;
- token não aparece no perfil JSON, banco, frontend, log, manifesto ou subprocesso;
- respostas de erro passam pela sanitização vigente;
- fixtures, snapshots versionados e provas usam dados fictícios;
- nomes, IPs, contas, regiões, repositórios e tags reais não são registrados em
  documentação versionada;
- downloads e rotas validam o `client_id` e o documento solicitado;
- consultas são somente leitura;
- coleta real depende de autorização explícita e cliente selecionado.

## 20. Testes e critérios de aceite

### Configuração e segredo

- habilitar Cloud exige presença ou preservação do token;
- editar sem preencher o token não o apaga;
- respostas web nunca contêm o segredo;
- o teste Cloud não usa as chaves VM;
- mensagens de autenticação são específicas e sanitizadas.

### Cliente GraphQL

- endpoint e ambiente são validados por contrato;
- paginação usa cursor até `hasNextPage=false`;
- página repetida, cursor ausente e resposta parcial produzem erro acionável;
- timeout e rate limit são retentáveis conforme política;
- campos opcionais negados não interrompem as fontes obrigatórias;
- `User-Agent` é enviado.

### Normalização e métricas

- identidades usam IDs da API;
- VPR zero e VPR ausente permanecem distintos;
- CVE por ativo é deduplicada;
- VM e imagem não colidem;
- findings de postura não entram nas contagens CVE;
- rankings seguem desempate determinístico;
- resolvidas respeitam `[início, fim)`;
- aging e duração tratam datas ausentes e inválidas;
- módulo indisponível não vira zero.

### Correção e classificação

- somente remediação correlacionada torna a CVE elegível;
- tipo explícito prevalece;
- regras locais são determinísticas e versionadas;
- conflitos resultam em `Não determinado`;
- a origem da classificação é persistida;
- texto longo não rompe a tabela.

### Histórico

- fotografia é vinculada a cliente, tenant, competência e versão;
- somente `MAIN` compatível entra no comparativo;
- sem predecessor, o relatório corrente continua válido;
- reexecução exata pode reutilizar fotografia;
- execução histórica sem fotografia recebe aviso de reconstrução impossível.

### Orquestração e interface

- Cloud habilitado entra automaticamente na execução do cliente;
- falha Cloud preserva os demais documentos;
- status geral indica sucesso parcial;
- card mostra progresso e erro Cloud;
- retentativa Cloud não repete os demais produtos;
- `Gerar todos` respeita a configuração individual de cada cliente.

### Documentos

- Modelo Base e Ampliado usam a mesma fotografia;
- métricas compartilhadas são idênticas;
- Top 5 cria até cinco subitens, sem seções vazias;
- Top 10 com correção contém tipo e ação resumida;
- ausência de população recebe mensagem editorial;
- módulos indisponíveis não deixam páginas vazias;
- sumário, cabeçalhos, rodapés, datas e numeração são válidos;
- todas as páginas são renderizadas com LibreOffice e inspecionadas;
- o template original não é sobrescrito.

### Regressão

- relatório-base VM continua geral e não filtrado por TAG;
- WAS continua opcional e tolerante a falha;
- customizado e relatórios por TAG mantêm o comportamento vigente;
- exclusão, download, `MAIN`, retenção e orquestração multicliente continuam
  funcionando;
- clientes sem Cloud habilitado não fazem chamadas GraphQL nem criam documentos
  Cloud.

## 21. Estratégia de homologação

1. criar fixtures GraphQL fictícias cobrindo fontes completas, parciais e ausentes;
2. implementar o teste de contrato e validar sem coleta completa;
3. executar testes unitários, integração e regressão;
4. autorizar uma coleta piloto em cliente selecionado;
5. produzir uma fotografia única e os dois DOCX;
6. renderizar ambos com LibreOffice e revisar página a página;
7. reconciliar uma amostra de métricas com a plataforma;
8. apresentar os dois modelos e o manifesto comparativo;
9. registrar quais seções e tabelas formarão o padrão;
10. desativar a geração dupla e versionar o template Cloud escolhido.

Nenhuma coleta completa é iniciada apenas para validar código ou documentação.

## 22. Documentação oficial consultada

- Tenable Cloud Exposure — integrações GraphQL, paginação, campos, polling e casos
  de uso: <https://developer.tenable.com/docs/cloud-security-integrations>.
- Data Requirements for Attack Path — dependências CSPM, CIEM e CWP:
  <https://docs.tenable.com/exposure-management/Content/attack-path/data-requirements.htm>.
- Cloud Security scoring — contexto de exposição, vulnerabilidades e permissões:
  <https://docs.tenable.com/quick-reference/scoring-explained/Content/cloud-security.htm>.
- Cloud Resources scoring — categorias de exposição e configuração:
  <https://docs.tenable.com/quick-reference/scoring-explained/Content/CloudResources.htm>.

