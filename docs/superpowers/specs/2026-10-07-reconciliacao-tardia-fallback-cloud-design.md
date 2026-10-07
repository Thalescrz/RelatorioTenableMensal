# Reconciliação tardia VM/TAG e fallback de correção Cloud

> **Registro de decisão:** este arquivo preserva o desenho aprovado para esta
> entrega. Consulte o [contexto atual](../../../CONTEXTO.md) e o
> [índice da documentação](../../README.md) para o estado vigente.

## Objetivo

Tornar reproduzível a correção conservadora de competências VM coletadas depois
da tolerância de fechamento, propagar a mesma regra aos recortes por TAG e ampliar
o fallback Cloud para remediações de containers sem inventar dados ausentes.

## Reconciliação tardia VM

A reconciliação é aplicada somente quando `collection_timing.status` seria
`LATE`, isto é, quando a coleta termina depois de `period.end_at` mais a tolerância
`reporting.late_collection_grace_days` do perfil.

Para a competência solicitada, um finding aberto pode ter `last_found` deslocado
para depois do limite final pela continuidade de scans. Ele volta a compor a
fotografia do período somente quando toda a evidência abaixo existe no snapshot
preservado:

- estado `OPEN` ou `REOPENED`;
- ativo ligado por UUID;
- severidade permitida pelo perfil;
- `last_found >= period.end_at`;
- `first_found < period.end_at`;
- para `REOPENED`, `resurfaced_at < period.end_at`.

O algoritmo ajusta apenas a data efetiva usada para classificar a ocorrência no
período. A evidência original permanece imutável no snapshot compacto. Findings
`FIXED`, órfãos, informativos ou ressurgidos depois do término não são convertidos.
O dataset registra contagem total e por severidade, sem chaves, hosts ou IPs.

Essa é uma reconstrução conservadora do estado aberto confirmado pela captura
tardia; não equivale a uma fotografia bruta feita exatamente na virada do mês.

## TAG

O dataset geral é reconciliado antes de qualquer recorte. Cada relatório por TAG
continua reutilizando a coleta geral e a associação histórica da própria TAG por
UUID. A mesma correção temporal alcança a TAG somente quando o ativo pertencia à
TAG naquela competência; a associação do mês seguinte nunca substitui a do mês
anterior. Competências sem escopo histórico real não são inventadas.

## Histórico e manutenção dos documentos já publicados

Snapshots históricos novos são formados a partir dos findings reconciliados, de
modo que totais, severidades, novos, fingerprints, evolução por plugin e Top 20 por
TAG permaneçam coerentes.

Para setembro de 2026, a manutenção reconstrói agosto a partir do snapshot compacto
imutável e o usa somente em memória como predecessor corrigido. O registro histórico
original não é reescrito. Os documentos de setembro são substituídos de forma
atômica, com backup anterior e reconciliação do manifesto/catálogo `MAIN`.

## Fallback Cloud

`FixedBy` continua sendo uma fonte opcional e isolada. Quando ela não estiver
disponível, uma vulnerabilidade pode ser considerada corrigível por remediação
somente se a evidência Cloud relacionar explicitamente o mesmo ID de recurso e a
mesma CVE observada no inventário normalizado.

A correlação procura correspondências em VMs e imagens de container. Não usa nome,
digest, IP ou aproximação textual. Uma remediação genérica sem recurso + CVE não
entra nos rankings. A cobertura de containers considera separadamente
`container_image_fix_versions` e `vulnerability_remediations`, resultando em
`COMPLETE`, `PARTIAL` ou `UNAVAILABLE` conforme as fontes realmente preservadas.

Relatórios Cloud antigos só podem ganhar dados pelo fallback quando o snapshot
preservado contém a evidência necessária. Ausência de evidência não autoriza nova
consulta real nem preenchimento estimado; o documento mantém o estado de cobertura
correspondente.

## Publicação e validação

Somente relatórios afetados são regenerados. Os DOCX oficiais são reconstruídos a
partir dos snapshots e perfis preservados, sem nova coleta VM, WAS ou Cloud. Antes
da substituição, a ferramenta valida identidade, período, hashes, ausência de jobs
concorrentes e equivalência das métricas atuais. Todos os arquivos alterados são
renderizados com LibreOffice e revisados visualmente.

