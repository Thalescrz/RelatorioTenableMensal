# Portabilidade Windows/Linux e renderizacao reproduzivel

> **Registro de decisao:** este arquivo preserva o desenho aprovado para tornar a
> aplicacao instalavel em Windows e Linux sem alterar silenciosamente o resultado
> atual do Windows. Consulte o [contexto vigente](../../../CONTEXTO.md) e os guias
> atuais antes da implementacao.

## Objetivo

Permitir que a mesma base de codigo execute coleta, persistencia, montagem de DOCX,
validacao e automacao mensal em Windows e Linux. O ambiente Windows atual sera a
referencia visual inicial; o Linux usara fontes redistribuiveis equivalentes e tera
um contrato visual proprio, estavel e verificavel.

Copiar somente o repositorio devera ser suficiente para preparar uma instalacao
nova por meio do bootstrap da plataforma. A continuidade de uma instalacao
existente exigira, adicionalmente, um backup operacional controlado do PostgreSQL,
dos documentos publicados e das configuracoes locais. Credenciais e dados reais
nunca serao incorporados ao Git.

## Escopo e plataformas de referencia

A primeira entrega homologara:

- Windows x64 com instalacao nativa;
- Linux x64 baseado em `systemd`, tendo Ubuntu Server 24.04 LTS como ambiente de
  referencia;
- Python 3.14.5 nos pacotes oficiais de implantacao iniciais;
- PostgreSQL 18 como referencia inicial, preservando compatibilidade de migrations
  com versoes suportadas que forem comprovadas pela matriz automatizada;
- LibreOffice 26.2.4.2 como renderizador de referencia inicial.

O contrato de desenvolvimento do `pyproject.toml` pode continuar mais amplo, mas
um ambiente oficial so e reproduzivel quando usa a versao exata registrada no
manifesto da plataforma. Atualizacoes de Python, Pillow, LibreOffice, fontes ou
PostgreSQL exigem nova execucao da validacao estrutural e visual.

macOS, containers, Kubernetes, cron e exposicao publica da interface ficam fora da
primeira entrega. Um container futuro usara o backend de agendamento externo, sem
executar `systemd` dentro do container.

## Principios de compatibilidade

1. O comportamento Windows existente deve ser congelado por fixtures antes da
   primeira refatoracao.
2. Regras de periodo, metricas, historico, `MAIN`, retries e idempotencia nao
   variam por sistema operacional.
3. Apenas integracoes realmente dependentes da plataforma recebem adaptadores:
   fontes, LibreOffice, agendador, bootstrap e diagnostico do host.
4. Ausencia de fonte ou renderizador obrigatorio nunca aciona fallback visual
   silencioso em uma publicacao oficial.
5. A interface pode iniciar em modo de diagnostico quando o ambiente estiver
   incompleto, mas deve impedir uma geracao oficial antes de iniciar coleta remota.
6. O Windows permanece visualmente compativel com os documentos aprovados; o Linux
   preserva conteudo e arquitetura editorial, mas nao promete equivalencia pixel a
   pixel com glifos Calibri/Arial.
7. Nenhuma alteracao deste trabalho autoriza coleta real, migracao de banco,
   publicacao de documentos ou instalacao em outro host.

## Deteccao e capacidades da plataforma

Um modulo de dominio de infraestrutura, sem dependencia da interface web, expoe um
objeto imutavel `RuntimeCapabilities`. A deteccao usa `platform.system()` uma unica
vez durante a composicao da aplicacao e classifica o host como `WINDOWS`, `LINUX` ou
`UNSUPPORTED`.

`RuntimeCapabilities` informa:

- plataforma e arquitetura;
- perfil tipografico selecionado;
- backend de agendamento selecionado;
- executavel e versao do LibreOffice;
- versao do Python;
- disponibilidade do banco e do timezone IANA;
- diretorios persistentes e permissoes de leitura/escrita;
- falhas bloqueantes, avisos e recomendacoes sanitizadas.

