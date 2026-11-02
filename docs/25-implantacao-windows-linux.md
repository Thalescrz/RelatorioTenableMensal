# Implantação Windows e Linux

Este guia define o pacote de produção reproduzível. O pacote de release é diferente
de um clone do repositório: ele contém a aplicação, Python, LibreOffice,
PostgreSQL, wheels travados e todas as fontes abertas exigidas no Linux. No Windows,
Calibri e Arial vêm preferencialmente do próprio host licenciado; um payload
autorizado pode acompanhar o pacote apenas como recuperação. Depois que o
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
oficiais descritos em `deploy/windows/bundle-layout.json` ou
`deploy/linux/bundle-layout.json`. O pacote Windows exige assinaturas
Authenticode válidas e correspondência exata com o assunto e o SHA-256 do
certificado oficial fixados no layout para Python, LibreOffice e PostgreSQL. Uma
assinatura válida de outro publicador é rejeitada. Ele pode ser montado sem fontes
Microsoft e então declara
`windows-calibri-v1` em `host_requirements`. Se um único arquivo do payload de
recuperação for fornecido, o grupo inteiro e `license-proof.json` tornam-se
obrigatórios. O Linux exige os
artefatos e assinaturas/hashes previstos no layout, inclusive os pacotes nativos
do Ubuntu Server 24.04. O tarball Python é validado pelo bundle Sigstore oficial,
com emissor OIDC fixado. O endereço exato do signatário é extraído do certificado
do próprio bundle, sem ser versionado, e só é aceito quando pertence ao domínio
institucional previsto. O LibreOffice é validado por assinatura destacada com o
chaveiro TDF fixado pelo
fingerprint `C2839ECAD9408FBE9531C3E9F434A1EFAFEEAEA3`.

O ambiente que monta a release Linux precisa ter `sigstore==4.5.0` e `gpgv`. Essas
ferramentas pertencem à fase de construção; a máquina que instala o ZIP pronto não
precisa de acesso à internet nem de Sigstore/GnuPG. A cópia do código é derivada
somente do índice Git e então limitada às raízes permitidas. Arquivos ignorados,
credenciais locais, artefatos não rastreados e releases anteriores não entram no
pacote; por isso, a construção exige um checkout Git válido. A release também é
bloqueada quando o índice está vazio ou incompleto, ou quando um arquivo rastreado
das raízes permitidas não está materializado no checkout. Isso impede que um
checkout parcial ou esparso seja publicado como pacote completo.

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
Arquivo obrigatório ausente, adulterado ou não autorizado deixa a release em
`BLOCKED`; um payload Windows parcial também é rejeitado. Não se
deve contornar essa proteção. Verifique o pacote novamente no destino antes de
executar o instalador.

O manifesto interno detecta alteração depois da construção, mas não substitui a
autenticidade do canal que entrega o próprio ZIP. Distribua o arquivo por canal
protegido e publique seu SHA-256 por um meio independente ou previamente confiável.

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
LibreOffice, PostgreSQL e wheels, prepara o banco e restringe os arquivos de
credencial. Para `windows-calibri-v1`, compara primeiro Calibri e Arial em
`%WINDIR%\Fonts` com os hashes aprovados. Se estiverem válidas, não as copia nem
altera seu registro. Se faltarem ou divergirem, instala automaticamente o payload
opcional completo e autorizado; sem nenhuma das duas fontes válidas, interrompe
com `WINDOWS_FONT_REQUIREMENT_UNMET` antes de liberar a aplicação. Marcadores em
`.install-state` tornam a execução idempotente. Ele
não inicia coleta, não cria agenda e não inicia a interface. A janela de console
do LibreOffice não é usada pelo runtime; as sondagens e renderizações Windows
preferem `soffice.com` com `CREATE_NO_WINDOW`.
Ao final, o instalador executa `environment-status --require-ready`. Qualquer
bloqueio de fonte, LibreOffice, PostgreSQL, armazenamento ou fuso encerra a
instalação com código diferente de zero e impede a mensagem de conclusão.

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

