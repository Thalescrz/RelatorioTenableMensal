# Contexto do projeto

**Atualizado em:** 2026-09-15  
**Base verificada:** `277750f` (`main` no início desta revisão)  
**Natureza:** resumo versionado; estado operacional transitório fica fora deste arquivo

## Finalidade

Esta aplicação local coleta dados autorizados da plataforma Tenable, normaliza os
resultados, preserva o histórico compacto necessário e publica relatórios mensais
reproduzíveis em Word para uma carteira de clientes. O objetivo central é manter o
padrão editorial aprovado sem misturar coleta, regras de negócio e apresentação.

Este arquivo é o ponto de retomada do projeto. Ele resume o estado vigente e aponta
para os contratos detalhados; não substitui código, testes, migrations, runbooks ou
os guias técnicos.

## Fontes de verdade e precedência

Em caso de divergência, use esta ordem:

1. código, testes e migrations definem o comportamento executável;
2. `AGENTS.md` da raiz e dos diretórios definem limites para alterações;
3. este `CONTEXTO.md` registra o mapa consolidado do estado atual;
4. [DESIGN.md](DESIGN.md) e [docs/19 a docs/23](docs/README.md) detalham os
   contratos vigentes;
5. skills e runbooks locais orientam procedimentos especializados;
6. `docs/01` a `docs/18` preservam contratos e etapas históricas;
7. especificações e planos em `docs/superpowers` registram decisões de cada ciclo,
   mas não comprovam sozinhos que uma implementação foi concluída.

Uma diferença entre documentação e comportamento deve ser resolvida verificando o
código e os testes antes de atualizar a descrição vigente.

## Estado consolidado

- A aplicação possui interface web local, CLI e execução mensal agendada.
- Novos lotes da carteira usam o pipeline durável `STAGED_V1`.
- Coletas remotas podem avançar em paralelo entre clientes; a montagem local dos
  documentos é serializada.
- VM é o núcleo do relatório geral. WAS e Cloud são capacidades opcionais e
  independentes, com falha e retentativa próprias.
- O relatório Cloud padrão compartilha o shell editorial e o controle documental
  do relatório geral, mas usa uma fotografia Cloud independente do dataset VM.
- PostgreSQL é a autoridade operacional para histórico, documentos, tentativas,
  lotes e seleção `MAIN`.
- O painel reconcilia atualizações sem recriar cartões inalterados e permite
  reconhecer alertas sem apagar o histórico operacional.

Esse resumo descreve capacidades versionadas. Quantidade de clientes, jobs em
execução, percentuais, alertas visíveis e último período publicado são estados
mutáveis e devem ser consultados na aplicação e no PostgreSQL.

## Relatórios entregues

Uma execução pode publicar quatro tipos de documento:

1. **Relatório-base geral:** conteúdo editorial comum, indicadores do ambiente,
   Top 5 VM e, quando disponível, Top 5 WEB.
2. **Relatório geral de inteligência e customizações:** módulos analíticos atuais e
   históricos habilitados para o cliente.
3. **Relatório operacional por TAG:** recorte VM compacto e opcional, comparando a
   mesma TAG ao longo do tempo.
4. **Relatório Tenable Cloud Security:** documento opcional, independente e gerado
   em um único modelo padrão completo.

Os quatro tipos usam o shell corporativo oficial, com capa, sumário nativo do Word,
títulos numerados e contracapa. O relatório geral nunca é filtrado por TAG.

## Arquitetura resumida

O código em `src/tenable_reports` é separado por responsabilidade:

- `domain`: modelos e regras puras;
- `application`: orquestração, normalização, histórico, TAGs, publicação e
  retentativas;
- `infrastructure`: integrações Tenable VM/WAS/Cloud, arquivos e PostgreSQL;
- `presentation`: montagem e validação dos DOCX;
- `webapp`: servidor local, API e interface estática;
- CLI: entrada headless para execução e manutenção controladas.

Credenciais locais ficam fora dos perfis e do Git. Dados pesados intermediários
permanecem somente enquanto necessários para publicação, diagnóstico ou retomada.

## Módulos VM, WAS e Cloud

- **VM:** coleta ativos e findings, forma o dataset geral e sustenta os documentos
  gerais e por TAG.
- **WAS:** é opcional; indisponibilidade, ausência de licença ou ausência de
  achados não invalida VM concluído. Uma recuperação WAS publicada reutiliza o
  checkpoint VM e não repete assets, VM, TAG ou Cloud.
- **Cloud:** é opcional e usa credencial e fluxo GraphQL próprios. Sua falha não
  invalida VM ou WAS concluídos. A retentativa Cloud não repete os demais
  componentes.