O sistema operacional nao pode ser falsificado por variavel de ambiente em
producao. Testes injetam uma sonda de plataforma falsa. Configuracao explicita pode
selecionar somente capacidades validas para a plataforma detectada, como desativar
o gerenciamento do agendador e usar o modo `EXTERNAL`.

Hosts nao suportados podem abrir o diagnostico e executar comandos somente leitura,
mas nao podem gerar documento oficial nem aplicar agendamento.

## Perfis tipograficos

Toda escolha de fonte passa por um `FontProfile`; nenhum renderizador consulta
`C:\Windows\Fonts`, `fontconfig` ou `ImageFont.load_default()` diretamente.

O perfil `windows-calibri-v1` preserva o comportamento visual de referencia:

- Calibri regular e bold para corpo, titulos, tabelas e controle documental;
- as variantes Arial atualmente usadas em graficos permanecem nas mesmas funcoes
  ate que uma mudanca editorial seja aprovada;
- caminhos reais dos arquivos sao resolvidos pelo Windows e validados antes da
  geracao;
- Calibri e Arial nao sao copiadas para o repositorio; a instalacao exige fontes
  licenciadas no host.

O perfil `linux-carlito-v1` usa arquivos versionados e redistribuiveis:

- Carlito regular e bold como equivalentes de Calibri;
- Liberation Sans regular e bold onde o perfil Windows usa Arial;
- os arquivos e suas licencas acompanham o pacote de implantacao e possuem hashes
  registrados no manifesto;
- o bootstrap instala as fontes em um diretorio controlado e atualiza o cache do
  `fontconfig`.

O perfil fornece, no minimo, familia DOCX, arquivo regular, arquivo bold, arquivo
italic e familia alternativa usada pelos graficos. `python-docx`, Pillow e todos os
builders compartilhados recebem esse mesmo perfil. A familia escrita no XML do
DOCX corresponde a fonte efetivamente instalada na plataforma.

Se qualquer arquivo obrigatorio estiver ausente, ilegivel ou com hash inesperado,
o preflight retorna `FONT_PROFILE_INVALID`. A geracao oficial e rejeitada antes de
qualquer chamada Tenable; a interface permanece disponivel para correcao.

## Baseline visual Windows

Antes de parametrizar fontes ou LibreOffice, fixtures sinteticas e deterministicas
geram os quatro documentos oficiais: Geral, Inteligencia e Customizacoes, TAG e
Cloud. Nenhuma fixture contem cliente, IP, hostname, pessoa, e-mail ou credencial
real.

O baseline preserva:

- pacote DOCX normalizado, ignorando somente metadados volateis e ordem de ZIP;
- estilos, familias tipograficas, tamanhos, alinhamentos e propriedades de tabela;
- imagens e dimensoes dos graficos;
- quantidade de paginas e pontos de quebra relevantes;
- PDFs renderizados pelo LibreOffice de referencia;
- imagens das paginas criticas para comparacao visual.

A refatoracao Windows e aceita somente quando nao altera conteudo, paginacao,
cortes, tabelas, imagens ou hierarquia. Diferencas minimas de antialiasing podem ter
tolerancia documentada; mudanca de linha, pagina, tamanho de grafico ou fonte nao
pode ser mascarada pela tolerancia.

O baseline e composto apenas por artefatos sanitizados e pequenos. Evidencias
temporarias de renderizacao permanecem fora do Git quando excederem o necessario
para o teste automatizado.

## Contrato visual Linux

O Linux deve produzir os mesmos titulos, textos, tabelas, dados, graficos, sumario,
controle documental, cabecalho, rodape, capa e contracapa. As familias Carlito e
Liberation Sans podem produzir pixels diferentes, mas a execucao homologada nao
pode apresentar:

- texto cortado ou sobreposto;
- linhas orfas causadas por fonte ausente;
- tabela fora das margens;
- grafico com fonte bitmap de fallback;
- pagina vazia inesperada;
- sumario sem destino navegavel;
- alteracao de contagem ou ordenacao de dados.

Uma fixture Linux aprovada torna-se o baseline `linux-carlito-v1`. Repeticoes no
mesmo manifesto precisam ser deterministicas dentro das tolerancias documentadas.

