# Consolidação da documentação e contexto do projeto — Design

**Status:** aprovado para implementação em 2026-09-15  
**Base verificada:** `main` no commit `277750f`  
**Escopo:** documentação Markdown versionada, instruções de agentes, skills locais e validação documental

## Problema

O projeto acumulou documentação de três naturezas diferentes:

1. guias vigentes que descrevem o produto e a operação atuais;
2. contratos técnicos originados durante as fases de construção;
3. especificações e planos que registram decisões tomadas antes de cada entrega.

Esses grupos são úteis, mas hoje não existe um ponto único que permita retomar o
projeto rapidamente e saber quais arquivos prevalecem. Além disso, documentos
históricos podem ser interpretados como instruções atuais mesmo quando uma decisão
posterior os substituiu.

## Objetivos

- criar `CONTEXTO.md` na raiz como porta de entrada para retomadas humanas e por
  agentes;
- atualizar os guias vigentes a partir do código e dos testes atuais;
- preservar a rastreabilidade dos registros históricos sem tratá-los como estado
  presente;
- tornar explícita a hierarquia das fontes de verdade;
- corrigir referências cruzadas e links locais;
- incorporar o novo contexto à validação automatizada do projeto;
- manter toda evidência versionada sanitizada.

## Fora de escopo

- alterar comportamento da aplicação, modelos de dados ou formato dos DOCX;
- executar coleta real, migração, publicação de relatório ou modificação de banco;
- registrar a carteira real de clientes, credenciais, e-mails, UUIDs, hostnames,
  IPs ou estados operacionais transitórios;
- reescrever decisões históricas como se sempre tivessem refletido o estado atual;
- transformar `CONTEXTO.md` em cópia integral dos demais guias.

## Hierarquia documental

Quando houver divergência, a precedência será:

1. código executável, testes e migrations aplicadas;
2. `AGENTS.md` aplicável ao diretório para regras de trabalho e segurança;
3. `CONTEXTO.md` como resumo de retomada e mapa das fontes vigentes;
4. `DESIGN.md` e `docs/19` a `docs/23`, cada um em seu domínio;
5. skills e runbooks locais para procedimentos especializados;
6. `docs/01` a `docs/18` como contratos e registros de evolução;
7. `docs/superpowers/specs` e `docs/superpowers/plans` como histórico de decisões
   e intenção de implementação.

O `CONTEXTO.md` não substitui o código nem os guias detalhados. Ele deve dizer qual
é o estado consolidado, onde encontrar o contrato completo e quais limites não
podem ser inferidos.

## Estrutura de `CONTEXTO.md`

O novo documento terá as seguintes seções:

1. **Identidade e finalidade** — objetivo do produto e público operador;
2. **Estado consolidado** — data, commit-base verificado e capacidades atuais;
3. **Entregáveis** — relatórios geral, customizado, TAG e Cloud;
4. **Arquitetura resumida** — camadas, fluxo e fronteiras entre coleta,
   normalização, apresentação e persistência;
5. **Módulos e comportamento de falha** — VM, WAS e Cloud independentes;
6. **Controle documental** — padrões globais, distribuição comum e adicionais por
   cliente;
7. **Orquestração** — lotes duráveis, concorrência remota, montagem serial,
   checkpoints, pausa, parada e retentativa;
8. **Dados e métricas invariáveis** — período, UUID, estados, severidade,
   histórico e `MAIN`;
9. **Interface e operação** — capacidades do painel, atualização, alertas e estado
   visual;
10. **Segurança e privacidade** — secrets, dados identificáveis e limites de logs;
11. **Ambiente e comandos** — instalação, execução local e verificações seguras;
12. **Fluxo Git** — `main`, uma branch `codex/*` por ciclo, validação, merge,
    publicação e limpeza;
13. **Mapa documental** — fontes vigentes e arquivos históricos;
14. **Pendências e limites conhecidos** — somente limitações confirmadas no código
    ou nos guias atuais;
15. **Regra de manutenção** — quando e como atualizar o contexto.

O estado consolidado será estável e versionável. Contadores de clientes, execução
ativa, falhas correntes e outros dados que mudam sem commit ficarão fora do arquivo.

## Política por grupo de arquivos

