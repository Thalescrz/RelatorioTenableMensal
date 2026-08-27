# Design da solução

**Status:** arquitetura vigente em 2026-08-23  
**Escopo:** decisões estruturais que devem permanecer estáveis enquanto o produto
evolui. Detalhes operacionais ficam nos guias em `docs`.

## Contexto

A solução transforma dados Tenable em relatórios Word mensais para clientes com
necessidades diferentes. Ela precisa preservar um padrão editorial manual,
reconciliar números auditáveis, trabalhar com exports assíncronos e evitar retenção
permanente de grandes volumes de dados brutos.

O sistema é local, orientado a uma carteira de clientes e operado principalmente
por uma interface web simples. PostgreSQL mantém estado durável; Tenable continua
sendo a origem dos achados.

## Objetivos de design

- uma coleta geral reproduzível por cliente e período;
- métricas calculadas uma vez e reutilizadas por todos os documentos;
- relatórios gerais independentes de qualquer seleção de TAG;
- histórico determinístico com referência explícita;
- recuperação segura de exports longos sem repetir chunks válidos;
- módulos opcionais que não prejudiquem a entrega principal;
- uso temporário, não acumulativo, de dados pesados;
- rastreabilidade entre execução, dataset, documento e histórico;
- proteção de secrets e dados identificáveis.

## Fora do escopo atual

- autenticação multiusuário ou exposição pública da interface;
- distribuição remota automática dos documentos;
- coleta funcional de Cloud Security;
- provedor externo de tradução automática;
- comparação de uma TAG contra outra;
- uso de IP ou hostname como identidade técnica.

## Invariantes

1. O intervalo temporal é `[início, fim)` no fuso do cliente.
2. `OPEN` e `REOPENED` usam `last_found`; `FIXED` usa `last_fixed`.
3. Severidade `Informational` não participa dos relatórios atuais.
4. Ativo e finding são vinculados por UUID.
5. A coleta e os dois relatórios gerais abrangem todo o ambiente elegível.
6. Relatório por TAG é um recorte local do dataset VM geral.
7. Comparativo de TAG usa a mesma categoria e o mesmo valor em outro período.
8. `Exploitable` geral e flags por framework são conceitos separados.
9. WAS é opcional e não pode bloquear uma entrega VM válida.
10. Documento só é publicado depois de validação e registro consistentes.
11. Staging só é removido depois que DOCX e histórico compacto estão seguros.
12. Texto editorial aprovado não é reescrito sem decisão explícita de produto.

## Arquitetura em camadas

```text
CLI / Webapp
     |
     v
Application services
     |
     +------> Domain models and policies
     |
     +------> Infrastructure adapters
     |
     +------> Presentation renderers
```

### Domínio

Contém identidade, período, modelos normalizados, fingerprints, compatibilidade
histórica e contratos de datasets. Não conhece HTTP, PostgreSQL, interface ou Word.

### Aplicação

Coordena os casos de uso: coleta, normalização, criação do dataset, inteligência,
recortes por TAG, histórico, publicação, registro e retenção. É responsável pela
ordem das transações de negócio, não pelos detalhes de transporte.

### Infraestrutura

Implementa os clientes Tenable VM/WAS, armazenamento JSONL e repositórios
PostgreSQL. Respostas externas são convertidas para contratos internos antes de
chegarem às regras de negócio.

### Apresentação

Transforma datasets prontos em DOCX, tabelas, gráficos, filtros de validação e nomes
de arquivos. Renderizadores não fazem novas consultas e não recalculam métricas.

### Interface

A CLI oferece operações compostas e ferramentas administrativas. A webapp local é
a experiência preferida do analista e reutiliza os mesmos casos de uso.

## Fluxo de dados

```text
perfil + credenciais + período
              |
              v
       ativos + TAGs + VM -----> WAS opcional
              |                       |
              +-----------+-----------+
                          v
                    normalização
                          |
                          v
                  dataset mensal geral
                   /        |         \
                  v         v          v
           base DOCX   custom DOCX   recortes TAG
                  \         |          /
                   +--------+---------+
                            v
                 registro + MAIN + histórico
                            |
                            v
                    limpeza do staging
```

