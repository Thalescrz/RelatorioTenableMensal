# Contrato do modelo normalizado da Fase 3

**Versão:** 1  
**Validação:** fixtures offline e execução autenticada em 2026-08-12

## Objetivo e fronteira

A Fase 3 transforma dois exports imutáveis do mesmo `run_id` — ativos v2 e findings VM — em um conjunto determinístico e auditável. Ela não calcula ainda indicadores executivos, Top 5 ou conteúdo Word; esses consumidores serão implementados a partir deste contrato nas fases seguintes.

## Identidade

| Entidade | Chave estável | Regra |
|---|---|---|
| Ativo | `client_id + Tenable asset.id` | Não fundir registros por IP, hostname, MAC ou nome |
| Finding VM | `asset.uuid + plugin.id + port.port + port.protocol` | A chave canônica é armazenada como SHA-256 |
| Vínculo | `finding.asset.uuid == asset.id` | Sem fallback por atributos mutáveis |

Um finding cujo UUID de ativo não aparece no export de ativos é preservado como órfão, recebe `asset_key=null` e gera `FINDING_ASSET_ORPHAN`. Isso evita associações silenciosas e incorretas.

## Ciclo de vida

O ativo recebe `ACTIVE`, `DELETED` ou `TERMINATED` a partir de `timestamps.deleted_at` e `timestamps.terminated_at`. Esse estado não altera o estado do finding: ativo removido ou terminado não significa vulnerabilidade corrigida. Somente o estado e as datas do próprio finding podem sustentar uma classificação de correção.

## Artefatos publicados

```text
data/
  raw/<client_id>/<run_id>/<source>/<export_uuid>/
  snapshots/<client_id>/<run_id>/<source>.snapshot.json
  normalized/<client_id>/<run_id>/
    assets.jsonl
    findings.jsonl
    quality-issues.jsonl
    manifest.json
```

Os arquivos são criados de forma exclusiva: uma execução não sobrescreve silenciosamente outra com o mesmo `run_id`. O manifesto registra os snapshots de origem, regras de identidade, reconciliação, contagens de qualidade, URI, tamanho, número de registros e SHA-256 de cada artefato.

## Campos normalizados

Ativos preservam identidade, ciclo de vida, nome de exibição, tipos, fontes, hostnames, FQDNs, IPv4, IPv6, MACs, sistemas operacionais, rede, datas de scan e timestamps, ACR e AES.

Findings preservam identidade, plugin, família, CVEs, sinopse, descrição, solução, CVSS, patch, output opcional, porta/protocolo/serviço, estado, severidade, datas, VPR e exploitabilidade. `Plugin Output` continua ausente por padrão na coleta e só é incluído mediante a opção explícita `--include-output`.

## Reconciliação e gates

As seguintes invariantes são obrigatórias:

1. `ativos_normalizados + ativos_rejeitados + ativos_duplicados = ativos_raw`;
2. `findings_normalizados + findings_rejeitados = findings_raw`;
3. `findings_vinculados + findings_orfaos = findings_normalizados`;
4. export concluído com chunks falhos ou cancelados é falha, nunca ausência de dados;
5. IP inválido pode ser descartado com aviso, mas seu valor não é repetido no artefato de qualidade.

## Evidência de aceite

A suíte integrada possui 53 testes offline, incluindo compatibilidade com este contrato. A execução autenticada completa da Fase 3 publicou 3.173 ativos e 148.901 findings, todos vinculados, sem órfãos, rejeições, duplicatas ou ocorrências de qualidade. Os três hashes do manifesto normalizado foram recalculados e corresponderam aos arquivos publicados.

Referências oficiais: [Export de ativos v2](https://developer.tenable.com/reference/export-assets-v2), [status do export de ativos](https://developer.tenable.com/reference/exports-assets-export-status), [download de chunks](https://developer.tenable.com/reference/exports-assets-download-chunk), [recuperação de dados de ativos](https://developer.tenable.com/docs/retrieve-asset-data-from-tenableio) e [integrações VM/WAS](https://developer.tenable.com/docs/vm-and-was-integrations).
