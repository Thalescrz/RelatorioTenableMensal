---
name: validating-tenable-report-data
description: Use when validating Tenable report counts, rankings, periods, tables, charts, or DOCX content against the platform and the normalized data contract.
---

# Validating Tenable Report Data

Use esta skill para explicar ou investigar divergências sem ajustar números para
“bater” visualmente.

## Procedimento

1. Identifique cliente, execução, período `[início, fim)`, documento e tabela.
2. Leia o [contrato de dados](references/data-contract.md) da população envolvida.
3. Confirme escopo: geral, WAS ou uma TAG específica. TAG não pode afetar o geral.
4. Confirme estados, severidades e o campo temporal correto antes de comparar.
5. Reconcile em camadas: raw/manifesto, normalizado, dataset, histórico e DOCX.
6. Para rankings, valide primeiro a população; depois empate, VPR, severidade e
   ativos afetados.
7. Diferencie zero legítimo, ausência de população, campo não coletado e falha.
8. Registre a evidência mínima: filtros usados, contagens, diferença e camada onde
   ela surgiu, sempre sanitizando ativos e pessoas.

## Regras críticas

- `OPEN`/`REOPENED`: `last_found`; `FIXED`: `last_fixed`.
- Ressurgida: `REOPENED` com `resurfaced_at` no período.
- `Informational` é excluída.
- `Exploitable` geral usa `plugin.exploit_available`; frameworks usam flags próprias.
- IP e hostname não conciliam identidade; use UUID.
- Top 5 é ranking local e pode não coincidir com uma ordenação padrão da interface.
- WAS vazio não invalida VM.
- Sem `MAIN` compatível, valide o mês corrente e trate o comparativo como ausente.

Não faça coleta real apenas para conferir documentação; reutilize dataset e
manifesto existentes quando íntegros. Se uma nova chamada for indispensável, peça
autorização e preserve período, perfil e versão de métricas.
