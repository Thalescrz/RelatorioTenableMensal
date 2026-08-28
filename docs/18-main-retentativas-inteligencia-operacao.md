# Operação de main, retentativas e inteligência

## Estado atual — 2026-08-28

A referência `MAIN` também cobre a identidade dos documentos por TAG, garantindo
comparação da mesma TAG no tempo. Retentativas VM preservam chunks íntegros e
distinguem job criado, retomado ou fornecido. Cancelamento automático é restrito a
job criado pela execução atual e sem progresso. A retenção atual mantém DOCX e
histórico compacto; raw, snapshots, normalizados e datasets pesados de uma execução
bem-sucedida são removidos, enquanto falhas permanecem por prazo curto.

Este guia descreve as ações disponíveis na interface web e os cuidados para manter
comparativos mensais determinísticos.

## Relatório main

O `main` é a geração escolhida como referência oficial de um cliente, competência,
escopo, timezone e versão de métricas. O relatório do mês seguinte consulta somente
o `main` do mês imediatamente anterior.

- A primeira geração mensal válida de uma competência torna-se `main`
  automaticamente.
- Uma nova geração do mesmo mês não substitui a anterior sem ação do analista.
- Na lista de relatórios do cliente, use **Definir como main** para escolher a versão
  revisada e informe o responsável e o motivo.
- A promoção afeta somente relatórios gerados depois da mudança. Documentos antigos
  continuam vinculados à referência usada quando foram criados.
- Sem `main` no mês imediatamente anterior, o documento customizado mantém tabelas
  atuais e baselines disponíveis e informa que não há histórico para comparação.

## Exclusão permanente

A exclusão iniciada pela interface é física, irreversível e auditada.

- A prévia informa período, documentos, arquivos e bytes que serão removidos.
- O analista informa o motivo e digita exatamente `EXCLUIR`.
- Para excluir um `MAIN`, é obrigatório selecionar uma geração substituta
  compatível; a operação não permite criar uma lacuna histórica.
- A substituta passa a ser `MAIN` na mesma operação.
- A exclusão é bloqueada enquanto existir geração ativa para o mesmo cliente.
- DOCX gerais, customizados e por TAG, manifesto de publicação, histórico compacto
  e registros PostgreSQL relacionados ao conjunto são removidos.
- O serviço aceita somente arquivos resolvidos dentro da raiz `data` configurada.
- Os arquivos passam por `data/.purge` antes da transação. Falha no disco ou no
  banco restaura o estágio; depois do commit, não há restauração pela aplicação.

O fluxo anterior de exclusão lógica permanece apenas para registros legados já
marcados; novas exclusões usam exclusivamente o procedimento permanente acima.

## Retentativas

O botão **Tentar novamente** aparece em jobs com falha. A nova execução preserva o
cliente, o modo e a janela temporal originais e registra o job anterior como origem.

Na automação mensal, somente falhas transitórias são repetidas. Falhas de
autenticação não são repetidas automaticamente e devem ser resolvidas em
`credentials/<cliente>.env` antes de uma nova tentativa.

WAS possui uma retentativa independente:

- manual: a execução preserva VM/assets/TAG/Cloud e aguarda **Continuar sem WEB**
  ou **Tentar WEB novamente**;
- mensal automática: os documentos VM são publicados com alerta e fica disponível
  somente **Tentar WEB novamente**;
- a retentativa reaproveita chunks WAS já persistidos e nunca reinicia os exports
  VM ou a coleta Cloud;
- quando o conjunto já foi publicado, o contexto VM é reconstruído pelo snapshot
  compacto, os DOCX VM/TAG são regenerados em staging e substituídos junto com o
  manifesto somente após validação e persistência;
- se o hash das métricas VM mudar, a reparação é recusada e o conjunto anterior
  permanece intacto.

## Retenção e recuperação de espaço

A tela principal mostra espaço total, usado, livre e consumo por cliente. A limpeza
manual usa exclusivamente o plano calculado pelo servidor e pede confirmação.

Horizontes padrão:

- raw de execução com falha: 7 dias;
- raw de execução concluída: 60 dias;
- snapshots e normalizados: 90 dias;
- datasets e documentos: 395 dias.

Não são removidos runs ativos, pendentes de retentativa, sem histórico confirmado ou
documentos/datasets protegidos como `main`. Use `--no-apply-retention` ao executar a
orquestração se for necessário suspender temporariamente a limpeza automática.

## Backfill inicial

Na interface web, abra **Admin** e clique em **Analisar**. Essa etapa é somente
leitura e separa os registros em promoções seguras, decisões manuais, ignorados e
referências já definidas. O botão **Aplicar promoções seguras** permanece desabilitado
quando não há nada elegível e exige a frase de confirmação mostrada na tela.

O backfill nunca substitui uma referência `main` existente. Alertas de seleção manual
devem ser resolvidos no histórico do cliente, usando **Definir MAIN**.

Como alternativa de manutenção, a mesma análise pode ser feita por linha de comando:

```powershell
.\.venv\Scripts\python.exe -m tenable_reports backfill-report-main `
  --database-env-file .\credentials\database.env `
  --dry-run
```

Revise os alertas `MAIN_SELECTION_REQUIRED`. Uma competência com um único candidato
válido pode ser selecionada automaticamente; havendo vários candidatos, somente a
versão comprovadamente usada como histórico anterior é escolhida. Ambiguidades ficam
para decisão manual.

Depois da revisão, a aplicação equivalente por linha de comando é:

```powershell
.\.venv\Scripts\python.exe -m tenable_reports backfill-report-main `
  --database-env-file .\credentials\database.env `
  --apply
```

O backfill registra ator `system-backfill` e motivo `migração inicial`. Ele não
modifica nem exclui DOCX, datasets, snapshots ou arquivos legados.

## Verificação operacional recomendada

1. Teste a API de cada cliente na interface.
2. Gere primeiro um relatório de janela pequena para um único cliente.
3. Confirme os dois DOCX e o manifesto antes de promover uma versão manualmente.
4. Revise alertas de módulos omitidos e mensagens de ausência de dados.
5. Só então habilite a execução da carteira completa ou o agendamento mensal.
