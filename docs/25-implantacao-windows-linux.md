# Implantação Windows e Linux

Este guia define o pacote de produção reproduzível. O pacote de release é diferente
de um clone do repositório: ele contém a aplicação, Python, LibreOffice,
PostgreSQL, wheels travados e fontes autorizadas para a plataforma. Depois que o
ZIP oficial foi montado, a instalação ocorre sem acesso à internet e não exige que
o operador procure ou instale dependências separadamente.

## Matriz suportada

| Plataforma | Perfil | Fontes do documento | Agendador |
|---|---|---|---|
| Windows x86_64 | `windows-calibri-v1` | Calibri e Arial, provenientes de licença válida do ambiente ou de payload licenciado | Agendador de Tarefas |
| Ubuntu Server 24.04 x86_64 | `linux-carlito-v1` | Carlito e Liberation Sans, redistribuíveis | timer de usuário do `systemd` |

A aplicação identifica o sistema operacional e a arquitetura. Plataforma não
suportada, perfil de fontes incompatível, fonte ausente ou versão divergente
interrompe o preflight; não existe substituição silenciosa de tipografia.

As versões da release ficam fixadas em `deploy/runtime-manifest.json`: Python
3.14.5, LibreOffice 26.2.4.2 e PostgreSQL 18. As dependências Python são travadas
por plataforma em `requirements/lock`. A interface permanece vinculada somente a
`127.0.0.1`; a instalação não abre porta no firewall.

## Montar o pacote offline

Os binários grandes e as fontes proprietárias não ficam no Git. O mantenedor da
release prepara, fora do repositório, um diretório de fornecedor com os arquivos
oficiais descritos em `deploy/windows-x86_64/bundle-layout.json` ou
`deploy/linux-x86_64/bundle-layout.json`. O pacote Windows exige assinaturas
Authenticode válidas e prova de licença das fontes Microsoft. O Linux exige os
artefatos e assinaturas/hashes previstos no layout, inclusive os pacotes nativos
do Ubuntu Server 24.04.

No Windows:

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
.\.venv\Scripts\python.exe tools\build_offline_bundle.py `
  --source-root . `
  --vendor-root D:\release-input\windows-x86_64 `
  --destination D:\release-output `
  --platform windows
.\.venv\Scripts\python.exe tools\verify_offline_bundle.py `
  D:\release-output\tenable-reports-windows-x86_64
```

No Linux:

```bash
export PYTHONPATH="$PWD/src"
.venv/bin/python tools/build_offline_bundle.py \
  --source-root . \
  --vendor-root /srv/release-input/linux-x86_64 \
  --destination /srv/release-output \
  --platform linux
.venv/bin/python tools/verify_offline_bundle.py \
  /srv/release-output/tenable-reports-linux-x86_64
```

O construtor valida origem, assinatura quando aplicável, licença, tamanho e
SHA-256. Em seguida cria `bundle-manifest.json`, `bundle-manifest.sha256` e o ZIP.
Arquivo ausente, adulterado ou não autorizado deixa a release em `BLOCKED`; não se
deve contornar essa proteção. Verifique o pacote novamente no destino antes de
executar o instalador.

## Instalar no Windows

Extraia o ZIP em uma pasta local e abra PowerShell elevado nela:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1
```

Para instalar em outro diretório:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1 `
  -InstallRoot 'C:\ProgramData\TenableReports'
```

O instalador verifica o manifesto antes de copiar, instala silenciosamente Python,
LibreOffice, PostgreSQL, fontes e wheels, prepara o banco e restringe os arquivos
de credencial. Marcadores em `.install-state` tornam a execução idempotente. Ele
não inicia coleta, não cria agenda e não inicia a interface. A janela de console
do LibreOffice não é usada pelo runtime; as sondagens e renderizações Windows
preferem `soffice.com` com `CREATE_NO_WINDOW`.

Inicie a interface quando desejar:

```powershell
.\scripts\run_web.ps1
```

## Instalar no Ubuntu Server

Extraia o ZIP e execute como `root`:

```bash
sudo ./scripts/setup.sh --install-root /opt/tenable-reports
```

Para permitir que o timer de um usuário continue ativo sem sessão aberta:

```bash
sudo ./scripts/setup.sh --install-root /opt/tenable-reports \
  --enable-linger-for tenable-reports
