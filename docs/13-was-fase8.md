# Coleta e relatório Web App Scanning — Fase 8

## Estado atual — 2026-08-23

WAS continua geral, opcional e independente de VM; ausência de produto, permissão
ou achados não bloqueia os documentos VM. TAGs não filtram VM ou WAS gerais. As
TAGs selecionadas agora podem gerar relatórios operacionais VM próprios, e o
comparativo temporal opcional fica dentro do documento da mesma TAG. Relatório WEB
por TAG não faz parte do escopo atual.

## Resultado

A Fase 8 integra o Tenable Web App Scanning ao mesmo ciclo mensal do relatório sem
misturar seu contrato com Vulnerability Management. VM permanece geral; WAS também
permanece geral dentro do perfil. Tags selecionadas pelo analista continuam afetando
exclusivamente o comparativo temporal da mesma rede no segundo DOCX.

O primeiro DOCX recebe do snapshot WAS:

- aplicações e contagens por severidade;
- plugins WEB e agrupamentos OWASP 2021;
- Top 5 de vulnerabilidades WEB abertas/ressurgidas;
- descrição, solução, referências e URIs afetadas;
- `Plugin Output` somente quando habilitado pelo perfil e comprovadamente coletado.

Uma correção posterior ao primeiro documento de homologação passou a reconhecer as
categorias OWASP retornadas pelo tenant como `A1` a `A10`, normalizando-as para
`A01` a `A10`. Assim, categorias sem zero à esquerda não são mais descartadas.

Descrições e soluções extensas usam o fragmentador já existente na apresentação. A
futura tradução opera sobre esses blocos e nunca envia `Plugin Output`, prova,
payload ou outros campos de evidência ao tradutor.

## Contrato técnico

O adaptador usa o fluxo assíncrono oficial:

1. `POST /was/v1/export/vulns` inicia o export;
2. `GET /was/v1/export/vulns/{uuid}/status` acompanha o job;
3. `GET /was/v1/export/vulns/{uuid}/chunks/{id}` baixa cada chunk.

O coletor publica raw e manifesto imutáveis sob o mesmo `run_id` de VM e ativos. A
normalização gera `was-findings.jsonl` e `was-manifest.json`, preservando hashes,
contagens de entrada, rejeições e duplicatas. O dataset registra a fonte
`tenable_was_findings` separadamente e usa disponibilidade tipada:
`AVAILABLE`, `NO_DATA` ou `NOT_COLLECTED`.

A identidade preferencial é `finding_id`. Quando ele não existe, a chave combina
`asset.uuid + plugin.id + URL + método HTTP`. O Top 5 considera somente estados
`OPEN`/`REOPENED`, severidades permitidas e eventos dentro de `[início, fim)`. Findings
`FIXED` entram na população mensal usando `last_fixed`, mas não no Top 5 aberto.

## Validação autenticada

Em 13 de agosto de 2026, o contrato foi validado com as credenciais locais sem
exposição de seus valores. O export retornou 3.304 registros em um chunk. Para julho
de 2026, o domínio incluiu:

| Métrica | Valor |
|---|---:|
| Findings WAS recebidos | 3.304 |
| Instâncias incluídas no período | 1.151 |
| Abertas/ressurgidas | 952 |
| Corrigidas | 199 |
| Aplicações com findings abertos | 20 |
| Vulnerabilidades no Top WEB | 5 |

O relatório-base sanitizado possui 22 páginas e foi verificado integralmente após
renderização no LibreOffice. O Office MCP encontrou 81 títulos, 920 parágrafos, 29
tabelas e 3 imagens, sem avisos estruturais. O sumário permanece como campo dinâmico
do Word; o renderizador LibreOffice utilizado na homologação não calculou seu texto.

## Decisões e limites

- Nenhum valor ausente é convertido em zero ou texto inventado.
- Toda tabela sem registros recebe abaixo uma mensagem explícita de que não houve
  identificação no mês; categorias OWASP usam uma mensagem própria.
- URI, hostname, IP, cliente e pessoa podem permanecer vazios por anonimização.
- A API de export observada entrega `output`; sua publicação continua opcional.
- A saúde global não recebe um CES/AES artificial: o export de findings não oferece
  essa métrica.
- A integração Cloud Security, o provedor de tradução, o histórico persistente, o
  agendamento e a interface permanecem para fases posteriores.

## Referências oficiais

- [Export WAS findings](https://developer.tenable.com/reference/was-export-findings)
- [Status do export WAS](https://developer.tenable.com/reference/was-export-findings-status)
- [Download de chunk WAS](https://developer.tenable.com/reference/was-export-findings-download-chunk)
- [Integração VM e WAS](https://developer.tenable.com/docs/vm-and-was-integrations)
