# Relatórios Tenable

Aplicação local para coletar dados da Tenable, normalizá-los, manter histórico
compacto e gerar relatórios mensais em Word para uma carteira de clientes.

O projeto separa coleta, regras de negócio e apresentação para que os mesmos dados
possam alimentar três tipos de documento:

1. relatório-base geral, com o conteúdo editorial comum, Top 5 VM e Top 5 WEB;
2. relatório geral de inteligência e customizações, com módulos atuais e históricos;
3. relatório operacional compacto por TAG, opcional, com comparativo temporal da
   própria TAG quando habilitado.

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

## Períodos

- Automático mensal: executado no primeiro dia do mês para o mês-calendário anterior completo.
- Manual padrão: um mês móvel até o instante da execução.
- Manual personalizado: últimos `X` dias ou intervalo explícito escolhido pelo analista.

Os intervalos internos são tratados como `[início, fim)`. Findings ativos usam a
data de última identificação; findings mitigados usam a data da correção. A
severidade informativa não faz parte do relatório.

## Dados e armazenamento

PostgreSQL é a fonte operacional do histórico, do registro de documentos, das
tentativas e da referência `MAIN`. Os DOCX publicados e o histórico compacto são
duráveis. Raw, snapshots, normalizados e datasets intermediários são temporários:
após uma publicação validada eles são removidos; uma falha os preserva por tempo
limitado para diagnóstico.

O WAS é opcional e tolerante a indisponibilidade: se o cliente não tiver o produto
ou não houver achados, a parte VM continua sendo gerada. Cloud Security e tradução
por provedor externo ainda não estão implementados.

## Verificação local

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe tools\validate_project_guidance.py --root .
```

Chamadas reais à Tenable só devem ser feitas com perfil e credenciais autorizados
e com a confirmação explícita exigida pelo comando.

## Documentação

Comece pelo [índice da documentação](docs/README.md). Os guias principais são:

- [visão geral e objetivos](docs/19-visao-geral-e-objetivos.md);
- [arquitetura e fluxo de dados](docs/20-arquitetura-e-fluxo-de-dados.md);
- [catálogo de dados e métricas](docs/21-catalogo-de-dados-e-metricas.md);
- [guia operacional](docs/22-guia-operacional.md);
- [guia de desenvolvimento](docs/23-guia-de-desenvolvimento.md).

As decisões históricas e os contratos detalhados continuam em `docs/01` a
`docs/18`. As instruções para agentes estão em [AGENTS.md](AGENTS.md), com regras
mais específicas nas pastas correspondentes.