```

O instalador usa somente o conteúdo do pacote: instala os `.deb` nativos,
compila o Python empacotado, instala LibreOffice/PostgreSQL/fontes, cria a `.venv`
com `--no-index` e prepara a aplicação. Ele não inicia coleta, interface ou timer.
Inicie a interface local com:

```bash
/opt/tenable-reports/scripts/run_web.sh
```

## Agendamento

O mesmo caso de uso `run-monthly-batch` atende as duas plataformas. No Windows, o
adaptador usa o Agendador de Tarefas. No Linux, ele materializa um serviço e um
timer de usuário do `systemd`; `cron` não é utilizado. Salvar ou validar a política
não cria agenda. Sincronizar, ativar ou desativar exige confirmação explícita e
nenhuma dessas ações executa uma coleta imediatamente.

## Diagnóstico pós-instalação

Antes da primeira operação, abra **Admin → Diagnóstico do ambiente** ou execute:

```text
python -m tenable_reports environment-status --project-root <raiz-instalada>
```

Confirme plataforma, perfil de fontes, versão do LibreOffice, conexão PostgreSQL,
migrations e espaço de armazenamento. A saída é sanitizada e não imprime senha ou
token. Uma instalação somente é liberada quando o preflight estiver `READY`.

## Transferir o estado operacional

Não copie `data` nem o banco manualmente. Na instalação de origem, gere um pacote
fora da árvore do projeto:

```text
python -m tenable_reports deployment-export \
  --project-root <origem> \
  --destination <backup.tenable-transfer.zip>
```

Use `--include-staging` apenas quando checkpoints recuperáveis precisarem seguir
para o destino. O arquivo contém dump PostgreSQL em formato custom, configurações
sem segredos, documentos publicados, manifestos e referências `MAIN`; access keys,
tokens e senhas não são exportados.

No destino, faça primeiro a validação integral sem escrita:

```text
python -m tenable_reports deployment-import \
  --project-root <destino> \
  --archive <backup.tenable-transfer.zip> \
  --dry-run
```

O dry-run verifica schema, hashes, espaço, identidade da instalação e presença dos
documentos `MAIN`. Se aprovado, ele informa uma frase exata no formato
`IMPORT <transfer_id>`. Só então aplique com credencial administrativa local:

```text
python -m tenable_reports deployment-import \
  --project-root <destino> \
  --archive <backup.tenable-transfer.zip> \
  --apply --confirm "IMPORT <transfer_id>"
```

A aplicação restaura em banco de staging, rebaseia caminhos controlados, valida
novamente e promove com rollback coordenado. Credenciais Tenable e PostgreSQL são
provisionadas novamente no destino. Conflito de identidade, incompatibilidade de
migration ou documento `MAIN` ausente impede a promoção.

## Desenvolvimento e rollback

Um clone do Git não contém os payloads da release. Para desenvolvimento, use um
Python compatível, crie a `.venv` e instale o projeto editável; não execute
`scripts/setup.ps1` de produção sem antes construir um bundle válido.

Para código, prefira reverter o commit defeituoso, sem reescrever histórico. Para
migração de máquina, preserve o ZIP de transferência até validar banco, documentos
e `MAIN` no destino. Um instalador interrompido pode ser executado novamente: os
marcadores idempotentes conservam etapas concluídas e as verificações de hash são
repetidas.

## Evidência e limites desta entrega

No Windows x86_64, o gerador foi executado com os quatro tipos de relatório
sanitizados e comparado à baseline `windows-calibri-v1`: não houve diferença
visual, com inspeção de capa, sumário, controle de documento, gráficos, tabelas e
contracapa. Os instaladores, manifestos, locks, regras de integridade e scripts
Linux possuem testes automatizados.

A instalação completa em uma máquina Windows limpa e a execução em um host Ubuntu
Server 24.04 limpo ainda dependem da montagem dos payloads oficiais de release e
devem ser realizadas antes de publicar o primeiro ZIP. Essa pendência não deve ser
descrita como homologação real concluída. O pacote atual falha de forma fechada
quando esses artefatos ou a prova de licença das fontes Windows não estão
presentes.