| Grupo | Tratamento |
|---|---|
| `README.md` | Manter como introdução e início rápido; apontar para `CONTEXTO.md` e remover duplicação excessiva. |
| `CONTEXTO.md` | Criar como resumo de retomada, com links para contratos detalhados. |
| `DESIGN.md` | Atualizar status, arquitetura e decisões estruturais confirmadas. |
| `docs/README.md` | Tornar o índice completo, explicar precedência e classificar cada grupo documental. |
| `docs/19` a `docs/23` | Revisar integralmente contra código, testes, migrations e interface atuais. |
| `AGENTS.md` de raiz e subdiretórios | Corrigir somente regras desatualizadas ou links; preservar instruções de segurança e escopo. |
| Skills e runbooks locais | Alinhar procedimentos ao comportamento vigente sem iniciar operações reais. |
| `templates/corporate/README.md` | Conferir o contrato editorial e atualizar referências vigentes. |
| `docs/01` a `docs/18` | Preservar o corpo; adicionar aviso uniforme de referência histórica e links para contexto/guias atuais. |
| `docs/superpowers/specs` e `docs/superpowers/plans` existentes | Preservar o corpo; adicionar aviso de registro de decisão, sem declarar execução concluída. |

## Avisos históricos

Os documentos históricos receberão um bloco curto após o título. O texto informará
que o arquivo registra uma etapa ou intenção passada, pode conter decisões
posteriormente alteradas e não deve ser usado isoladamente para determinar o estado
atual. O bloco apontará para `CONTEXTO.md` e para `docs/README.md` usando links
relativos válidos para cada diretório.

O aviso não classificará automaticamente um plano como concluído, parcial ou
abandonado. Essa classificação só será adicionada quando houver evidência direta em
commit, teste ou guia vigente.

## Fontes usadas na revisão

A atualização será derivada de:

- código e testes da branch de documentação;
- `README.md`, `DESIGN.md`, `docs/19` a `docs/23` e `AGENTS.md` atuais;
- comandos e opções expostos pela CLI sem realizar chamadas externas;
- migrations PostgreSQL e contratos persistidos;
- rotas e recursos estáticos da webapp;
- templates e renderizadores documentais;
- skills e runbooks versionados.

Memórias de sessões e estados do dashboard podem ajudar a localizar assuntos, mas
não serão tratados como prova do comportamento atual sem confirmação no repositório.

## Validação automatizada

`tools/validate_project_guidance.py` passará a exigir `CONTEXTO.md`. O validador
continuará verificando ausência de marcadores de rascunho nos guias vigentes e
passará a verificar links locais em todos os arquivos Markdown do repositório,
ignorando diretórios técnicos e temporários não documentais.

Os registros históricos poderão conter checklists ainda abertos; por isso a regra
de marcadores de rascunho continuará restrita às fontes vigentes. A ampliação de
links será coberta por testes com arquivos atuais, históricos e diretórios
ignorados.

## Segurança

- todos os exemplos usarão nomes e identificadores fictícios;
- nenhuma credencial ou valor de configuração local será lido para redigir os
  documentos;
- exemplos de caminhos poderão indicar convenções, nunca valores secretos;
- resultados operacionais mutáveis serão descritos por regra, não por cliente;
- a auditoria de vazamentos continuará obrigatória antes da conclusão.

## Verificação

A entrega será aceita somente com:

1. inventário de Markdown versionado revisado;
2. links locais válidos em todas as fontes documentais;
3. `CONTEXTO.md` incluído no índice da raiz e no índice de `docs`;
4. teste de regressão do validador documental;
5. suíte completa do projeto aprovada;
6. `tools/validate_project_guidance.py --root .` aprovado;
7. `tools/audit_secret_leaks.py` sem vazamentos;
8. `git diff --check` sem erros;
9. busca final sem marcadores de rascunho nas fontes vigentes;
10. revisão do diff para confirmar que registros históricos não foram
    retroativamente reescritos.

## Critérios de aceite

- uma pessoa sem contexto anterior encontra em `CONTEXTO.md` o estado, os limites,
  os comandos e as fontes detalhadas do projeto;
- os guias atuais não contradizem o comportamento testado;
- cada documento histórico deixa claro que não é fonte única do estado presente;
- planos antigos continuam preservados como evidência de intenção e decisão;
- a documentação não contém dados identificáveis ou secrets;
- futuras mudanças conseguem detectar automaticamente a ausência do contexto ou um
  link local quebrado.
