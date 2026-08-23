# Histórico e tendências — Fase 9

## Estado atual — 2026-08-23

O relatório customizado geral mantém as tabelas e séries mensais aplicáveis ao
cliente. O comparativo de uma TAG selecionada passou a integrar o respectivo
relatório operacional por TAG. Categoria, valor e versão do escopo fazem parte da
compatibilidade, impedindo comparação entre TAGs diferentes. O histórico persistido
é compacto e não depende da retenção permanente dos findings raw.

**Concluída em:** 2026-08-20

## Contrato implementado

A Fase 9 persiste cada competência pela interface substituível
`SnapshotRepository`, com PostgreSQL como backend operacional. O banco armazena
metadados, agregados mensais, identidades estáveis de findings e recortes das tags
selecionadas; as credenciais da Tenable não são gravadas. O SQLite permanece como
compatibilidade para fixtures e migração controlada.

Um predecessor só é aceito quando coincidem `client_id`, `tenant_id`, tipo de
execução, modo do período, timezone, versão métrica e hash do escopo geral. Relatórios
manuais não são comparados com mensais automáticos, e uma mudança de regra métrica ou
escopo bloqueia o delta. A ausência é registrada como `NO_IMMEDIATE_MAIN`; nunca é
convertida em zero nem autoriza o uso de um mês mais antigo como substituto
silencioso.

Cada cliente, competência e escopo compatível possui uma referência canônica
`main`. A primeira geração mensal válida é promovida automaticamente quando ainda
não existe referência. Uma segunda geração da mesma competência permanece como
alternativa até que o analista a promova explicitamente.

## Resultados derivados

Com duas competências compatíveis, o dataset enriquecido recebe:

- `monthly_history` e a vista `monthly_views/general` para os gráficos de volume;
- `previous_period_overview` com mitigadas, não mitigadas, exploráveis e patch acima
  de 30 dias, incluindo severidade;
- `finding_transitions`, calculado por `finding_key` estável, separando novas,
  corrigidas, ressurgidas e persistentes;
- `network_comparisons`, sempre `mesma tag atual × mesma tag anterior`;
- movimentação do ranking por identidade estável do ativo no segundo DOCX.

As tags continuam restritas ao comparativo customizado. Elas não entram no hash do
escopo geral e não filtram métricas, Top 5 ou rankings do relatório-base.

## CLI

O fluxo mensal e o manual publicam histórico por padrão depois de criar e validar
dataset, relatório-base, relatório customizado e manifesto. Em produção, a conexão
é lida de `credentials/database.env`. Use `--history-database` apenas para a ponte
legada em SQLite, `--history-export-csv` para exportar dados agregados e
`--skip-history` apenas quando o histórico não for desejado.

Um dataset já existente pode ser publicado sem acessar a API:

```powershell
$env:PYTHONPATH = "src"
python -m tenable_reports publish-history `
  --profile .\clients\examples\client-profile-intelligence-expanded.json `
  --dataset .\data\report-dataset.json `
  --normalized-findings .\data\normalized\findings.jsonl `
  --database .\data\history\tenable-history.sqlite `
  --output .\data\report-dataset-with-history.json `
  --export-csv .\data\history\tenable-history.csv
```

O CSV produzido pode ser reimportado com `import-history-csv`. Como é agregado, ele
recupera as séries mensais, mas não inventa identidades de findings, hosts ou recortes
por tag que não existam no arquivo.

O segundo DOCX também pode publicar e consumir o histórico no mesmo comando ao usar
`generate-customizations-docx` com `--history-database` e
`--normalized-findings`. Sem essas opções, ele continua aceitando diretamente um
dataset já enriquecido.

## Evidência de validação

- duas competências compatíveis encontram o predecessor determinístico;
- alteração de versão métrica impede o comparativo;
- novas, corrigidas, ressurgidas e persistentes usam identidade estável;
- a mesma tag é comparada nos dois períodos; outra tag não é usada como substituta;
- exportação/importação CSV preserva os agregados;
- o cenário integrado de julho/agosto comprova promoção manual, preservação do
  primeiro `main`, uso exato do predecessor e exclusão com substituta;
- a suíte completa possui 176 testes e 2 subtestes offline.

## Fora do escopo

Distribuição externa e canais de notificação continuam dependências independentes e
exigem autorização explícita.
