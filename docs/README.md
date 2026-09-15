# Documentação do projeto

Este índice separa a documentação vigente dos registros históricos de evolução.
Quando houver divergência, os guias vigentes e o código testado prevalecem sobre
uma descrição de fase antiga.

## Ordem de leitura

1. [Contexto do projeto](../CONTEXTO.md): retomada, estado consolidado, limites e
   mapa das fontes de verdade.
2. [README](../README.md): início rápido e resumo de uso.
3. [Design da solução](../DESIGN.md): arquitetura e invariantes estruturais.
4. Guias `19` a `23`: contratos vigentes de produto, dados, operação e
   desenvolvimento.
5. Documentos `01` a `18` e registros em `superpowers`: evolução histórica e
   decisões de cada ciclo.

## Guias vigentes

- [Design da solução](../DESIGN.md): decisões estruturais e invariantes.
- [Visão geral e objetivos](19-visao-geral-e-objetivos.md): finalidade, escopo,
  entregáveis e limites atuais.
- [Arquitetura e fluxo de dados](20-arquitetura-e-fluxo-de-dados.md): componentes,
  execução completa, histórico e ciclo de vida dos artefatos.
- [Catálogo de dados e métricas](21-catalogo-de-dados-e-metricas.md): dados VM/WAS,
  regras temporais, métricas e validação na plataforma.
- [Guia operacional](22-guia-operacional.md): instalação, clientes, execução,
  acompanhamento, falhas, `MAIN` e armazenamento.
- [Guia de desenvolvimento](23-guia-de-desenvolvimento.md): organização do código,
  testes, alterações seguras e validação dos documentos.

## Referências históricas

- [Análise e arquitetura original](01-analise-e-arquitetura.md)
- [Catálogo das APIs Tenable](02-catalogo-apis-tenable.md)
- [Protocolo de análise dos DOCX](03-protocolo-analise-docx.md)
- [Matriz e contrato dos relatórios](04-matriz-e-contrato-dos-relatorios.md)
- [Histórico, regras críticas e tradução](05-historico-regras-criticas-e-traducao.md)
- [Modelo normalizado](06-modelo-normalizado-fase3.md)
- [Dataset mensal](07-dataset-mensal-fase4.md)
- [Template Word](08-template-word-fase5.md)
- [Relatório-base completo](09-relatorio-base-completo-fase6.md)
- [Escopo e comparativo por TAG](10-escopo-tags-e-comparativo-por-rede.md)
- [Catálogo visual e tabelas customizadas](11-catalogo-visual-e-tabelas-customizadas.md)
- [Perfis e variações](12-perfis-e-variacoes-fase7.md)
- [Web App Scanning](13-was-fase8.md)
- [Histórico e tendências](14-historico-e-tendencias-fase9.md)
- [Orquestração e distribuição](15-orquestracao-e-distribuicao-fase10.md)
- [Armazenamento e reciclagem](16-armazenamento-e-reciclagem.md)
- [PostgreSQL](16-postgresql-migracao-e-operacao.md)
- [Interface web](17-interface-web-mvp.md)
- [`MAIN`, retentativas e operação](18-main-retentativas-inteligencia-operacao.md)

## Decisões de implementação

Os desenhos e planos aprovados ficam em `docs/superpowers/specs` e
`docs/superpowers/plans`. Eles explicam por que uma mudança foi criada, mas não
substituem os guias operacionais atuais.

## Instruções especializadas

- [AGENTS.md da raiz](../AGENTS.md): regras globais do repositório;
- [clientes](../clients/AGENTS.md), [código-fonte](../src/tenable_reports/AGENTS.md)
  e [testes](../tests/AGENTS.md): complementos por diretório;
- [skill de operação](../.agents/skills/operating-tenable-reports/SKILL.md) e seu
  [runbook](../.agents/skills/operating-tenable-reports/references/runbook.md);
- [skill de validação](../.agents/skills/validating-tenable-report-data/SKILL.md) e
  seu [contrato de dados](../.agents/skills/validating-tenable-report-data/references/data-contract.md);
- [template corporativo](../templates/corporate/README.md): contrato do shell DOCX.

## Segurança da documentação

Exemplos devem usar nomes fictícios. Não registre access key, secret key, senha do
PostgreSQL, hostname, IP, nome ou e-mail reais. Evidências com dados reais ficam
fora do Git.
