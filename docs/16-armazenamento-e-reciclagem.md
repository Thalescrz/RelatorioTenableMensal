# Armazenamento e reciclagem

## O que permanece

- Os relatórios DOCX publicados permanecem na pasta `reports` até exclusão explícita pelo analista.
- O PostgreSQL mantém resumos mensais, snapshots por rede, contagens por plugin e fingerprints compactos necessários aos comparativos.
- As migrations e os registros de auditoria permanecem versionados no banco.

## O que é temporário

Durante uma geração, `raw`, `snapshots`, `normalized` e `report-datasets` guardam a coleta e os dados intermediários. VM, WEB e normalizados usam gzip. Depois da validação dos DOCX e da confirmação do snapshot no PostgreSQL, essas quatro pastas são removidas apenas para o cliente e o `run_id` publicados.

Uma execução com falha mantém o staging por sete dias para diagnóstico e nova tentativa. Uma execução ativa ou marcada para retentativa não é removida.

## Estados da limpeza

- `NOT_REQUIRED`: ainda não há uma publicação elegível ou a reciclagem foi desativada para diagnóstico.
- `PENDING`: documentos e histórico foram confirmados e a limpeza será aplicada.
- `COMPLETE`: todos os temporários elegíveis foram removidos.
- `PARTIAL`: parte foi removida e há resíduos para nova tentativa.
- `FAILED`: nada pôde ser removido; os documentos continuam válidos.

## Interface web

O quadro **Armazenamento** mostra espaço livre, volume temporário, reserva da fila e pendências. O botão **Revisar limpeza** executa primeiro uma prévia. A confirmação seguinte aplica exatamente os candidatos apresentados; não existe opção para ignorar as proteções do histórico, de execução ativa ou de retentativa.

Se o PostgreSQL estiver indisponível, a interface recusa a limpeza porque não consegue comprovar as proteções.

## Recuperação de pendências

Depois de corrigir permissões ou liberar um arquivo em uso, abra a interface e use **Revisar limpeza** novamente. Execuções `PENDING`, `PARTIAL` ou `FAILED` voltam à prévia. A aplicação é idempotente: pastas já ausentes são ignoradas.

Os dados brutos de uma execução bem-sucedida não são arquivados. Para regenerar integralmente um relatório antigo é necessária uma nova coleta na API; os snapshots compactos existem somente para comparativos futuros.
