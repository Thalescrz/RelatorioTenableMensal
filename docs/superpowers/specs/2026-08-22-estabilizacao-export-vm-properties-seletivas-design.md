# Estabilização do Export VM e Propriedades Seletivas — Design

## Contexto

As execuções recentes do TRT8 passaram a dividir a coleta de vulnerabilidades em dois exports: `OPEN/REOPENED` e `FIXED`. O primeiro segmento permaneceu em `PROCESSING` por 30 minutos, apesar de já disponibilizar um dos dois chunks. Antes dessa alteração, exports equivalentes com os três estados combinados concluíram com o mesmo período e `num_assets=1000`.

O coletor atual só baixa chunks depois que o job inteiro termina. Portanto, um timeout descarta a oportunidade de preservar os chunks já disponíveis e obriga a próxima tentativa a recomeçar. A Tenable também passou a oferecer o parâmetro `properties`, que reduz o payload, mas o conjunto experimental existente não contém todos os campos usados pelos relatórios.

## Objetivos

- Restaurar `combined` como estratégia padrão e manter `split` como opção experimental por cliente.
- Tornar `num_assets` configurável por cliente, com padrão local de 250 e opções 100, 250, 500 e 1000 na interface.
- Baixar e persistir cada chunk assim que ele aparecer em `chunks_available`.
- Manter um manifesto parcial reutilizável após timeout e reaproveitá-lo em uma retentativa da mesma competência.
- Preservar as regras seguras de cancelamento: somente job criado pela execução atual, sem nenhum progresso, pode ser cancelado automaticamente.
- Implementar propriedades seletivas com modos `disabled`, `validation` e `enabled`.
- Fazer fallback automático para payload completo quando a API rejeitar `properties` com HTTP 400 ou quando o payload seletivo não satisfizer o contrato mínimo do relatório.
- Oferecer validação A/B controlada por cliente na interface, sem iniciar export real automaticamente durante instalação ou atualização.

## Não objetivos

- Não aumentar silenciosamente o timeout de 30 minutos.
- Não cancelar automaticamente jobs reutilizados, fornecidos ou retomados.
- Não remover o limite superior local do período do relatório.
- Não assumir que um chunk menor reduz a quantidade total de findings; ele melhora granularidade, persistência e recuperação.
- Não ativar propriedades seletivas globalmente sem validação por tenant.

## Configuração por cliente

O perfil passa a aceitar o bloco abaixo dentro de `reporting`:

```json
{
  "vm_export": {
    "strategy": "combined",
    "num_assets_per_chunk": 250,
    "selective_properties": "disabled"
  }
}
```

Contratos:

- `strategy`: `combined` ou `split`.
- `num_assets_per_chunk`: inteiro entre 50 e 5000; a interface oferece 100, 250, 500 e 1000.
- `selective_properties`: `disabled`, `validation` ou `enabled`.
- Perfis antigos recebem os padrões acima sem migração obrigatória.

## Estratégias de coleta

### Combined

Um export contém `OPEN`, `REOPENED` e `FIXED`, usando o filtro `since`. Esse comportamento corresponde à semântica oficial: `since` é aplicado a `last_found` nos estados ativos e a `last_fixed` nos estados corrigidos. É o padrão de estabilização por ter concluído anteriormente no mesmo tenant.

### Split

Dois exports são mantidos como opção experimental:

- `active`: `OPEN/REOPENED`, observado por `last_found`.
- `fixed`: `FIXED`, observado por `last_fixed`.

Os segmentos continuam agregados no mesmo snapshot lógico e não alteram os números finais após o recorte superior local.

## Persistência incremental e retomada

O cliente VM notificará o coletor sempre que surgirem novos IDs em `chunks_available`. O coletor armazenará cada chunk de forma atômica e atualizará `manifest.partial.json` com:

- cliente, tenant, run, logical job e UUID;
- origem do job;
- query sanitizada e seu hash canônico;
- chunks completos, hashes e contagens;
- estado remoto e horário da última atualização.

Ao concluir, o manifesto parcial será promovido logicamente para `manifest.json`. Em timeout, ele permanecerá disponível. Uma retentativa só pode reutilizá-lo quando cliente, tenant, origem, query e `logical_job_id` forem compatíveis. Chunk inválido ou com hash divergente será baixado novamente.

## Propriedades seletivas

O conjunto seletivo será mantido em um módulo próprio e derivado dos campos efetivamente consumidos pela normalização e pelos relatórios. Ele incluirá identidade, estado, severidade, datas, porta/serviço, identificação do ativo, nome/família/CVE, descrição, sinopse, solução, referências, CVSS, VPR e metadados de exploração. `output` só será solicitado quando a opção de coluna Output estiver habilitada.

O normalizador aceitará tanto o formato legado (`plugin.cvss3_base_score`, `definition.cvss3_base_score`) quanto o formato seletivo oficial (`definition.cvss3.base_score`, `definition.cvss3.base_vector`). Para o indicador Exploitable, o campo direto legado continua prioritário; quando ele não existir no formato seletivo, `definition.exploitability_ease == "Exploits are available"` ou algum framework oficial marcado como verdadeiro caracteriza a vulnerabilidade como explorável.

## Modos de propriedades seletivas

### Disabled

Envia o payload completo, sem `properties`.

### Validation

Executa dois exports com os mesmos filtros: completo e seletivo. O relatório continua usando o completo. O comparador grava um resultado sanitizado contendo:

- total de findings e conjunto de identidades;
- distribuições por estado e severidade;
- rankings Top 5 por VPR, severidade e ativos afetados;
- contagens de novos, ressurgidos e mitigados;
- contagens Exploitable e por framework;
- cobertura de nome, família, descrição, sinopse, solução, referências, CVSS e VPR.

O resultado é `PASSED` somente quando identidades, números e rankings relevantes coincidem e a cobertura dos campos seletivos não diverge da coleta completa.

### Enabled

Usa o export seletivo como fonte principal. Se a criação retornar HTTP 400 ou o contrato mínimo falhar, registra o motivo e repete a coleta uma vez sem `properties`. Outros erros de autenticação, permissão, rate limit ou timeout preservam sua classificação normal e não são mascarados pelo fallback.

## Interface web

Na edição do cliente, uma seção compacta “Coleta VM” exibirá:

- estratégia: combinada ou segmentada experimental;
- ativos por chunk: 100, 250, 500 ou 1000;
- propriedades seletivas: desativadas ou ativadas após validação;
- botão “Validar export otimizado”.

O botão abre uma confirmação informando que serão iniciados dois exports reais e enfileira uma execução de validação A/B. O progresso usa o mesmo cartão de execução e identifica `full` ou `selective`. O resultado final informa aprovado, reprovado ou fallback, sem exibir hostnames, IPs ou segredos.

## Compatibilidade e segurança

- Perfis e comandos existentes continuam válidos.
- O filtro temporal inferior continua sendo enviado à Tenable; o limite superior continua aplicado localmente ao dataset.
- Nenhum segredo é persistido em perfil, manifesto, progresso ou comparação.
- Validação A/B e modo seletivo nunca são acionados automaticamente em todos os clientes.
- O botão de cancelamento existente continua exigindo confirmação e UUID exato.

## Referências oficiais

- https://developer.tenable.com/reference/exports-vulns-request-export
- https://developer.tenable.com/docs/select-vulnerability-export-properties
- https://developer.tenable.com/docs/retrieve-vulnerability-data-from-tenableio
- https://developer.tenable.com/docs/refine-vulnerability-export-requests
