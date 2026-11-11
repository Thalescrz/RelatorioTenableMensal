# Monthly Client Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Execução nesta sessão, preservando alterações preexistentes no checkout.

**Goal:** Persistir a seleção dos clientes que participam dos novos lotes mensais.

**Architecture:** Estender a política existente e compartilhar a seleção entre
prévia e executor. Interface usa a mesma API de configuração, sem novo serviço.

**Tech Stack:** Python, JavaScript nativo, HTML/CSS, pytest e node:test.

**Spec:** `docs/superpowers/specs/2026-11-10-monthly-client-selection-design.md`

## Global Constraints

- `null` mantém todos os elegíveis; `[]` nunca seleciona todos.
- Somente clientes ativos com credenciais prontas entram no lote.
- Sem coleta real, migração, alteração operacional ou mudança na geração manual.
- Preservar o lote existente por competência e as mudanças já presentes no Git.

## Review Focus

- Configuração legada sem o novo campo mantém o comportamento anterior.
- Lista vazia ou inteiramente inelegível não enfileira nenhum cliente.
- Prévia e execução aplicam a mesma seleção; novos clientes não ampliam lista fixa.
- Edição de horário e ativação não apagam a seleção; carga inválida não é salva.
- Repetir competência existente reutiliza o lote, mesmo após mudar a seleção.

## Task 1: Política, prévia e executor

Files: `config/monthly_schedule.py`, `application/monthly_schedule.py`,
`application/monthly_batch.py`, `tests/test_monthly_schedule.py`,
`tests/test_monthly_batch.py`.

- [x] Escrever e executar testes de compatibilidade, seleção, rejeição de IDs,
  lista vazia, salvamento parcial e idempotência. Confirmar falhas esperadas.
- [x] Implementar `selected_client_ids: tuple[str, ...] | None` e a seleção comum.
- [x] Executar os testes focados; esperado: todos passam sem rede ou banco real.

## Task 2: Interface e orientação

Files: `webapp/static/{index.html,app.css,app.js,monthly_schedule.js}`,
`tests/test_monthly_schedule_ui.js`, `CONTEXTO.md`, `docs/22-guia-operacional.md`.

- [x] Cobrir modos todos/selecionados e rascunho vazio por testes JavaScript.
- [x] Adicionar seletor, checklist com nomes, marcar/limpar e salvar seleção.
- [x] Atualizar guias e verificar a interface com dados fictícios, sem iniciar
  aplicação operacional ou gravar configurações reais.
- [x] Executar suíte Python completa, testes JavaScript, validação de orientação,
  auditoria de segredos e `git diff --check`. Revisar o diff desta alteração.

O ciclo Git de integração/publicação não faz parte deste pedido. O checkout já
contém mudanças de outro trabalho; elas não serão descartadas nem incorporadas
num commit desta funcionalidade.

## Verificação concluída

- 87 testes relacionados passaram antes da suíte completa.
- Suíte completa: 1444 testes e 59 subtestes passaram.
- JavaScript: 19 testes passaram.
- Navegador com dados fictícios: seleção, salvar/reabrir, lista vazia, retorno a todos,
  cliente removido e edição de horário em outra janela; desktop e tela pequena.
- Revisão independente identificou dois casos na interface; ambos reproduzidos
  e corrigidos com testes. Sem pendências da revisão.
- Orientação do projeto validada; auditoria de segredos: zero vazamentos; diff sem
  erros de whitespace. Configurações e coletas operacionais não foram alteradas.