Cloud representa a fotografia preservada no instante da coleta. A competência do
relatório não transforma essa fotografia em reconstrução histórica; reprodução
exata de período anterior exige snapshot Cloud compatível já preservado.

## Controle de documento

O controle documental tem um padrão global, administrado na área administrativa,
para:

- tabela **Preparação**;
- tabela **Controle de Versionamento**;
- linhas comuns da **Lista de Distribuição**.

Em **Gerenciar clientes**, cada cliente mantém somente destinatários adicionais da
Lista de Distribuição. Na renderização, as linhas globais aparecem primeiro e os
adicionais do cliente são anexados nas linhas seguintes. A personalização não
substitui nem edita o padrão global.

Os relatórios Geral e Cloud usam o mesmo componente de controle documental. O
padrão global fica em `orchestration/document-control.json`; os adicionais ficam no
perfil local do cliente. Nenhum dos dois deve conter dados reais em arquivos
versionados.

## Períodos, identidade e métricas

- Intervalos internos usam `[início, fim)` no fuso configurado para o cliente.
- Datas explícitas inclusivas da interface são convertidas para limite final
  exclusivo, preservando integralmente o último dia escolhido.
- Findings `OPEN` e `REOPENED` pertencem ao período por `last_found`.
- Findings `FIXED` pertencem ao período por `last_fixed`.
- Severidade `Informational` fica fora dos relatórios atuais.
- Ativos e findings são ligados por UUID, nunca por IP ou hostname.
- TAG é recortada localmente do dataset VM geral pelos UUIDs associados; sua
  seleção não modifica a coleta nem os números do relatório geral.
- Comparação por TAG sempre usa a mesma categoria e valor em períodos compatíveis.
- Indicadores de explorabilidade permanecem segregados pelo framework definido;
  um indicador geral não substitui os demais.

As fórmulas, populações e regras de ausência estão no
[catálogo de dados e métricas](docs/21-catalogo-de-dados-e-metricas.md).

## Lotes, checkpoints e retentativas

Em `STAGED_V1`, o coordenador separa a execução em coleta remota, consolidação e
montagem local. Clientes distintos podem consultar e baixar exports em paralelo,
enquanto a montagem dos documentos respeita limite local único.

Estados, eventos, tentativas e ações solicitadas são persistidos. Pausar após o
atual, parar, retomar e derivar um lote de falhas não descartam silenciosamente
UUIDs remotos, manifests parciais ou checkpoints reutilizáveis.

- **Pausar após o atual:** impede a liberação de novo trabalho depois da unidade em
  andamento.
- **Parar lote:** solicita interrupção local durável; não cancela exports remotos
  por padrão.
- **Retomar lote:** libera apenas jobs ainda enfileirados.
- **Tentar falhas/interrompidos:** cria uma tentativa derivada somente para os
  componentes elegíveis.

Uma retentativa deve consultar primeiro o identificador ou snapshot preservado e
nunca repetir componente já concluído sem causa validada. Publicação parcial não se
torna `MAIN` enquanto os componentes obrigatórios não estiverem resolvidos.

## PostgreSQL, MAIN e armazenamento

PostgreSQL mantém:

- histórico compacto VM, TAG e Cloud;
- execuções, lotes, jobs, eventos e tentativas por componente;
- documentos publicados, hashes e validade;
- referência `MAIN` por identidade de cliente, período e tipo de relatório;
- metadados necessários para retomada e auditoria.

DOCX publicados permanecem no armazenamento local controlado e são registrados no
catálogo. Arquivos raw, chunks e datasets intermediários só são removidos depois de
publicação validada e persistência do histórico necessário. Falhas preservam os
artefatos recuperáveis conforme a política de retenção.

Seleção, substituição ou exclusão de `MAIN` é explícita e transacional. Pacotes ZIP
são projeções temporárias dos documentos válidos registrados, não uma nova fonte de
verdade.

## Interface web

O painel é uma interface operacional local. Os cartões exibem os módulos ativos
`VM`, `WAS` e `CLOUD`, sem expor identificadores técnicos; esses identificadores
continuam disponíveis apenas na edição individual quando necessários.

O dashboard consulta estado com intervalo rápido quando há trabalho e mais lento
quando está em repouso. A resposta é aplicada por reconciliação: cartões
inalterados preservam nó, foco, seleção e rolagem, reduzindo o incômodo visual de
atualizações automáticas.

A ação **Marcar todos como lidos** persiste somente um corte temporal local em
`orchestration/dashboard-alerts.json`. Eventos anteriores ou iguais ao corte deixam
de ser apresentados; eventos novos reaparecem. A operação não exclui jobs,
checkpoints, eventos ou histórico do PostgreSQL e não reescreve o status dos
clientes.

