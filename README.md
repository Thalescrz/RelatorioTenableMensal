# Relatórios Tenable

Aplicação local para coletar dados da Tenable, normalizá-los, manter histórico
compacto e gerar relatórios mensais em Word para uma carteira de clientes.

O projeto separa coleta, regras de negócio e apresentação para que os mesmos dados
possam alimentar quatro tipos de documento:

1. relatório-base geral, com o conteúdo editorial comum, Top 5 VM e Top 5 WEB;
2. relatório geral de inteligência e customizações, com módulos atuais e históricos;
3. relatório operacional compacto por TAG, opcional, com comparativo temporal da
   própria TAG quando habilitado;
4. relatório Tenable Cloud Security, opcional por cliente, em um único modelo
   padrão completo.

As TAGs nunca filtram os dois relatórios gerais. A coleta VM geral acontece uma vez
e os relatórios por TAG são recortes locais por UUID dos ativos.

## Começar

No Windows, abra o PowerShell na raiz do projeto:

```powershell
.\scripts\setup.ps1
.\scripts\bootstrap_postgresql.ps1
.\scripts\run_web.ps1
```

O painel abre em `http://127.0.0.1:8765`. Nele é possível cadastrar clientes,
manter o catálogo operacional de analistas responsáveis, testar APIs, buscar TAGs,
iniciar uma geração individual ou selecionar explicitamente os clientes da
carteira, acompanhar as fases, baixar documentos e escolher a referência `MAIN`
usada no próximo comparativo. Também é possível baixar um conjunto completo em ZIP
ou montar o ZIP mensal da carteira usando somente o `MAIN` de cada cliente.

As credenciais ficam somente em `credentials/*.env`, arquivos ignorados pelo Git.
Use os exemplos em [credentials](credentials) como referência; nunca grave chaves
nos perfis JSON, documentação, logs, testes ou commits.

Cloud Security usa uma credencial independente: `TCS_API_SECRET`. O token é salvo
localmente pelo formulário do cliente, nunca retorna ao navegador e não reutiliza
as chaves de VM.

Em **Gerenciar clientes → Coleta VM**, cada cliente pode definir estratégia de
export, tamanho de chunk, propriedades seletivas e fonte histórica. O padrão é
export combinado, 1000 ativos por chunk, propriedades desativadas e export VM
tradicional. Para período fechado sem snapshot compacto exato, a opção
**Inventory Findings · beta** exige confirmação e marca o resultado como
**HISTÓRICO RECONSTRUÍDO**; execuções automáticas e janelas atuais continuam no
export tradicional.

Ao gerar manualmente, **Forçar nova coleta pela API** ignora somente o replay de
um snapshot compacto exato e cria novos jobs para aquela execução. Esse controle
não ativa propriedades seletivas nem muda sozinho a rota de coleta configurada no
cliente. O snapshot anterior é preservado e uma retentativa mantém a intenção de
coleta nova.

## Períodos

- Automático mensal: executado no primeiro dia do mês para o mês-calendário anterior completo.
- Manual padrão: um mês móvel até o instante da execução.
- Manual personalizado: últimos `X` dias ou intervalo explícito escolhido pelo analista.

No período explícito, a interface recebe datas de calendário inclusivas. Por
exemplo, 01/07 a 31/07 é convertido internamente para
`[01/07 00:00, 01/08 00:00)`, preservando o dia final inteiro.

Os intervalos internos são tratados como `[início, fim)`. Findings ativos usam a
data de última identificação; findings mitigados usam a data da correção. A
severidade informativa não faz parte do relatório.

## Dados e armazenamento

PostgreSQL é a fonte operacional do histórico, do registro de documentos, das
tentativas e da referência `MAIN`. Os DOCX publicados e o histórico compacto são
duráveis. Raw, snapshots, normalizados e datasets intermediários são temporários:
após uma publicação validada eles são removidos; uma falha os preserva por tempo
limitado para diagnóstico.

O WAS e o Cloud Security são componentes opcionais e independentes. Falha, ausência
de licença ou ausência de população em um deles não invalida os documentos VM já
gerados. O Cloud registra uma fotografia atual compacta no PostgreSQL e mantém o
staging quando uma retentativa isolada ainda pode aproveitá-lo. Quando um tradutor
é injetado, descrições longas são divididas semanticamente e uma falha preserva
somente o trecho fonte afetado; provedor externo continua sem integração
automática.

No botão **Gerar todos** e no automático mensal, uma falha WAS inicia uma única
retentativa apenas do componente WEB. Se ela também falhar, os relatórios são
publicados sem WAS e recebem o alerta `WAS_RETRY_EXHAUSTED`; VM, assets, TAG e
Cloud não são repetidos. A geração manual individual continua oferecendo ao
analista a decisão entre tentar novamente ou continuar sem WEB.

## Controle durável da carteira

Novos lotes usam o modelo `STAGED_V1`: coletas remotas de clientes diferentes
podem ocorrer em paralelo, enquanto a normalização final, a montagem dos DOCX e a
publicação passam por um único worker local. A capacidade remota automática é
limitada ao menor valor entre clientes elegíveis e 64; a montagem permanece em 1.
Lotes antigos continuam como `LEGACY`. Reiniciar a interface não transforma
trabalho preservado em nova coleta: o painel reconcilia a fase e o checkpoint.