A separação entre dataset e apresentação permite validar números antes de abrir o
Word e garante que relatórios diferentes expliquem a mesma população.

## Decisões principais

### Uma coleta geral, vários consumidores

Não são criados exports completos adicionais para cada TAG. Ativos e findings são
coletados uma vez; os UUIDs associados às TAGs recortam localmente o dataset. Isso
reduz tempo, chamadas, risco de rate limit e inconsistência entre documentos.

### Corte temporal local

O parâmetro inferior do export reduz o universo, mas não garante o fim histórico.
Por isso a aplicação aplica localmente as duas fronteiras ao campo temporal correto
de cada estado. Essa decisão evita que uma execução posterior contamine o mês
solicitado.

### Exports retomáveis

Cada chunk disponível é persistido imediatamente com manifesto parcial. Uma nova
tentativa reaproveita chunks íntegros. O estado remoto `FINISHED`, e não a quantidade
de chunks anunciada, determina conclusão.

O cancelamento automático só alcança job criado pela execução corrente e sem
progresso. Jobs retomados, fornecidos ou preexistentes exigem decisão humana.

### Propriedades seletivas com prova de equivalência

Payload seletivo é uma otimização por tenant, não um pressuposto global. Antes de
ativá-lo, uma validação compara o resultado com export completo. HTTP 400 ou contrato
incompleto permite um fallback completo; falhas operacionais continuam visíveis.

### Histórico compacto e `MAIN`

PostgreSQL guarda agregações, fingerprints, documentos, tentativas e a referência
canônica. O comparativo consulta o `MAIN` anterior compatível, não o arquivo de nome
mais recente. Reexecuções coexistem até promoção explícita.

### Staging efêmero

Raw, snapshots completos, normalizados, datasets e imagens existem para montar e
diagnosticar uma execução. Após sucesso validado, são descartados. DOCX e histórico
compacto permanecem. Falhas conservam staging por janela curta para retomada.

### Módulos opcionais isolados

WAS possui coleta e normalização próprias. Ausência de licença, permissão ou achados
vira aviso e mensagem editorial, não falha do núcleo VM. O mesmo padrão deve orientar
futuras capacidades opcionais.

## Estados importantes

### Export VM

```text
created/reused -> queued -> processing -> finished
                         \-> temporary failure -> retry/resume
                         \-> confirmed cancel
```

Fila e processamento têm limites separados. Progresso remoto ou chunk persistido
impede que o job seja tratado como completamente parado.

### Execução do cliente

```text
planned -> running -> succeeded
                   \-> partial failure
                   \-> failed retryable
                   \-> failed permanent
```

Falha WAS pode produzir sucesso VM com alerta. Falha de publicação não autoriza
limpeza dos dados necessários à recuperação.

### Documento

```text
rendered -> validated -> registered -> available
                                \-> MAIN (automático ou promovido)
```

Exclusão é explícita e auditável. A remoção de um `MAIN` precisa considerar a base
do próximo comparativo.

## Persistência

O esquema PostgreSQL evolui por migrations incrementais. Uma migration aplicada não
é reescrita. A identidade do histórico inclui cliente, tenant, período, modo,
timezone, versão métrica e hash de escopo; documentos por TAG acrescentam categoria
e valor.

Arquivos em `data` não são a autoridade exclusiva do histórico. Eles são artefatos
publicados ou staging catalogado, conforme o tipo.

## Segurança e privacidade

- secrets permanecem em arquivos locais ignorados pelo Git;
- perfis JSON nunca contêm chaves;
- endpoints não devolvem secrets ao navegador;
- logs usam identificadores operacionais e evitam evidências sensíveis;
- fixtures e documentos versionados são sanitizados;
- `Plugin Output`, provas e payloads são opt-in e tratados como sensíveis;
- ações destrutivas exigem alvo explícito e confirmação.

