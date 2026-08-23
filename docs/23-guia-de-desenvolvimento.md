# Guia de desenvolvimento

## Preparação

```powershell
.\scripts\setup.ps1
$env:PYTHONPATH = (Join-Path $PWD 'src')
.\.venv\Scripts\python.exe -m pytest -q
```

Trabalhe em branch `codex/*` e, para mudanças extensas, use um worktree isolado.
Antes de editar, verifique o estado do Git e preserve alterações do usuário.

## Organização e dependências

- `domain` contém regras puras e modelos; não acessa HTTP, PostgreSQL ou DOCX.
- `application` coordena casos de uso e depende de contratos explícitos.
- `infrastructure` implementa APIs, persistência e serialização.
- `presentation` transforma datasets aprovados em documentos.
- `webapp` expõe operação local sem duplicar regra de negócio.

Evite cálculos independentes em cada renderizador. Uma métrica deve ser definida no
dataset e reutilizada por Word, interface, histórico e testes.

## Fluxo de alteração

1. Escreva um teste que demonstre o comportamento ausente ou incorreto.
2. Execute-o e confirme que falha pelo motivo esperado.
3. Implemente a menor mudança suficiente.
4. Execute testes focados.
5. Execute a suíte completa e validações estruturais.
6. Para DOCX, renderize uma prova e faça inspeção visual.
7. Revise `git diff --check` e o estado do Git antes de concluir.

Não use uma chamada real à Tenable como teste unitário. Clientes HTTP devem ser
testados com respostas e chunks sanitizados em `tests/fixtures`.

## Comandos de verificação

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe tools\validate_project_guidance.py --root .
.\.venv\Scripts\python.exe tools\audit_secret_leaks.py
git diff --check
```

O validador de orientação verifica a presença dos guias, `AGENTS.md`, skills,
frontmatter e links locais. Ele valida estrutura, não redação exata.

## Regras de domínio que exigem regressão

- intervalo `[início, fim)` e fuso do cliente;
- `OPEN`/`REOPENED` por `last_found` e `FIXED` por `last_fixed`;
- ressurgidas por `resurfaced_at`;
- exclusão de severidade informativa;
- identidade e vínculo por UUID;
- indicador geral de exploração separado dos frameworks;
- coleta geral independente de TAG;
- comparativo da mesma TAG no tempo;
- WAS opcional sem bloquear VM;
- `MAIN` explícito e histórico compatível;
- descarte seguro apenas depois da publicação validada.

## Tenable VM e WAS

Respeite o contrato assíncrono dos exports. Não interprete `total_chunks` como estado
final. Persista chunks conforme ficam disponíveis e preserve o manifesto parcial
para retentativa.

Qualquer nova lista de propriedades seletivas precisa ser validada contra payload
completo no tenant antes de virar padrão. O fallback seletivo é restrito às falhas
de contrato já previstas; não mascare autenticação, limite de taxa ou timeout.

Não inicie export real, servidor ou cancelamento sem necessidade e autorização.
Comandos reais devem exigir confirmação explícita.

## PostgreSQL

Mudanças de esquema entram como nova migration numerada em
`src/tenable_reports/infrastructure/postgresql_migrations`. Nunca edite uma migration
que já possa ter sido aplicada. O código precisa tolerar atualização incremental e
o teste deve cobrir ordem, idempotência esperada e leitura/escrita afetada.

Segredos do banco ficam em `credentials/database.env`; exemplos só contêm nomes de
variáveis e valores fictícios.

## Documentos Word

Preserve o conteúdo editorial dos modelos aprovados. Não introduza parágrafos,
tabelas ou títulos novos sem decisão explícita de produto. Datas e identificadores
dinâmicos devem ser substituídos sem destruir formatação de runs, cabeçalhos,
rodapés, imagens e quebras de seção.

Depois de alterar apresentação:

1. gere um DOCX com fixture determinística;
2. confirme estrutura por teste;
3. renderize com LibreOffice;
4. inspecione páginas críticas, tabelas, cortes e campos vazios;
5. mantenha a prova fora do Git quando contiver dados reais.

## Interface web

Rotas novas precisam de teste do servidor e do JavaScript que as consome. Mostre
erros de forma acionável, associe-os ao cliente e não retorne secrets ao navegador.
Operações destrutivas, como exclusão ou cancelamento de export, exigem confirmação
e alvo explícito.

## Documentação e instruções

Atualize os guias vigentes quando o comportamento mudar. Registros de fase podem
receber uma nota de estado atual, mas decisões históricas não devem ser apagadas.

As regras gerais para agentes ficam em [AGENTS.md](../AGENTS.md); pastas com riscos
específicos possuem um arquivo próprio. Skills do projeto vivem em `.agents/skills`
e devem ser referências curtas, com `name` igual à pasta, descrição iniciada por
`Use when` e links válidos para seus materiais de apoio.
