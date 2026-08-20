# Perfis declarativos e variações — Fase 7

**Concluída em:** 2026-08-13

## Contrato implementado

O primeiro DOCX possui um núcleo invariável: `summary`, `infrastructure`, `vm_top5`,
`was` e `was_top5`. O perfil não pode remover, trocar ou acrescentar itens nesse
conjunto; toda variação comprovada pertence a `report.intelligence_modules` e,
portanto, ao segundo DOCX.

Os IDs de inteligência aceitos são enumerados no código. Um ID desconhecido falha
na validação do perfil, impedindo condicionais como `if cliente == ...`. Os módulos
`was_unsupported_tech` e `cloud_container_images` também exigem, respectivamente,
`scope.was.enabled=true` e `scope.cloud_security.enabled=true`.

Quando um módulo válido está habilitado, mas o dataset não possui sua população,
o texto, tabela ou gráfico não é criado. A CLI informa a omissão como
`NO_COMPATIBLE_DATA` ou `NO_COMPATIBLE_HISTORY`; zeros nunca substituem ausência.

`vm_top5_include_output` e `was_top5_include_output` permanecem desligados por
padrão. Se uma dessas opções for ligada sem a cobertura correspondente registrada
em `source_coverage`, a geração falha antes de publicar o DOCX.

## Perfis de prova

- `client-profile-vm-standard.json`: apenas o núcleo obrigatório; sem módulos de
  inteligência e sem capacidades WAS/Cloud declaradas.
- `client-profile-intelligence-expanded.json`: todos os módulos atualmente
  implementados, com capacidades WAS e Cloud Security declaradas.

Os dois usam o mesmo `client_id`, tenant, dataset canônico e configuração do
relatório-base. Assim, a prova isola somente a variação editorial do segundo DOCX.

## Evidência de validação

- 82 testes offline aprovados;
- Office MCP: zero avisos estruturais nos quatro DOCX;
- relatórios-base: 81 títulos, 471 parágrafos, 28 tabelas e 3 imagens em ambos;
- renderização LibreOffice: 13 páginas do base, 2 do customizado essencial e 12 do
  customizado ampliado;
- as 13 imagens de página dos dois relatórios-base tiveram hashes idênticos;
- inspeção visual integral sem clipping, sobreposição ou tabela quebrada.

A auditoria de acessibilidade não encontrou achados altos. Permanecem avisos
médios herdados do template: rótulos `Descrição:` estilizados como Heading 4 e
tabelas de layout, inclusive em cabeçalho/rodapé, sem marcação `w:tblHeader`.
Esses avisos não causaram defeitos visuais e não foram alterados nesta fase para
preservar a fidelidade editorial já aprovada.

## Gate seguinte

A Fase 8 deve integrar WAS em produção. Ela deve confirmar os campos exatos no
tenant, publicar snapshots imutáveis e preencher o bloco WEB padrão já existente,
sem alterar os textos manuais preservados no primeiro DOCX.
