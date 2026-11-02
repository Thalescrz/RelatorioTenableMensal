# Correcoes pos-coleta mensal - Lista de afazeres

> Este arquivo registra correcoes aprovadas para serem implementadas somente depois
> da coleta mensal em andamento. Nenhum item desta lista autoriza interromper,
> retentar ou alterar o lote atual.

## Agendamento mensal no Windows

- [x] Definir explicitamente o diretorio de trabalho da tarefa mensal como a raiz
  do projeto e tornar `scripts/run_monthly_orchestration.ps1` independente do
  diretorio herdado pelo Agendador de Tarefas.
- [x] Garantir que a descoberta do LibreOffice portatil use um caminho absoluto
  resolvido a partir da raiz do projeto, sem depender do diretorio corrente.
- [x] Preservar em arquivo de log sanitizado a saida padrao, a saida de erro, o
  horario e o codigo de saida de cada execucao automatica, sem registrar
  credenciais nem dados de clientes.
- [x] Adicionar teste de regressao que execute somente o preflight a partir de um
  diretorio externo ao projeto e comprove que o LibreOffice continua sendo
  localizado. O teste nao pode iniciar coleta real.

### Evidencia que originou a pendencia

Em 1 de novembro de 2026, a tarefa do Windows foi acionada no horario configurado,
mas encerrou com codigo `2` antes de criar um lote. A mesma validacao de ambiente
passou quando executada a partir da raiz do projeto e falhou a partir do diretorio
padrao da tarefa com `ENVIRONMENT_PREFLIGHT_BLOCKED (LIBREOFFICE_NOT_FOUND)`.

## Fluxo administrativo do agendador

- [x] Corrigir o botao **Validar sem executar** para sempre recuperar seu estado
  visual depois da resposta, inclusive em falha, sem acessar `event.currentTarget`
  depois de uma operacao assincrona.
- [x] Revisar o fluxo de ativacao e sincronizacao para deixar claro quando a
  configuracao apenas foi salva e quando a tarefa do sistema operacional foi
  efetivamente criada ou atualizada.
- [x] Cobrir o fluxo com testes de interface para sucesso, falha e timeout, sem
  executar a tarefa mensal nem iniciar uma coleta.

## Competencia do mes anterior no fluxo manual

- [x] Separar explicitamente na interface as opcoes **Mes-calendario anterior** e
  **Um mes movel ate agora**. O rotulo **Ultimo mes** nao pode continuar
  representando silenciosamente um intervalo movel.
- [x] Fazer a opcao de mes-calendario anterior resolver os limites no fuso do
  cliente como `[primeiro dia do mes anterior, primeiro dia do mes atual)`, com
  `period_id` canonico `YYYY-MM` e sufixo mensal nos documentos.
- [x] Preservar o mes movel como opcao pontual distinta. Esse modo deve continuar
  exibindo o intervalo completo fora do caso dia 1 para dia 1. Pela regra
  aprovada, esse caso especial participa da competencia do mes inicial em qualquer
  horario.
- [x] Exibir, antes da confirmacao, o periodo efetivo, o fuso e a identidade
  editorial que serao usados pela coleta.
- [x] Adicionar testes de dominio, referencia, reparacao e apresentacao cobrindo
  uma execucao entre o dia 1 de meses consecutivos em horario nao nulo.
- [x] Regularizar os conjuntos elegiveis existentes como `2026-10`, preservando os
  instantes reais, criando backup, reconciliando checkpoints e republicando os 70
  DOCX com `OUT26` e o ultimo dia civil de outubro.

### Evidencia que originou a pendencia

Uma execucao manual iniciada no primeiro dia do mes usou a opcao **Ultimo mes** e
foi resolvida como um mes movel entre dois instantes com o mesmo horario. O
comportamento e coerente com o modo atual, mas diverge da intencao operacional de
coletar o mes-calendario anterior e gerou identificador e nomes de arquivo de
intervalo, em vez de `YYYY-MM` e do sufixo mensal.

Decisao posterior aprovada: quando os limites locais caem no dia 1 de meses
consecutivos, o intervalo e competencia do mes inicial mesmo que os horarios nao
sejam meia-noite. Os horarios continuam sendo evidencia de auditoria, nao rotulo
editorial.

## Cloud Security: publicacao inicial, retry e MAIN

- [x] Garantir que o registro pai da execucao exista antes da publicacao do
  snapshot Cloud, ou adiar essa publicacao para uma transacao posterior ao registro
  da execucao. O snapshot nao pode falhar por ordem de persistencia interna.
- [x] Preservar no estado operacional o erro sanitizado exato de publicacao Cloud,
  sem reduzir a causa a uma mensagem generica e sem incluir dados sensiveis.
- [x] Ao terminar uma retentativa seletiva, reconciliar manifesto, documentos,
  snapshot e estados dos componentes antes de avaliar a elegibilidade para `MAIN`.
- [x] Se todos os componentes obrigatorios estiverem concluidos, os documentos
  forem validos e ainda nao existir `MAIN` para a mesma chave, promover o proprio
  `run_id` por `auto_promote_if_empty`. Nunca substituir automaticamente um `MAIN`
  ja existente.
- [x] Manter o `MAIN` anterior intacto durante falha, parcialidade ou retry em
  andamento e apresentar uma justificativa explicita quando o conjunto completo
  continuar inelegivel.
- [x] Adicionar teste de regressao para o fluxo: publicacao inicial parcial por
  falha Cloud, retry somente do Cloud, conjunto final completo, um unico documento
  Cloud valido e promocao automatica para `MAIN` sem repetir VM ou WAS.
- [x] Adicionar teste que comprove que uma retentativa completa nao substitui um
  `MAIN` ja selecionado para a mesma chave.

### Evidencia que originou a pendencia

Dois conjuntos com VM e WAS concluidos ficaram parciais por falha na etapa de
publicacao do snapshot Cloud. A retentativa seletiva concluiu o Cloud e atualizou os
documentos e o estado dos componentes, mas o caminho de retry nao voltou a executar
a reconciliacao de `MAIN`. Um conjunto precisou de promocao manual; o outro ainda
aparecia completo e sem `MAIN` na verificacao em modo leitura.

## Validacao antes de concluir

- [x] Executar testes focados do agendador, preflight e interface administrativa.
- [x] Executar testes focados de periodo manual, publicacao Cloud, retry seletivo e
  promocao segura de `MAIN`.
- [x] Executar a verificacao minima definida em `AGENTS.md`.
- [ ] Confirmar em uma validacao sem coleta que a tarefa aponta para a raiz correta,
  encontra o LibreOffice e produz log sanitizado.
- [x] Atualizar `CONTEXTO.md`, `docs/22-guia-operacional.md` e
  `docs/25-implantacao-windows-linux.md` no mesmo ciclo da implementacao.

Validacao ambiental em 01/11/2026: o launcher foi chamado de fora da raiz com
`-PreflightOnly`, resolveu a raiz e localizou o LibreOffice 26.2.4.2 pelo caminho
absoluto, sem iniciar coleta. O preflight terminou bloqueado por PostgreSQL
indisponivel e, por isso, nao houve um disparo automatico completo capaz de gerar
o JSONL real. A escrita e a sanitizacao do log foram comprovadas por testes; a
confirmacao externa completa permanece pendente ate o banco estar disponivel.