A interface não possui autenticação multiusuário, autorização remota ou proteção
para publicação na Internet. Deve permanecer vinculada ao ambiente local até que
esses controles sejam projetados e implementados.

## Segurança e privacidade

- Credenciais ficam somente em arquivos locais ignorados pelo Git.
- Tokens não retornam ao navegador depois de persistidos.
- Código, fixtures, testes, documentos e logs versionados não recebem cliente,
  pessoa, e-mail, UUID, hostname, IP, segredo ou evidência operacional reais.
- Mensagens de erro persistidas e apresentadas devem ser sanitizadas.
- Chamadas reais, cancelamentos, alterações de banco e exclusões exigem escopo e
  autorização explícitos.
- A tradução externa se limita aos campos editoriais permitidos; evidências como
  Plugin Output, hosts e IPs não são enviadas.

## Ambiente e comandos seguros

Preparação e inicialização local no Windows:

```powershell
.\scripts\setup.ps1
.\scripts\bootstrap_postgresql.ps1
.\scripts\run_web.ps1
```

O painel fica em `http://127.0.0.1:8765`. Encerre o servidor no mesmo terminal com
`Ctrl+C`. Iniciar a interface não autoriza coleta real; execuções continuam
dependendo da ação explícita do operador.

Verificação mínima antes de declarar uma mudança concluída:

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe tools\validate_project_guidance.py --root .
.\.venv\Scripts\python.exe tools\audit_secret_leaks.py
git diff --check
```

A ajuda da CLI pode ser consultada sem iniciar coleta:

```powershell
.\.venv\Scripts\python.exe -m tenable_reports --help
.\.venv\Scripts\python.exe -m tenable_reports run-client --help
.\.venv\Scripts\python.exe -m tenable_reports run-monthly-batch --help
.\.venv\Scripts\python.exe -m tenable_reports import-web-batch-recovery --help
```

## Fluxo Git

- Parta de `main` atualizado e limpo.
- Use uma única branch `codex/*` ativa por ciclo de entrega.
- Faça commits pequenos, temáticos e verificáveis.
- Preserve mudanças existentes do usuário e não reescreva histórico.
- Execute as validações proporcionais durante o trabalho e a suíte completa antes
  da integração.
- Integre e publique somente depois de revisão; então remova a branch ou worktree
  concluída conforme o fluxo aprovado.

## Mapa da documentação

- [README.md](README.md): início rápido e visão operacional curta.
- [DESIGN.md](DESIGN.md): arquitetura e invariantes estruturais.
- [Índice da documentação](docs/README.md): ordem de leitura e inventário.
- [Visão geral e objetivos](docs/19-visao-geral-e-objetivos.md): produto e
  entregáveis.
- [Arquitetura e fluxo de dados](docs/20-arquitetura-e-fluxo-de-dados.md): pipeline,
  componentes e persistência.
- [Catálogo de dados e métricas](docs/21-catalogo-de-dados-e-metricas.md): modelos,
  fórmulas e regras de ausência.
- [Guia operacional](docs/22-guia-operacional.md): uso seguro da interface e CLI.
- [Guia de desenvolvimento](docs/23-guia-de-desenvolvimento.md): alteração, testes e
  validação.
- [Template corporativo](templates/corporate/README.md): contrato do shell DOCX.
- [Instruções para agentes](AGENTS.md): regras globais, complementadas pelos
  `AGENTS.md` de cada diretório.

## Limites confirmados

- Cloud não reconstrói retroativamente um período sem snapshot compatível.
- WAS depende da disponibilidade e das permissões do tenant.
- A interface é local e não está pronta para exposição pública ou uso
  multiusuário.
- O projeto publica arquivos localmente; não distribui relatórios automaticamente
  por e-mail ou serviço remoto.
- Retentativas seletivas dependem de checkpoint ou snapshot íntegro para evitar
  repetir coleta concluída.
- Alterações editoriais nos modelos oficiais exigem aprovação e inspeção visual.

## Como manter este contexto

Atualize este arquivo no mesmo ciclo sempre que mudar um contrato de produto,
arquitetura, operação, persistência, segurança, relatório ou fluxo Git. Atualize
também o guia detalhado afetado e os testes do comportamento executável.

Não use este arquivo para números de clientes, status momentâneo, identificadores,
datas da última coleta ou incidentes em andamento. Registre apenas capacidades e
limites confirmados, ajuste a data e a base verificada, valide todos os links e
preserve documentos históricos com seus avisos de contexto.
