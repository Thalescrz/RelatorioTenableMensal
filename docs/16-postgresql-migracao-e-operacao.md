# PostgreSQL: migração e operação

O PostgreSQL local passa a ser a fonte de verdade para o estado estruturado da
solução. Os arquivos raw, snapshots JSON/JSONL, datasets, DOCX, PDF e logs
continuam no disco como evidências imutáveis; seus caminhos, hashes e tamanhos
são catalogados no banco.

## O que fica no PostgreSQL

- snapshots mensais e contrato de compatibilidade do histórico;
- execuções de relatório e sua competência;
- publicações, documentos, hashes e estado de distribuição;
- orquestrações, resultado isolado por cliente e eventos;
- catálogo pesquisável de artefatos;
- cópia JSON das tabelas SQLite de auditoria antigas;
- registro de origem e hash de cada SQLite importado.

Os SQLite existentes não são apagados. Eles permanecem disponíveis para
auditoria e recuperação até uma decisão explícita de retenção.

## Provisionamento local

1. Prepare o Python e as dependências:

```powershell
.\scripts\setup.ps1
```

2. Execute o bootstrap:

```powershell
.\scripts\bootstrap_postgresql.ps1
```

O script pede a senha do superusuário `postgres` de forma oculta. Como alternativa
para execução assistida, ela pode ser colocada temporariamente em
`credentials/postgresql-admin.env`; o script limpa esse valor ao terminar. O
script gera uma senha aleatória para
`tenable_reports_app`, cria `credentials/database.env`, provisiona o banco
`tenable_reports`, aplica as migrations, importa o legado e executa a validação
final.

O papel da aplicação é `NOSUPERUSER`, `NOCREATEDB`, `NOCREATEROLE`, possui
limite de 10 conexões e é dono apenas do banco da aplicação. Acesso público ao
schema da aplicação é revogado.

## Comandos de operação

```powershell
.\.venv\Scripts\python.exe -m tenable_reports database-migrate --database-env-file .\credentials\database.env
.\.venv\Scripts\python.exe -m tenable_reports database-status --database-env-file .\credentials\database.env
.\.venv\Scripts\python.exe -m tenable_reports migrate-legacy-state --database-env-file .\credentials\database.env
```

`database-migrate` é idempotente e rejeita uma migration já aplicada cujo
checksum tenha sido alterado. `migrate-legacy-state` também é idempotente: os
snapshots usam a identidade histórica existente e os artefatos usam caminho
absoluto único.

## Backfill da referência MAIN

Depois das migrations e da importação do legado, execute primeiro o planejamento:

```powershell
.\.venv\Scripts\python.exe -m tenable_reports backfill-report-main `
  --database-env-file .\credentials\database.env `
  --dry-run
```

O `dry-run` é o comportamento padrão. Ele não altera arquivos nem referências e
separa as competências em promoções inequívocas, candidatos inválidos e casos
ambíguos que exigem escolha do analista. Uma geração única e válida é escolhida;
quando há várias, somente uma geração já utilizada pelo histórico pode vencer
automaticamente.

Revise o JSON antes de aplicar. Casos com `MAIN_SELECTION_REQUIRED` não são
alterados pelo comando. Para aplicar apenas as promoções inequívocas:

```powershell
.\.venv\Scripts\python.exe -m tenable_reports backfill-report-main `
  --database-env-file .\credentials\database.env `
  --apply
```

As promoções são auditadas com ator `system-backfill` e motivo `migração inicial`.
O backfill não modifica nem exclui DOCX, datasets, snapshots ou arquivos legados.

## Integração com relatórios

Com `credentials/database.env` configurado, `collect-monthly`, `collect-manual`,
`run-client`, `publish-history`, `import-history-csv` e
`build-report-dataset` usam PostgreSQL automaticamente. O argumento legado
`--history-database` força SQLite somente para compatibilidade ou recuperação.

A configuração multi-cliente aponta para o arquivo compartilhado:

```json
{
  "defaults": {
    "database_env_file": "../credentials/database.env"
  }
}
```

Cada processo de cliente recebe somente o caminho desse arquivo. A senha não é
inserida no JSON da orquestração, no comando, nos manifestos ou nos logs.

## Backup e recuperação

O backup deve preservar duas camadas:

1. dump PostgreSQL do banco `tenable_reports`;
2. diretórios `data` e de relatórios, preservando os arquivos apontados pelo
   catálogo de artefatos.

Exemplo de dump, com autenticação fornecida pelo mecanismo seguro do ambiente:

```powershell
& "C:\Program Files\PostgreSQL\18\bin\pg_dump.exe" `
  --format=custom --dbname=tenable_reports --username=tenable_reports_app `
  --file=tenable_reports.backup
```

Não armazene dumps ou arquivos `.env` no repositório Git.
