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
testar suas APIs, buscar TAGs, iniciar uma geração individual ou da carteira,
acompanhar o progresso, baixar documentos e escolher a referência `MAIN` usada
no próximo comparativo.

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
staging quando uma retentativa isolada ainda pode aproveitá-lo. Tradução por
provedor externo continua sem integração automática.

No botão **Gerar todos** e no automático mensal, uma falha WAS inicia uma única
retentativa apenas do componente WEB. Se ela também falhar, os relatórios são
publicados sem WAS e recebem o alerta `WAS_RETRY_EXHAUSTED`; VM, assets, TAG e
Cloud não são repetidos. A geração manual individual continua oferecendo ao
analista a decisão entre tentar novamente ou continuar sem WEB.

## Controle durável da carteira

A geração da carteira usa uma fila sequencial persistida no PostgreSQL. Reiniciar a
interface não transforma trabalhos interrompidos em novas coletas: o painel recupera
o lote e mostra quantos clientes concluíram, falharam, foram interrompidos ou ainda
estão pendentes.

No painel do lote:

- **Pausar após o atual** deixa o cliente em execução terminar e mantém os próximos
  na fila;
- **Parar lote** pede interrupção cooperativa ao cliente atual, marca os ainda não
  iniciados como `CANCELLED_BY_USER` e preserva o export remoto;
- **Retomar lote** libera somente trabalhos que continuavam `QUEUED`;
- **Tentar falhas/interrompidos** cria outro lote apenas com falhas, interrupções e
  cancelamentos;
- **Gerar todos novamente** cria outro lote com todos os clientes e exige confirmação.

Os estados terminais incluem `STOPPED`, `COMPLETE`, `COMPLETE_WITH_FAILURES` e
`COMPLETE_WITH_WARNINGS`. Se a interrupção cooperativa não encerrar o subprocesso
local no prazo de tolerância, a aplicação finaliza somente a árvore local e mantém
o UUID/checkpoint para recuperação; ela não envia cancelamento à Tenable por essa
ação.

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