O instalador usa somente o conteúdo do pacote: instala os `.deb` nativos em modo
não interativo,
compila o Python empacotado, instala LibreOffice/PostgreSQL/fontes, cria a `.venv`
com `--no-index` e prepara a aplicação. O conjunto nativo inclui explicitamente as
bibliotecas de execução do LibreOffice e o OpenSSL necessário ao PostgreSQL. O
preflight final usa `--require-ready`: qualquer bloqueio de fonte, LibreOffice,
PostgreSQL, armazenamento ou fuso encerra a instalação com código diferente de
zero. Aviso de agendador ainda não ativado não bloqueia a geração manual. O
instalador não inicia coleta, interface ou timer.
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
No Windows, a ação cadastrada informa a raiz absoluta por `-ProjectRoot`; o
launcher muda para essa raiz antes do preflight e resolve o LibreOffice sem depender
do diretório herdado pelo Agendador. Cada disparo escreve um registro sanitizado em
`data/automation-logs/monthly-scheduler.jsonl`. Use `-PreflightOnly` para validar
essa instalação a partir de outro diretório sem criar lote ou iniciar coleta.

## Diagnóstico pós-instalação

Antes da primeira operação, abra **Admin → Diagnóstico do ambiente** ou execute:

```text
python -m tenable_reports environment-status --json --require-ready \
  --root <raiz-instalada>
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

Os payloads redistribuíveis oficiais foram montados e verificados: Authenticode com
certificado exato no Windows; Sigstore para Python no Linux; chave TDF conferida
pelo fingerprint fixado para LibreOffice; hashes e manifesto do bundle nas duas
plataformas. Em um contêiner limpo derivado de Ubuntu 24.04, sem rede e com
`systemd` como PID 1, o instalador Linux concluiu duas execuções consecutivas,
aplicou as 15 migrations e confirmou Python 3.14.5, LibreOffice 26.2.4.2,
PostgreSQL 18.6 e o perfil `linux-carlito-v1`. A segunda execução comprovou a
idempotência do fluxo e o serviço PostgreSQL permaneceu ativo sob o gerenciador de
serviços real.

Na mesma instalação foram gerados, convertidos em PDF pelo LibreOffice Linux e
inspecionados integralmente os quatro documentos sanitizados: Geral com 15
páginas, Inteligência e Customizações com 12, TAG com 7 e Cloud com 15. A métrica
de Carlito exigiu uma adaptação restrita ao perfil Linux: o EMF decorativo da capa
é trocado por uma rasterização PNG da mesma arte na dimensão efetiva de exibição,
com fundo branco opaco e traço azul corporativo reforçado para impedir a perda de
saturação durante a redução; a contracapa
usa espaçadores de 12,5 pt antes da logomarca, 9 pt no bloco central e 16 pt antes
dos contatos; a mensagem institucional quebra antes de `DIGITAL.`; e a logomarca
usa contorno acima/abaixo para impedir que `SUA` seja deslocado para a lateral da
imagem. A seção final materializa variantes vazias próprias de cabeçalho e rodapé,
inclusive para páginas pares, evitando herança do corpo. O pequeno texto
decorativo que era recortado para fora da página pelo LibreOffice Linux é omitido.
A tabela de contatos equilibra a largura da terceira coluna com a primeira para
manter Fortaleza, Belém e Portugal em intervalos horizontais equivalentes. A
baseline Windows não é alterada; identidade, endereços e contatos institucionais
continuam preservados na contracapa única.

Essa evidência de contêiner com `systemd` é mais próxima do servidor final, mas
ainda não substitui uma homologação em Ubuntu Server/VM. O timer de usuário não foi
configurado no ensaio e permaneceu em `WARNING`; valide-o no host final antes da
primeira publicação automática. A instalação completa em uma máquina Windows
limpa também permanece pendente; o funcionamento editorial no Windows atual e o
bundle verificado não devem ser descritos como essa homologação. A ausência do
payload proprietário continua aceita apenas porque as fontes licenciadas do host
são validadas antes de liberar a aplicação.
