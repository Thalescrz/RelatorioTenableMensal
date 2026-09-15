# Qualidade WAS, VM vazia e padrao Cloud

> **Registro de decisão:** este arquivo preserva o desenho ou plano considerado no
> momento da entrega. Ele não comprova sozinho o estado atual nem a conclusão de
> cada etapa. Consulte o [contexto atual](../../../CONTEXTO.md) e o
> [índice da documentação](../../README.md).

## Objetivo

Corrigir tres problemas observados nos documentos mensais sem alterar as metricas
aprovadas: a coluna vazia de familia nos quadros WAS, a publicacao silenciosa de
uma coleta VM vazia apesar de existirem ativos e a divergencia visual do relatorio
Cloud Security.

## Familia de plugins WAS

O export de findings WAS nao e obrigado a trazer a familia do plugin. Depois de
normalizar os findings, a aplicacao consulta o catalogo oficial de plugins WAS por
`Plugin ID`, persiste os metadados no catalogo PostgreSQL existente e enriquece os
findings antes de gravar o snapshot normalizado.

A consulta e best effort: falha de permissao, transporte ou catalogo nao invalida
os findings WAS. O ocorrido vira alerta operacional sem expor dados sensiveis.

No DOCX a tabela decide seu esquema a partir das cinco linhas exibidas:

- se ao menos uma linha tiver familia, a coluna `Familia` aparece e ausencias
  pontuais recebem `Nao informado pela Tenable`;
- se nenhuma linha tiver familia, a coluna inteira e omitida;
- as demais colunas e seus valores nao mudam.

## VM vazia com ativos existentes

Uma exportacao VM concluida com zero findings continua sendo um retorno valido da
API, mas nao deve parecer uma coleta integralmente saudavel quando existem ativos
observados. O dataset registra `VM_FINDINGS_EMPTY_WITH_ASSETS` como aviso de
qualidade, preservando o zero real e orientando o analista a revisar licenciamento,
permissoes e recencia dos scans. Nenhum finding historico e inventado.

## Padrao visual Cloud

O relatorio Cloud ampliado permanece como unico modelo Cloud. Capa, corpo,
cabecalhos, rodapes, titulos, paragrafos e tabelas passam a usar o mesmo sistema
tipografico e cromatico do relatorio geral: Calibri, azul corporativo, hierarquia
de tamanhos e espacamentos equivalentes. O conteudo e as metricas Cloud nao mudam.

## Validacao

As mudancas sao cobertas por testes unitarios e estruturais. Os DOCX sanitizados
de WAS e Cloud sao renderizados com LibreOffice e as paginas afetadas sao
inspecionadas visualmente antes da entrega.