## Desempenho e capacidade

As otimizações preferidas são, nesta ordem:

1. evitar exports duplicados;
2. cortar corretamente o período;
3. persistir e retomar chunks;
4. ajustar chunk dentro do contrato da API;
5. validar propriedades seletivas por tenant;
6. reciclar staging depois do sucesso.

Concorrência entre muitos clientes deve ser limitada pela orquestração. Velocidade
não pode sacrificar cobertura, contrato temporal ou rastreabilidade.

O projeto legado `RelatorioTenableITP` obtinha respostas mais rápidas por meio de
consultas síncronas já filtradas e agregadas no servidor. Esse padrão permanece uma
referência para futuros modelos de leitura e validação, não uma fonte canônica: seu
uso exige paginação comprovada e equivalência com o snapshot completo. A análise de
vantagens e limitações está no
[catálogo de APIs](docs/02-catalogo-apis-tenable.md#base-técnica-legada--consultas-projetadas-do-relatoriotenableitp).

## Observabilidade

Eventos estruturados devem permitir responder:

- qual cliente, execução e tentativa estão ativos;
- qual etapa está em andamento;
- qual UUID de export e sua origem;
- quantos chunks foram persistidos;
- quando houve o último progresso;
- qual falha ocorreu e se é retentável;
- quais documentos foram publicados e qual virou `MAIN`.

Mensagens para o analista devem ser acionáveis e não expor detalhes secretos.

## Estratégia de testes

- domínio: testes unitários determinísticos;
- aplicação: casos de uso com relógios e repositórios falsos;
- infraestrutura: transportes simulados e fixtures sanitizadas;
- PostgreSQL: migrations e repositórios em ambiente de teste explícito;
- apresentação: estrutura do DOCX mais renderização visual;
- webapp: rotas, payloads sanitizados e confirmações;
- documentação/skills: presença, frontmatter e links locais.

Chamadas reais servem para validação autenticada controlada, não para a suíte
automática.

## Pontos de extensão

### Cloud Security

Deve entrar como adaptador e normalizador próprios, com capacidade explícita no
perfil e falha isolada. Não reutilizar um finding VM como se fosse finding cloud.

Quando habilitado, publica um único DOCX padrão. O valor técnico `expanded` é
mantido no registro somente para compatibilidade; `base` e `comparison` não criam
novos modelos e são normalizados ao carregar perfis legados. A apresentação inclui
o Top 5 detalhado no padrão tipográfico do relatório geral, Top 10 corrigível sem a
coluna de ação extensa, marcador manual em 3.6.2 e evolução mensal em 3.11. O
inventário Cloud não é seção do documento. Uma série sem histórico anterior usa
somente o ponto real da fotografia atual e declara que não há comparação temporal.

### Tradução

Deve operar sobre blocos de texto editorialmente permitidos, respeitar limite do
provedor e nunca enviar Output, prova, payload ou identificadores sensíveis. Cache e
versão da tradução precisam preservar reprodutibilidade.

### Interface multiusuário

Exigirá autenticação, autorização por cliente, proteção CSRF, gestão central de
segredos e trilha de auditoria mais forte. O servidor local atual não deve ser
exposto diretamente.

## Critério para mudar o design

Uma mudança estrutural deve declarar:

1. invariante afetada;
2. contrato de dados e migração;
3. impacto nos quatro tipos de relatório;
4. comportamento sem histórico ou capacidade opcional;
5. plano de compatibilidade e rollback;
6. testes automatizados e prova visual necessária;
7. atualização deste arquivo e dos guias vigentes.

Para detalhes complementares, consulte [arquitetura e fluxo de dados](docs/20-arquitetura-e-fluxo-de-dados.md),
[catálogo de dados e métricas](docs/21-catalogo-de-dados-e-metricas.md) e
[guia de desenvolvimento](docs/23-guia-de-desenvolvimento.md).
