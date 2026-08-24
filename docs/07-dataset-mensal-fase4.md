# Contrato e validação do dataset mensal da Fase 4

> Nota de compatibilidade: `report-definition-v1.2` acrescenta a matriz
> `metrics.by_exploit_framework`. Ela combina `Exploit Available`, o indicador
> direto `Exploited By Malware` e os cinco flags individuais de framework da
> Tenable. Snapshots antigos continuam legíveis: malware fica desconhecido e a
> lista de frameworks fica vazia, sem inferência.

**Versão métrica atual:** `report-definition-v1.2`  
**Validação:** 53 testes offline e coleta autenticada em 2026-08-12/13

## Objetivo

A Fase 4 materializa uma população mensal defensável para os relatórios. Ela evita que o documento misture todo o histórico do tenant, ativos sem evidência no período ou findings posteriores ao fechamento. O resultado é imutável e deriva dos mesmos snapshots de ativos e findings do `run_id`.

## Políticas de execução e janela

- Automática mensal: mês-calendário anterior completo no timezone do perfil.
- Agendamento automático: primeiro dia do mês, depois do fechamento.
- Manual padrão: um mês-calendário móvel até o instante da execução.
- Manual alternativo: últimos `N` dias até a execução ou intervalo específico escolhido pelo analista.
- Representação: intervalo fechado/aberto `[period_start, period_end)`.
- A tolerância de coleta após o fechamento é configurável; o padrão é um dia.

Exemplo automático em `America/Fortaleza`: a execução de 1º de agosto cobre `[2026-07-01T00:00:00-03:00, 2026-08-01T00:00:00-03:00)`. Exemplo manual em 13 de agosto às 10h: o padrão cobre `[2026-07-13T10:00:00-03:00, 2026-08-13T10:00:00-03:00)`. Em meses mais curtos, o dia inicial é limitado ao último dia válido.

Os modos são registrados no dataset como `PREVIOUS_CALENDAR_MONTH`, `MANUAL_ROLLING_MONTH`, `TRAILING_DAYS` ou `EXPLICIT_RANGE`, junto com `execution_type=AUTOMATIC_MONTHLY|MANUAL`.

## Duas barreiras temporais

O request remoto aplica `since=period_start`, tipos `host`, severidades acionáveis e estados `OPEN`, `REOPENED`, `FIXED`. Isso reduz o download, mas não define sozinho a fotografia mensal. O dataset reaplica localmente início e fim porque `since` é uma condição inferior e pode retornar eventos após o mês.

Para `OPEN`/`REOPENED`, o evento mensal é `last_found`. Para `FIXED`, é `last_fixed`. Eventos em `period_end` pertencem ao período seguinte.

## Ativos observados e “fantasmas”

Um ativo é incluído quando tem evidência temporal no mês por scan ou por finding aceito. Evidência de finding é necessária porque um export realizado depois do fechamento pode mostrar somente o `last_scan` posterior, embora o finding prove que o ativo foi observado durante o mês.

Cada ativo recebe exatamente um motivo:

- `OBSERVED_BY_SCAN`;
- `OBSERVED_BY_FINDING`;
- `EXCLUDED_INACTIVE_BEFORE_PERIOD`;
- `EXCLUDED_FIRST_SEEN_AFTER_PERIOD`;
- `EXCLUDED_STALE_BEFORE_PERIOD`;
- `EXCLUDED_NO_PERIOD_EVIDENCE`;
- `EXCLUDED_MISSING_TIME_EVIDENCE`.

Nada é removido do raw. As exclusões somente impedem que registros sem sustentação temporal contaminem métricas e rankings. O vínculo permanece exclusivamente por UUID da Tenable.

## População e métricas

O grão é a instância estável `asset.uuid + plugin.id + port + protocol`.

- Não mitigadas: `OPEN`/`REOPENED` com `last_found` no período.
- Mitigadas: `FIXED` com `last_fixed` no período; se `FIXED` não foi coletado, o valor é `NOT_COLLECTED`, nunca zero.
- Ressurgidas: `REOPENED` com `resurfaced_at` no período.
- Severidade informativa: excluída; nenhum perfil conhecido a utiliza atualmente.
- Aging: diferença entre o fim do período e `first_found` válido.
- Patch acima de 30 dias: finding não mitigado com patch disponível e idade maior que 30 dias.
- Exploitable: `plugin.exploit_available == true`; sempre subconjunto do total.
  Sinais de malware, facilidade de exploração e flags de frameworks não promovem
  o finding para esse total.
- A matriz de exploração usa, nessa ordem: `Exploitable`, `Malware`, `Core Impact`,
  `Canvas`, `D2 Elliot`, `ExploitHub` e `Metasploit`. `Malware` conta apenas
  findings exploráveis com `exploited_by_malware == true`; as cinco linhas de
  framework usam seus próprios flags. As linhas podem se sobrepor.

O dataset publica ainda Top 10 de ativos, matriz por sistema operacional e Top 5 de plugins para não mitigadas, mitigadas e ressurgidas. O Top 5 contém detalhes, hosts, CVEs e links públicos; `Plugin Output` só aparece quando a coleta e a geração foram explicitamente habilitadas.

## Reconciliação e gates

As contagens por motivo precisam reconciliar exatamente com as entradas de ativos e findings. A publicação registra disponibilidade de fontes, horário de conclusão, atraso em relação ao fechamento, hashes e motivos de qualidade. Coleta antes do fim do período é erro; coleta além da tolerância é aviso porque estados correntes podem ter mudado depois do fechamento.

## Evidência autenticada

A execução mensal final `7a3e5180-353d-4c22-9147-42f9d43e2d88`, referente a julho de 2026 e publicada por reutilização dos exports autenticados já concluídos, produziu:

- 2.552 ativos retornados; 840 observados e 1.712 excluídos com motivo;
- 60.057 findings retornados; 14.091 incluídos e 45.966 posteriores ao mês excluídos;
- 7.755 não mitigados, 6.336 mitigados e 2.142 ressurgidos;
- 198 ativos vulneráveis e 2.422 findings exploráveis;
- zero findings órfãos no snapshot normalizado;
- invariantes `Exploitable <= Total` satisfeitas nos Top 10 ativos;
- os cinco itens do Top 5 não mitigado com pelo menos um link público;
- hashes do dataset e do manifesto conferidos.

A coleta ocorreu aproximadamente 12 dias após o fechamento, portanto o dataset registra `COLLECTION_AFTER_MONTH_CLOSE_GRACE`. O teste comprova o fluxo, mas também sustenta a necessidade operacional de executar no primeiro dia do mês.

## Artefatos

```text
data/
  automatic-monthly/
    raw/...
    snapshots/...
    normalized/...
    report-datasets/<client_id>/<run_id>/<period_id>/...
  manual/
    raw/...
    snapshots/...
    normalized/...
    report-datasets/<client_id>/<run_id>/<period_id>/...
```

Artefatos anteriores à versão 1.1 permanecem no layout legado e não são movidos ou sobrescritos automaticamente.

O diretório `data/` contém informações reais do tenant e permanece ignorado pelo Git.

Referências oficiais: [export de vulnerabilidades](https://developer.tenable.com/reference/exports-vulns-request-export), [refinamento de exports VM](https://developer.tenable.com/docs/refine-vulnerability-export-requests), [semântica atual de `since`](https://developer.tenable.com/changelog/io-new-behavior-for-since-filter-in-vulnerability-exports), [export de ativos v2](https://developer.tenable.com/reference/export-assets-v2) e [integrações VM/WAS](https://developer.tenable.com/docs/vm-and-was-integrations).