## Resolucao do LibreOffice

Um `OfficeRenderer` comum recebe um `LibreOfficeInstallation` resolvido pelo
preflight. O Windows procura primeiro o caminho explicitamente configurado, depois
o pacote portatil homologado e por fim uma instalacao reconhecida. O Linux procura
o caminho configurado e depois `soffice` no `PATH`.

O executavel deve responder com a versao esperada. Uma versao diferente aparece
como `LIBREOFFICE_VERSION_MISMATCH` e bloqueia publicacao oficial, salvo quando o
operador estiver executando explicitamente o fluxo de homologacao de uma nova
versao. Perfis temporarios do LibreOffice sao isolados por processo para impedir
contencao entre renders.

O LibreOffice continua sendo usado para QA e materializacao visual, nao para
calcular dados nem para substituir os builders DOCX.

## Agendamento multiplataforma

O servico mensal continua chamando o mesmo `STAGED_V1` e o mesmo comando
`run-monthly-batch`. Ele passa a depender de um protocolo `MonthlyScheduler`, com
operacoes de consulta, aplicacao e ativacao/desativacao.

Backends iniciais:

- `WindowsTaskScheduler`: preserva `schtasks.exe`, PowerShell e as confirmacoes
  atuais;
- `SystemdUserTimerScheduler`: cria service/timer para uma conta dedicada, chama
  `systemctl --user`, valida o conteudo instalado e habilita persistencia do usuario
  durante o bootstrap privilegiado;
- `ExternalScheduler`: apenas descreve o comando e informa que o ciclo e gerenciado
  fora da aplicacao; nao cria ou altera agenda.

A selecao padrao e Windows Task Scheduler no Windows e systemd de usuario no Linux.
Nao existe fallback automatico de systemd para cron. Se systemd nao estiver
disponivel, o status e `UNAVAILABLE` ate o operador selecionar explicitamente o
modo externo.

Durante a transicao, a resposta Windows conserva o campo `windows_task` existente e
adiciona o contrato generico `scheduler`. A interface migra para `scheduler` sem
alterar a semantica das confirmacoes. Depois de uma versao de compatibilidade, a
remocao do campo legado exige decisao separada.

## Instalacao e dependencias reproduziveis

O repositorio passa a oferecer dois bootstraps idempotentes:

- `scripts/setup.ps1` para Windows;
- `scripts/setup.sh` para Linux.

Ambos validam ou instalam a versao oficial do Python, criam ambiente virtual,
instalam dependencias a partir do lock da plataforma, verificam assets e executam o
preflight. Instaladores nao imprimem segredos e nao iniciam coleta.

Locks separados por plataforma registram versoes e hashes de todas as dependencias
Python. Um manifesto de runtime registra Python, Pillow, python-docx, docxcompose,
psycopg, tzdata, LibreOffice, PostgreSQL e fontes. Pacotes externos baixados pelo
bootstrap precisam de URL oficial e SHA-256; um pacote de release offline pode
inclui-los quando a licenca permitir, mas binarios grandes nao entram
automaticamente no Git.

Templates, imagens corporativas, migrations, arquivos estaticos e fontes Linux
devem fazer parte do artefato instalavel. Um wheel ou pacote que omita o shell DOCX
nao e uma distribuicao valida.

## Persistencia e transferencia entre maquinas

O Git continua sem credenciais, dados reais e documentos de clientes. Para mover
uma instalacao existente, uma ferramenta administrativa prepara um inventario e um
backup fora do repositorio contendo:

- dump consistente do PostgreSQL;
- documentos publicados e manifestos ainda validos;
- configuracoes locais de clientes, analistas e controle documental;
- staging recuperavel que o operador escolher preservar;
- manifesto de arquivos, hashes e versoes do ambiente.

Segredos nao entram no backup comum. Eles sao recadastrados no destino ou
transferidos por um mecanismo criptografado separado. O importador possui analise
somente leitura, valida versao de schema, hashes, espaco, caminhos e conflito de
identidade antes de qualquer aplicacao. A restauracao preserva referencias `MAIN`,
historico e IDs de execucao.

