# Instruções para agentes

## Escopo

Este arquivo vale para todo o repositório. Um `AGENTS.md` mais próximo do arquivo
editado complementa ou restringe estas regras.

## Objetivo do projeto

Preservar o padrão editorial aprovado e gerar relatórios Tenable reproduzíveis para
múltiplos clientes. A aplicação publica dois DOCX gerais e, quando configurado,
relatórios VM compactos por TAG. O relatório geral nunca é filtrado pelas TAGs.

## Fontes de verdade

- Código e testes definem o comportamento executável.
- `docs/19` a `docs/23` descrevem o estado atual.
- `docs/01` a `docs/18` registram contratos e evolução histórica.
- PostgreSQL é a fonte operacional de histórico, documentos, tentativas e `MAIN`.
- Credenciais ficam somente em arquivos locais ignorados pelo Git.

## Antes de alterar

1. Verifique `git status` e preserve mudanças existentes do usuário.
2. Leia o guia vigente relacionado e o `AGENTS.md` do diretório.
3. Para mudança de comportamento, escreva primeiro um teste que falhe pelo motivo
   esperado.
4. Não inicie servidor, coleta real, export, cancelamento ou alteração de banco sem
   necessidade e autorização explícita.

## Regras invariáveis

- Períodos usam `[início, fim)` no fuso do cliente.
- Ativos/finding são ligados por UUID, nunca por IP ou hostname.
- `OPEN` e `REOPENED` usam `last_found`; `FIXED` usa `last_fixed`.
- Severidade informativa fica fora dos relatórios atuais.
- `Exploitable` geral não substitui os indicadores segregados por framework.
- WAS é opcional e sua falha não bloqueia VM.
- TAG recorta localmente a coleta geral e compara a mesma TAG no tempo.
- Dados intermediários pesados só são removidos depois de publicação validada e
  persistência do histórico compacto.
- Texto, títulos, tabelas e ordem editorial dos modelos não mudam sem aprovação.

## Segurança

Nunca exponha access key, secret key, senha, hostname, IP, pessoa, cliente ou e-mail
reais em código, fixtures, documentos versionados, logs de teste ou mensagens.
Sanitize qualquer evidência antes de compartilhar. Não leia ou imprima credenciais
para diagnosticar quando basta confirmar presença e resultado da autenticação.

## Verificação mínima

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe tools\validate_project_guidance.py --root .
.\.venv\Scripts\python.exe tools\audit_secret_leaks.py
git diff --check
```

Use testes focados durante o desenvolvimento e a suíte completa antes de declarar
conclusão. Mudanças em DOCX também exigem renderização com LibreOffice e inspeção
visual das páginas afetadas.

## Git e documentação

Use branches `codex/*` e commits pequenos. Não misture alterações não relacionadas,
não reescreva histórico e não descarte mudanças do usuário. Atualize os guias
vigentes quando mudar comportamento; preserve documentos históricos com uma nota
de estado atual em vez de apagar decisões anteriores.