No painel do lote:

- **Gerar todos** sempre abre a seleção de clientes; lotes pausados ou concluídos
  não bloqueiam novas solicitações. Somente um trabalho ativo do mesmo cliente é
  destacado como conflito;
- no conflito, o analista pode excluir o cliente da nova solicitação ou usar
  **Parar execução atual**. A parada é individual, preserva export, UUID, chunks e
  checkpoint remotos, e não interfere nos demais clientes do lote;
- **Pausar após o atual** impede novas reivindicações e deixa as fases ativas
  salvarem seu checkpoint;
- **Parar lote** sinaliza cooperativamente todos os processos locais ativos,
  cancela os ainda não iniciados e preserva export, UUID e chunks remotos;
- se o lote estiver aguardando uma decisao WAS sem processo local ativo, a parada
  e concluida imediatamente; pedidos abandonados sao reconciliados no reinicio;
- **Retomar lote** libera somente trabalhos que permanecem retomáveis na própria
  fase;
- **Tentar falhas/interrompidos** cria outro lote apenas com falhas, interrupções e
  cancelamentos;
- **Gerar todos novamente** abre a seleção explícita e cria outro lote somente com
  os clientes confirmados.

O painel distingue coleta remota, espera por decisão WEB, pronto para montagem,
montando documento e terminal. Após 900 segundos sem progresso remoto há alerta;
em 7.200 segundos a tentativa local expira sem cancelar automaticamente o export
na Tenable. A API expõe apenas que existe checkpoint validado, nunca seu caminho.

VM, WAS e Cloud possuem resultados independentes. Um conjunto parcial preserva os
documentos válidos e mostra somente componentes falhos/interrompidos e retentáveis.
O retry integrado padrão continua disponível para Cloud; VM/WAS seletivos exigem o
executor faseado configurado e falham de forma explícita quando ele não estiver
disponível.

## Cloud Security

Quando habilitado no cliente, o Cloud inicia junto com a execução normal e usa a
API GraphQL em fluxo próprio. Antes da coleta completa, **Testar API Cloud** valida
credencial e contrato mínimo. Cada execução publica um único DOCX Cloud padrão. Os
valores legados `base`, `expanded` e `comparison` são aceitos somente ao carregar
perfis antigos e normalizados internamente para o modelo atual; não há seletor de
modelo na interface nem sufixo de variante no nome do arquivo.

O documento padrão reúne resumo executivo, hosts e imagens vulneráveis, Top 5 de
CVEs críticas detalhadas, overview das principais vulnerabilidades por imagem de
contêiner e Top 10 com correção disponível agrupado por CVE e software. As duas
tabelas exibem `Fixed by` quando o tenant fornece a versão corrigida; ausência ou
schema não suportado aparece como `N/D` e não bloqueia o documento. Dashboard,
componentes, postura quando suportada, aging, desempenho de remediação e evolução
mensal também permanecem no modelo. Sem
histórico anterior, a evolução mostra apenas a fotografia atual, sem simular uma
comparação. O item de inventário Cloud não faz parte do documento.

Uma falha Cloud resulta em sucesso parcial: VM, WAS, customizado e TAG continuam
disponíveis. O histórico do cliente mostra o alerta e oferece **Tentar Cloud
novamente**, sem repetir a coleta geral. O relatório representa o estado Cloud no
instante da coleta; o período identifica a competência do relatório, mas não cria
uma reconstrução histórica que não tenha sido preservada em fotografia anterior.

A estratégia rápida do projeto legado `RelatorioTenableITP`, baseada em consultas
projetadas e agregadas da API v3, está preservada no
[catálogo de APIs](docs/02-catalogo-apis-tenable.md#base-técnica-legada--consultas-projetadas-do-relatoriotenableitp)
somente como base técnica. Ela pode orientar prévias ou validações futuras, mas não
substitui o snapshot canônico sem prova de equivalência.

## Verificação local

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe tools\validate_project_guidance.py --root .
```

Chamadas reais à Tenable só devem ser feitas com perfil e credenciais autorizados
e com a confirmação explícita exigida pelo comando.

## Documentação

Comece pelo [índice da documentação](docs/README.md) e pelo [design da solução](DESIGN.md). Os guias principais são:

- [visão geral e objetivos](docs/19-visao-geral-e-objetivos.md);
- [arquitetura e fluxo de dados](docs/20-arquitetura-e-fluxo-de-dados.md);
- [catálogo de dados e métricas](docs/21-catalogo-de-dados-e-metricas.md);
- [guia operacional](docs/22-guia-operacional.md);
- [guia de desenvolvimento](docs/23-guia-de-desenvolvimento.md).

As decisões históricas e os contratos detalhados continuam em `docs/01` a
`docs/18`. As instruções para agentes estão em [AGENTS.md](AGENTS.md), com regras
mais específicas nas pastas correspondentes.
