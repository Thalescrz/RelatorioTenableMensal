# Seleção permanente de clientes da automação mensal

Solicitação aprovada: selecionar os clientes em **Admin → Automação mensal**,
independentemente da ativação geral e da geração manual.

O agendamento terá dois modos: **Todos os clientes elegíveis** (compatível com a
configuração anterior) e **Somente os selecionados**. A configuração persistirá
`selected_client_ids`: `null` significa todos; uma lista explícita limita a seleção;
`[]` significa nenhum, nunca todos. Novos cadastros não entram numa lista explícita.

Uma única regra selecionará clientes ativos com credenciais prontas tanto na prévia
quanto na criação efetiva do lote. A interface exibirá nomes e motivo de
inelegibilidade. IDs desconhecidos serão recusados ao salvar; IDs removidos de uma
seleção antiga não ampliarão o escopo. Salvar horário sem enviar a seleção preserva
a lista existente.

Salvar não inicia coleta nem altera o agendador do sistema. O launcher já lê a
configuração a cada disparo, portanto mudar apenas a seleção não exige recriar a
tarefa. Lotes existentes continuam idempotentes por carteira e competência e não
recebem clientes adicionais quando a política é alterada.

Alternativas consideradas: usar a ativação geral do cliente afetaria operações
manuais; uma seleção transitória não resolveria os próximos meses. A seleção
persistida na política atende ao pedido aprovado, sem alterar VM/WAS/Cloud,
documentos, credenciais ou banco operacional.