Uma instalacao nova nao importa estado antigo: cria banco vazio pelas migrations,
solicita configuracao local e usa os templates versionados.

## Interface e seguranca de rede

A portabilidade nao remove a protecao atual de loopback. Windows e Linux continuam
vinculados a `127.0.0.1` por padrao e podem ser operados localmente, por RDP ou por
tunel SSH.

Expor a interface a outros computadores exige um projeto separado de autenticacao,
autorizacao, TLS, sessao, auditoria e proxy reverso. O bootstrap desta entrega nao
abre firewall nem altera o host para `0.0.0.0`.

Uma pagina administrativa de ambiente apresenta apenas versoes, capacidades e
erros sanitizados. Nomes de arquivos de credencial podem ser confirmados como
presentes ou ausentes, mas seus valores nunca retornam ao navegador.

## Falhas e comportamento degradado

O preflight classifica resultados como `READY`, `WARNING` ou `BLOCKED` por
capacidade. Uma falha de agendador nao bloqueia geracao manual. Uma falha de
LibreOffice impede homologacao/publicacao oficial, mas nao impede diagnostico do
banco. Uma falha de fonte impede montagem oficial. Uma falha de PostgreSQL impede
operacoes duraveis e nunca ativa armazenamento alternativo silencioso.

Erros carregam codigo estavel, componente, plataforma e instrucao de correcao. Nao
incluem comando com segredo, token, DSN completo, host de cliente ou conteudo de
documento.

## Estrategia de entrega sem regressao

A implementacao segue gates independentes:

1. capturar e aprovar o baseline Windows sem alterar producao;
2. introduzir deteccao e preflight mantendo os adaptadores antigos;
3. migrar fontes por injecao e comprovar equivalencia Windows;
4. abstrair o agendador e comprovar equivalencia Windows;
5. concluir bootstrap e pacote reproduzivel Windows;
6. adicionar Carlito/Liberation Sans e homologar renderizacao Linux;
7. adicionar systemd e bootstrap Linux;
8. adicionar backup/importacao operacional;
9. executar a matriz integral e atualizar documentacao vigente.

Cada gate termina com testes focados, suite completa, validadores do projeto,
auditoria de segredos e `git diff --check`. Gates de DOCX exigem renderizacao e
inspecao visual. Nenhum gate Linux pode alterar o baseline Windows aprovado.

## Criterios de aceitacao

A entrega e aceita quando:

- a aplicacao detecta Windows e Linux sem condicionais duplicadas nos geradores;
- Windows reproduz o baseline visual aprovado nos quatro tipos de documento;
- Linux gera os quatro documentos com `linux-carlito-v1`, sem fallback, corte ou
  sobreposicao;
- a ausencia de fonte ou LibreOffice bloqueia a geracao antes de coleta remota;
- o painel informa plataforma, fontes, LibreOffice, banco e backend de agenda;
- Windows agenda pela mesma tarefa e confirmacao vigentes;
- Linux cria, consulta, ativa e desativa o timer systemd da conta dedicada;
- o modo manual funciona mesmo quando o agendador esta indisponivel;
- uma instalacao limpa pode ser preparada somente com o codigo e as dependencias
  licenciadas obtidas pelo bootstrap;
- uma migracao controlada preserva PostgreSQL, documentos, hashes, historico e
  referencias `MAIN` sem copiar segredos para o Git;
- os guias vigentes descrevem instalacao, upgrade, backup, restauracao, diagnostico
  e limitacoes nas duas plataformas.

## Fora de escopo

- alterar textos, ordem editorial, metricas ou regras de relatorio;
- substituir Calibri no Windows;
- prometer DOCX byte a byte identico entre plataformas;
- expor a interface diretamente na Internet;
- executar Word ou automacao COM;
- criar scans, alterar tenants ou iniciar coletas durante instalacao;
- suportar macOS, containers ou cron nesta primeira entrega.
