# Guia operacional

## Pré-requisitos

- Windows com PowerShell;
- Python 3.11 ou superior;
- PostgreSQL local acessível;
- LibreOffice para inspeção visual automatizada dos DOCX;
- chaves Tenable VM/WAS individuais para cada cliente;
- token de conta de serviço Cloud quando esse produto estiver habilitado.

Não é necessário usar a linha de comando para a rotina diária. Os comandos abaixo
servem para instalação e recuperação administrativa.

Consulte [CONTEXTO.md](../CONTEXTO.md) antes de retomar uma operação iniciada em
outro ciclo ou de interpretar um documento histórico.

## Instalação inicial

Na raiz do projeto:

```powershell
.\scripts\setup.ps1
.\scripts\bootstrap_postgresql.ps1
```

O segundo comando solicita a senha administrativa do PostgreSQL de forma oculta,
cria ou atualiza o banco e grava somente a credencial limitada da aplicação no
arquivo local apropriado. Use
[credentials/database.env.example](../credentials/database.env.example) como
referência de nomes, sem colocar a senha real no arquivo de exemplo.

## Iniciar a interface

```powershell
cd C:\Codex\RelatorioTenableMensalv2
powershell.exe -ExecutionPolicy Bypass -File ".\scripts\run_web.ps1"
```

O painel local fica em `http://127.0.0.1:8765`. Se uma alteração recente não
aparecer, encerre todas as instâncias antigas e inicie novamente pela raiz correta.
Encerre o servidor com `Ctrl+C` no mesmo PowerShell que o iniciou. A interface não
possui proteção para exposição pública e deve permanecer no ambiente local.

## Cadastrar um cliente

1. Abra **Gerenciar clientes** e escolha **Adicionar cliente**.
2. Informe o nome; IDs técnicos são gerados automaticamente.
3. Salve Access Key e Secret Key no formulário local.
4. Mantenha **Vulnerabilidades WEB** habilitado quando desejar detecção automática;
   ausência de WAS não interrompe VM.
5. Habilite **Cloud Security** somente quando o cliente possuir o produto e informe
   o token próprio no campo Cloud.
6. Habilite relatório customizado, filtros de validação e coluna `Output` conforme
   a necessidade do cliente.
7. Salve e use **Testar API** e, quando aplicável, **Testar API Cloud** no próprio
   cliente.

As chaves ficam no arquivo local ignorado pelo Git e não retornam para a tela.

### Controle de documento e lista de distribuição

Em **Admin → Controle de documento**, configure o padrão global das tabelas
**Preparação** e **Controle de Versionamento**. As datas permanecem dinâmicas e são
preenchidas automaticamente na geração. Na mesma área, cadastre as entidades que
devem constar na **Lista de Distribuição** de todos os documentos, sempre com nome,
organização e e-mail. Clique em **Incluir** para montar a lista e em **Salvar
Controle de Documento padrão** para gravar as três tabelas.
O arquivo operacional `orchestration/document-control.json` é local e ignorado pelo
Git; `orchestration/document-control.example.json` mostra somente dados
sanitizados.

Em **Gerenciar clientes**, a única personalização do Controle de Documento é
**Destinatários adicionais do cliente**. Use-a apenas para entidades específicas
daquele cliente. A ordem final da tabela é:

1. destinatários da distribuição padrão;
2. destinatários adicionais do cliente.

E-mails iguais são emitidos uma única vez e o cadastro padrão prevalece. As mesmas
linhas e os mesmos cabeçalhos são usados nos relatórios Geral e Cloud. As tabelas
de Preparação e Controle de Versionamento não têm configuração por cliente: elas
sempre usam o padrão global produzido pelo componente editorial compartilhado.
Quando a execução usa
`--mask-sensitive`, nome, organização e e-mail permanecem vazios também no Cloud e
nas retentativas que reconstroem o documento.

### Analistas responsáveis

O catálogo em **Gerenciar clientes** é apenas metadado operacional: não cria conta,
login ou permissão. Cadastre um nome único, associe opcionalmente um responsável ao
cliente e use **Sem responsável** quando ainda não houver definição. Analista
inativo continua visível nos clientes já vinculados, mas não aceita nova atribuição.
Exclusão é bloqueada enquanto existir vínculo; a operação normal é desativar.

O filtro por analista no painel combina com a busca textual e altera somente os
cards exibidos. Ele não muda vulnerabilidades, relatórios nem lotes já confirmados.
Nos cards da carteira, os identificadores técnicos ficam ocultos e o bloco
**Módulos ativos** mostra `VM` para todos os clientes, além de `WAS` e `CLOUD`
quando habilitados. `client_id` e `tenant_id` continuam disponíveis somente ao
abrir **Gerenciar clientes → Editar**.

O painel atualiza o estado sem reconstruir cards que não mudaram: durante trabalho
ativo a consulta ocorre a cada 3 segundos e, quando a fila está ociosa, a cada 15
segundos. A atualização pausa enquanto a aba está oculta e é retomada imediatamente
ao voltar. Mudanças apenas no relógio do servidor não redesenham a interface.

Em **Ver alertas**, use **Marcar todos como lidos** para reconhecer as ocorrências
que já foram analisadas. Essa ação não exclui jobs, erros nem avisos do histórico:
ela grava apenas um corte temporal local em
`orchestration/dashboard-alerts.json`. O contador, o banner e a lista passam a
mostrar somente ocorrências posteriores. Um cliente com relatório já publicado e
falha ou aviso reconhecido aparece como **Concluído**; sem relatório publicado, o
estado de falha permanece visível. Recuperações WAS anteriores ao corte também
deixam de exibir o ícone vermelho no cartão, mas uma recuperação nova volta a
sinalizá-lo. Uma nova execução sempre volta a usar seu resultado real.

## Cloud Security

1. Em **Gerenciar clientes**, habilite **Cloud Security**.
2. Informe o token da conta de serviço. Ele é salvo como `TCS_API_SECRET` no arquivo
   local `credentials/<client_id>.env`; um campo vazio em edição preserva o token
   existente.
3. Escolha o ambiente GraphQL correspondente ao tenant.
4. Clique em **Testar API Cloud** antes da primeira coleta. O teste valida somente
   credencial e contrato mínimo; ele não gera o relatório completo. A interface não
   possui seletor de modelo: toda execução Cloud habilitada gera o único documento
   padrão completo.

Quando habilitado, Cloud começa junto com VM, WAS, customizado e TAG, mas possui
progresso e falha próprios. A coleta representa o estado no instante da execução.
Solicitar um período passado não reconstrói o fechamento histórico sem uma
fotografia Cloud compatível já preservada.

As colunas `Fixed by` dependem de uma fonte GraphQL opcional do tenant. Quando a
fonte não existir, não houver permissão ou a vulnerabilidade não informar versão, o
relatório usa `N/D` e continua. Isso não deve ser tratado como falha da coleta Cloud
obrigatória nem preenchido manualmente por inferência de texto.

Uma fotografia exata pode ser reutilizada. Outra coleta Cloud completa dentro de
24 horas é bloqueada por padrão para evitar consumo repetido; atualização forçada
exige ação manual explícita. Se Cloud falhar, os demais documentos permanecem
válidos e o histórico oferece **Tentar Cloud novamente**, que não repete VM, WAS,
customizado ou TAG.

## TAGs e relatórios por TAG

1. No perfil do cliente, clique em **Buscar TAGs da Tenable**.
2. Habilite **Relatórios por TAG** somente para clientes que precisam deles.
3. Marque **Gerar relatório** nas TAGs desejadas.
4. Marque **Comparativo temporal** apenas nas TAGs que precisam dessa análise.

Uma única coleta geral atende os relatórios gerais e os recortes por TAG. Escolher
uma TAG não reduz o universo dos relatórios gerais.

O Workbench da Tenable lista no máximo 5.000 ativos. Se uma TAG exceder esse limite,
a aplicação inicia automaticamente um Asset Export v1 exclusivo para o escopo da
TAG, acompanha UUID e chunks e então aplica o recorte sobre a coleta VM geral já
existente. Portanto, essa operação adicional coleta somente a associação de ativos;
ela não repete findings, WAS nem altera os documentos gerais. Se a coleta do escopo
falhar, o aviso da TAG permanece retentável e os demais documentos são preservados.

## Gerar relatórios

### Cliente individual

No card do cliente, escolha gerar relatório, confirme o modo manual e defina:

- padrão: um mês móvel até agora;
- últimos dias: janela de `X` dias;
- período específico: datas inicial e final escolhidas.

No período específico, as duas datas são inclusivas para o analista. A aplicação
transforma a data final no início do dia seguinte: selecionar 01/07 a 31/07 produz
o intervalo técnico `[01/07 00:00, 01/08 00:00)`. Assim, nenhum minuto do último
dia é perdido.

Quando existir um snapshot compacto exato, a execução padrão o reutiliza e não
abre novos exports. Para testar a integração ou atualizar deliberadamente a coleta,
marque **Forçar nova coleta pela API**. A opção vale somente para aquela execução,
preserva o snapshot anterior e fica visível no progresso do card. Em períodos já
encerrados, a nova coleta é uma reconstrução histórica e pode divergir do estado
observado no fechamento original. O fluxo automático mensal não força a coleta
quando já existe um snapshot exato.

**Forçar nova coleta** e **Propriedades seletivas** são controles independentes.
O primeiro decide entre replay e novos jobs de API; o segundo altera o payload do
export VM somente quando habilitado e validado para o tenant. Portanto, é possível
testar uma coleta real nova mantendo o payload completo e a rota VM tradicional.

Se Cloud estiver habilitado, a mesma execução também inicia o componente GraphQL e
publica o documento padrão. Isso não altera o período nem o universo VM.

### Evidência autenticada de referência

Em 24/08/2026, uma execução manual sanitizada para período mensal encerrado foi
concluída em uma tentativa, sem replay, com os novos exports VM e WAS em
`FINISHED` e todos os chunks processados. Foram publicados os dois DOCX gerais e
um DOCX por TAG habilitada. A limpeza pós-publicação removeu o staging pesado e
preservou o snapshot compacto.

A rota usada nessa validação foi `legacy_vm`, com propriedades seletivas
desativadas. Como essa origem aplica `since` como limite inferior, mas não oferece
limite superior, a aplicação delimitou o fim do período localmente e registrou
`HISTORICAL_RECONSTRUCTION` com aviso. A duração observada foi de aproximadamente
seis minutos; ela é apenas evidência operacional, não um SLA para outros tenants.

### Carteira

Use **Gerar todos** para abrir a seleção dos clientes ativos e com credenciais
prontas. Todos começam marcados. Pesquise, filtre por analista ou **Sem
responsável**, selecione/limpe somente os resultados visíveis e confirme
**Gerar N clientes**. Seleção vazia não cria lote; o servidor valida a lista
explícita e persiste a fotografia de incluídos, excluídos, filtro e responsáveis.

Nesse fluxo coletivo, VM, WEB e Cloud habilitados são acompanhados como componentes
independentes. Falha retentável abre automaticamente uma segunda janela de 10
horas para o componente afetado, consultando primeiro seu UUID/cursor. Uma terceira
janela só existe quando a segunda precisou criar uma operação substituta.

### Controle durável do lote

O bloco da carteira representa o lote persistido, não somente a memória do servidor.
Lotes novos usam `STAGED_V1`: a coleta remota é concorrente entre clientes e a
montagem/publicação é serial. Com configuração automática, a capacidade remota é o
menor valor entre clientes elegíveis e 64; `remote_collection_workers` pode
reduzi-la. `local_build_workers` permanece obrigatoriamente em 1. Use:

- **Pausar após o atual** para impedir novos claims e permitir que fases já ativas
  salvem seus checkpoints;
- **Parar lote** para sinalizar cooperativamente todos os subprocessos locais ativos
  e cancelar os itens ainda não iniciados; confirme o identificador curto;
- durante uma espera de decisao WAS, **Parar lote** encerra o item imediatamente;
  um `INTERRUPT_REQUESTED` sem worker ativo e concluido no proximo reinicio;
- **Retomar lote** para liberar somente trabalhos preservados e retomáveis em sua
  fase;
- **Tentar falhas/interrompidos** depois do término para criar uma fila somente de
  `FAILED`, `INTERRUPTED` e `CANCELLED_BY_USER`, incluindo falhas locais posteriores
  a componentes remotos já completos; o lote derivado passa a ser a seleção ativa
  do painel assim que a API o cria;
- **Gerar todos novamente** quando a seleção precisa de nova execução.

A pausa não repete cliente concluído. A retomada também não reabre falha ou
interrupção; essas situações exigem o lote derivado. `COMPLETE_WITH_FAILURES` é um
resultado terminal do lote, não um estado ainda executando.

Reiniciar o servidor não executa trabalho preservado. Jobs que estavam em coleta
ou montagem são classificados como `INTERRUPTED/TERMINAL`, e o lote fica pausado.
Selecionar o lote também não inicia nada: clique em **Tentar falhas/interrompidos**
para autorizar a tentativa derivada. Se todos os componentes do cliente já têm
checkpoint publicável, essa ação faz somente consolidação e montagem locais, sem
nova chamada VM, WAS ou Cloud.

Na recuperação da mesma execução, assets, VM e TAGs já completos são validados e
reutilizados. Se o documento desse `run_id` já estiver publicado e íntegro, a
tentativa é concluída usando a publicação existente; ela não duplica histórico nem
reescreve os DOCX. Qualquer incompatibilidade de cliente, período, consulta, TAG ou
hash interrompe a recuperação com erro local explícito.

Quando os DOCX e o manifesto já existem, mas a execução terminou com `MemoryError`
antes de registrar os documentos, use a mesma ação de retentativa. A aplicação
revalida os arquivos e repara o PostgreSQL sem repetir coleta ou montagem.

Ao parar, o arquivo de controle solicita uma saída cooperativa. O export remoto não
é cancelado: UUID, manifesto parcial e chunks persistidos ficam disponíveis para uma
retentativa. Cada job ativo é controlado separadamente; se um subprocesso não
responder, o fallback encerra somente sua árvore e registra PID/evento. Depois, use
a ação de falhas/interrompidos em vez de reabrir resultado terminal.

O orçamento padrão de 10 horas pertence à janela do componente, não a cada POST.
Ao retomar o mesmo UUID/cursor, a aplicação usa o saldo persistido. Se a origem
confirmar que o identificador expirou ou é irrecuperável, a Janela 2 cria um único
substituto usando o saldo que ainda resta; ela não reinicia o relógio. Essa
substituição habilita a Janela 3. Após seu fim, somente uma ação manual explícita
abre nova janela. Valores transitórios de versões anteriores não viram configuração
global.

Lotes anteriores permanecem em `LEGACY`. Eles conservam o worker monolítico e não
são migrados automaticamente para o pipeline em fases. Uma retentativa derivada de
um lote importado `RECOVERED` é a exceção: período, UUID e manifesto continuam
preservados, enquanto os jobs derivados entram em `STAGED_V1` para consulta e
download remotos concorrentes. A montagem local continua serial.

O seletor de lotes apresenta data e hora, tipo, prefixo do ID e estado. Antes de
usar **Tentar falhas/interrompidos**, confirme especialmente o tipo `Gerar todos` e
o prefixo do lote desejado.

### Automático mensal

A tarefa chama `run-monthly-batch` no primeiro dia, às 00:05 por padrão, e usa o
mês anterior completo. A chave `automatic-monthly:<carteira>:<competência>` torna
a chamada idempotente: repetir o script acompanha ou retorna a mesma família.

No painel **Admin → Automação mensal**:

1. ajuste e salve o horário; isso não cria lote nem altera o Windows;
2. use **Validar sem executar** e confira competência e clientes elegíveis;
3. use **Sincronizar tarefa** somente após a confirmação exibida;
4. ative ou desative a tarefa separadamente.

O script executado é `scripts/run_monthly_orchestration.ps1`; a instalação manual
continua disponível em `scripts/install_monthly_task.ps1`. VM, WAS e Cloud usam a
mesma política 10h + 10h + 10h condicional do botão **Gerar todos**.

## Acompanhar progresso

O card informa etapa, cliente, execução e alertas. O painel do lote agrega coleta
na fila/ativa, decisão WEB, fila de montagem, montagem ativa, legado e terminal,
além da concorrência remota efetiva e da fila local. Durante VM, observe UUID do
export, origem, status e chunks persistidos. Durante WAS e Cloud, acompanhe estados
independentes.

Os contadores do lote são filtros da família inteira, não somas dos jobs brutos.
Use **Todos**, **Pendentes**, **Em execução**, **Em retry automático**,
**Aguardando retry manual**, **Semiconcluídos**, **Falha definitiva** ou
**Concluídos**; busca e responsável continuam combinados com o filtro escolhido.

Estados importantes:

- **Coleta remota na fila**: fase `REMOTE_QUEUED`;
- **Coleta remota**: fase `REMOTE_RUNNING`;
- **Aguardando decisão WEB**: fase `REMOTE_WAITING_DECISION`;
- **Pronto para montagem**: checkpoint validado em `READY_FOR_BUILD`;
- **Montando documento**: worker local único em `BUILD_RUNNING`;
- concluído/falhou: fase `TERMINAL`;
- falha temporária: pode receber nova tentativa;
- falha permanente: requer correção de perfil, credencial ou contrato.

Um export com `total_chunks=1` ainda pode estar processando. Não considere o número
de chunks como confirmação de término. Enquanto a Tenable não informar total, a
interface mostra exatamente `0/0 · aguardando a Tenable informar chunks`. O
indicador `checkpoint validado` é booleano; a interface e a API não mostram o
caminho do arquivo.

O seletor **Lotes recentes** conserva os dez lotes mais novos no painel. Use
**Ver clientes do lote** para carregar os jobs e eventos somente quando precisar
do detalhe. O indicador “Tenable confirmou” é atualizado apenas após uma resposta
HTTP 200 do endpoint de status; “último progresso real” muda somente quando estado,
contadores ou chunks avançam. Uma falha transitória de polling não é apresentada
como confirmação remota.

### Falha isolada no WAS

Na execução manual individual, a interface mantém VM, assets, TAG e Cloud já
coletados e solicita uma decisão:

- **Continuar sem WEB** conclui a publicação com alerta;
- **Tentar WEB novamente** executa somente WAS e reutiliza chunks já persistidos.

Em **Gerar todos** e na execução mensal automática, WAS usa as duas janelas
automáticas e a terceira condicional do coordenador. Ao esgotar a política, o
conjunto permanece semiconcluído e **Tentar WEB novamente** continua disponível
quando a causa for retentável. Essa ação posterior usa uma única janela manual,
reconstrói o contexto pelo histórico compacto e substitui os DOCX VM/TAG sem
repetir assets, VM, TAG ou Cloud. O comportamento também vale para uma execução
manual que já tenha sido publicada e cujo staging pesado tenha sido reciclado.
Antes da substituição, a aplicação valida o snapshot compacto e, após concluir,
confirma que o hash da coleta VM permaneceu inalterado.

No Word, “não foram identificadas vulnerabilidades WEB” significa coleta concluída
sem ocorrências. A mensagem “não foi possível concluir a coleta WEB” indica dados
indisponíveis e não deve ser validada na plataforma como zero.

A coluna **Família** da tabela WAS depende de correspondência do Plugin ID no
catálogo oficial. Se nenhuma linha exibida tiver correspondência, a coluna inteira
é omitida. Isso não significa falha da coleta WAS e não deve ser interpretado como
categoria OWASP ausente.

### Componentes e retentativas

O detalhe do conjunto mostra `VM_CORE`, `WAS` e `CLOUD` separadamente. Um
conjunto parcial mantém todos os documentos já válidos. **Tentar componentes com
falha** seleciona somente componentes cuja tentativa mais recente está falha ou
interrompida e marcada como retentável; **Selecionar componentes** permite reduzir
essa seleção. Componente concluído fica desabilitado.

Na família, **Falha definitiva** destaca os clientes que exigem correção prévia;
falha antiga deixa de contar quando há retry automático posterior. O botão
**Tentar falhas, parciais e interrompidos** usa a mesma decisão exibida em
**Ver clientes do lote**. Registros antigos `UNEXPECTED` são reinterpretados
somente quando a mensagem comprova timeout, export sem progresso ou
indisponibilidade do PostgreSQL; o código original continua visível para auditoria.
Falha realmente não retentável não entra silenciosamente no lote derivado.

Também são recuperáveis os registros antigos com `WinError 3/206` cujo caminho
identifica `tenable_vm_assets_v2`, `tenable_vm_vulnerabilities` ou
`tenable_was_findings` e contém um UUID válido. Depois de reiniciar o servidor com
a correção instalada, **Tentar falhas, parciais e interrompidos** reaproveita esse
UUID. Para uma falha em assets, a aplicação termina o download dos assets e somente
então inicia um export novo de vulnerabilidades VM, pois esse segundo export ainda
não existia.

Se a consulta do UUID retornar estado terminal, expiração ou HTTP 404, apenas o
componente afetado cria uma operação substituta e registra o evento correspondente.
Uma resposta 401 não entra nesse fallback: corrija a credencial/permissão, execute
**Testar API** e só depois faça uma nova tentativa para o cliente.

Cloud é independente e o retry integrado reaproveita dataset/checkpoint quando
válido, sem repetir VM ou WAS. WAS precisa de base VM reutilizável para reparar
somente documentos com seção WEB. VM pode retomar UUID/chunks ou normalizar raw
completo sem abrir API nova. A substituição só ocorre depois da validação; falha
preserva manifesto, hashes, `MAIN` e documentos anteriores.

Quando o dataset Cloud já está completo no checkpoint, **Tentar Cloud novamente**
executa somente validação, montagem e publicação locais. O conjunto original passa
a mostrar uma nova tentativa `COMPLETE`, ganha o DOCX Cloud e recebe uma nova
revisão do snapshot compacto; a fotografia parcial anterior não é alterada. Se o
componente já estiver `COMPLETE`, uma nova retentativa Cloud é corretamente
recusada, pois não há dado faltante a recuperar.

No bootstrap padrão, o executor faseado oferece retry seletivo para VM, WAS e
Cloud. Conjuntos excluídos nunca podem ser retentados.

Para uma execução com coleta nova, confirme também:

- indicação de coleta forçada no card;
- origem `created` em vez de replay;
- VM e WAS independentes, cada um em `FINISHED` quando disponível;
- Cloud independente, com fotografia e documento padrão publicado quando habilitado;
- quantidade de chunks concluídos igual à quantidade total;
- rota e estado de reconstrução registrados no resultado;
- DOCX esperados disponíveis e limpeza concluída após a publicação.

## Export preso e cancelamento

Em `STAGED_V1`, 900 segundos sem progresso geram alerta, mas não encerram a
coleta. O teto padrão é de 36.000 segundos (10 horas) por janela do componente,
somando fila e processamento. Esse prazo não é reiniciado pelo servidor nem pela
criação de um substituto dentro da mesma janela. Ao atingir o teto,
o processo local termina como falha retentável, preservando UUID, checkpoint e
chunks. A aplicação não cancela automaticamente o export remoto.

O prazo de 10 horas não é o timeout de uma requisição HTTP nem uma garantia de
conclusão da Tenable. Cada consulta continua curta. Respostas 429, 5xx e falhas de
transporte são tratadas com backoff dentro do orçamento; 401 continua sendo erro de
credencial/permissão e não é absorvido. Resposta 200 em `QUEUED` ou `PROCESSING`
confirma que a Tenable ainda reconhece o job, mas não prova progresso.

No mesmo pipeline faseado, a consulta GraphQL e a gravação do dataset Cloud
acontecem na fase remota. O worker local serial apenas valida o checkpoint e
renderiza o DOCX. Se o Cloud falhar, o checkpoint registra `FAILED`, os relatórios
VM continuam sendo publicados e o componente permanece disponível para
retentativa; um estado `PENDING` nunca é enviado diretamente ao build.

Se a falha ocorrer depois de o dataset Cloud ter sido gravado, a retentativa valida
seu hash e repete somente a publicação do snapshot. Ela não faz outra coleta
GraphQL. Em execuções antigas, a aplicação também procura o dataset no staging da
tentativa anterior e só o aceita após validar identidade e conteúdo.

A documentação oficial informa que cada chunk VM fica disponível para download
por até 24 horas depois de criado; por isso o coletor persiste cada chunk assim que
ele aparece, inclusive antes de `FINISHED` e fora de ordem. Depois desse prazo, um
404 pode significar expiração do chunk: [Download vulnerabilities chunk](https://developer.tenable.com/reference/exports-vulns-download-chunk).

Use **Cancelar export e tentar novamente** somente como ação manual, com confirmação
do UUID e da execução, depois de avaliar o impacto no trabalho remoto preservado.

Depois de um timeout, preserve o diretório da tentativa. Mesmo com `0/N` chunks,
o manifesto parcial contém o UUID necessário para salvar a operação. Na próxima
tentativa do mesmo período e trabalho lógico, a aplicação consulta esse UUID antes
de criar outro export. A mesma verificação é aplicada quando a fila repassa o UUID
explicitamente para a retentativa, antes de aguardar ou baixar chunks:

- `PROCESSING` ou `QUEUED`: continua aguardando o mesmo job;
- `FINISHED`: baixa os chunks ainda disponíveis e reaproveita os já persistidos;
- `CANCELLED`, `FAILED`, `ERROR` ou HTTP 404: cria um novo job;
- `FINISHED` com chunks restantes indisponíveis: considera o conteúdo expirado e
  cria um novo job.

No detalhe do lote, **Verificar export preservado** cria uma retentativa somente
para o cliente selecionado. A aplicação consulta o mesmo UUID e baixa apenas os
chunks ainda ausentes. Se o UUID estiver comprovadamente terminal, expirado, em
HTTP 404 ou `FINISHED` sem os chunks restantes, o evento
`TENABLE_EXPORT_RECOVERY_UNAVAILABLE` registra o motivo e o UUID substituto antes
de continuar. Esse evento é persistido antes da espera do novo export, promovendo o
UUID substituto e reiniciando seu orçamento. Não existe fallback silencioso para um
POST novo.

Falhas de autenticação não autorizam a criação de export duplicado. Rate limit,
5xx e indisponibilidade transitória de consulta permanecem visíveis nos eventos,
mas o polling continua dentro do orçamento total.

### Falhas locais e checkpoint

Antes de entrar em `READY_FOR_BUILD`, o checkpoint lista e valida o manifest
normalizado, assets, findings, snapshots e datasets TAG/WAS necessários. Execução
manual lê sempre o escopo `data/manual`; automática mensal lê
`data/automatic-monthly`. Ausência de uma dependência resulta em
`CHECKPOINT_ARTIFACT_MISSING`, e divergência de escopo em
`LOCAL_ARTIFACT_SCOPE_MISMATCH`, sem criar publicação parcial.

VM, WAS e Cloud podem terminar em horários diferentes. Assim que os componentes de
um cliente ficam terminais durante uma execução ativa, ele entra individualmente em
`READY_FOR_BUILD`; não é necessário esperar o lote inteiro. Após reiniciar o
servidor, o job abandonado fica interrompido até a retentativa explícita. Quando os
checkpoints já estão completos, essa retentativa segue para montagem sem nova
chamada à Tenable. A sequência esperada nos eventos é
`REMOTE_COMPONENTS_CONSOLIDATING`, `COLLECTION_READY`, `BUILD_STARTED` e
`JOB_FINISHED`.

Se a consolidação local falhar, o estado visível é
`CHECKPOINT_COMPONENT_INCOMPLETE`. Os dados coletados permanecem preservados para
retentativa; não use **Gerar todos** para contornar essa falha.

No Windows, contenção transitória ao substituir `export-state.json` é retentada
com arquivo temporário exclusivo. Persistência da contenção resulta em
`LOCAL_FILESYSTEM_TRANSIENT`. Se isso ocorrer apenas na telemetria WAS opcional,
VM permanece preservado e o WAS recebe `WAS_LOCAL_STATE_TRANSIENT`.

### Importar um snapshot de recuperação

Use esta intervenção somente para o snapshot legado criado antes da fila durável e
com o servidor antigo encerrado. Primeiro faça backup lógico das tabelas
`web_batches`, `web_batch_jobs` e `web_batch_events` e execute somente a análise:

```powershell
.\.venv\Scripts\python.exe -m tenable_reports import-web-batch-recovery `
  --snapshot C:\caminho\recovery-gerar-todos.json `
  --database-env-file .\credentials\database.env `
  --dry-run
```

O retorno mostra apenas totais. Confirme `COMPLETE`, `FAILED`, `INTERRUPTED` e
`QUEUED`; o lote será criado como `PAUSED`. Depois de revisar:

```powershell
.\.venv\Scripts\python.exe -m tenable_reports import-web-batch-recovery `
  --snapshot C:\caminho\recovery-gerar-todos.json `
  --database-env-file .\credentials\database.env `
  --apply
```

A aplicação usa o hash para idempotência. Uma falha provoca rollback da transação;
não repita manualmente inserts. Se a validação posterior reprovar o resultado,
restaure o backup lógico antes de iniciar qualquer lote novo. Nunca use **Retomar
lote** para trabalhos importados como `FAILED` ou `INTERRUPTED`; crie **Tentar
falhas/interrompidos**.

Essa ação é permitida para o lote especial `RECOVERED/PAUSED`. Cada trabalho
derivado conserva o período original e o `vm_export_uuid`; o executor consulta
primeiro esse export remoto e só cria outro conforme as regras de estado acima.

## Propriedades seletivas

O padrão seguro é payload completo. A opção seletiva deve ser ativada por cliente
somente após **Validar export otimizado** comprovar equivalência de contagens,
identidades, datas, Top 5 e indicadores de exploração no tenant.

Se a API rejeitar `properties` com HTTP 400 ou retornar contrato incompleto, ocorre
um único fallback para o payload completo. A validação real cria exports e exige
confirmação consciente do analista.

## Relatórios publicados e `MAIN`

Na lista de documentos do cliente é possível:

- baixar o DOCX;
- baixar em ZIP todos os documentos de um conjunto específico;
- excluir permanentemente o conjunto completo com confirmação;
- promover uma geração como `MAIN` para o próximo comparativo.

Cada conjunto mostra o período do relatório, a data e a hora em que a execução foi
concluída e, em uma linha técnica separada, o `run_id` e o tamanho total. Para
publicações legadas sem horário de conclusão, a interface usa o horário em que o
documento foi registrado no PostgreSQL.

O botão **Baixar ZIP mensal**, no topo do painel, solicita o mês e cria uma pasta
`Relatorios-Tenable-AAAA-MM`, com uma subpasta por cliente. Esse pacote inclui
somente o conjunto `MAIN` de cada cliente naquele mês. Clientes sem `MAIN` e
documentos registrados que não estejam mais no disco são omitidos e relacionados
em `RESUMO.txt`; os demais clientes continuam no download. O mês mais recente fica
pré-selecionado. Se existirem referências `MAIN` de contextos diferentes para o
mesmo cliente e mês, o pacote usa a promoção `MAIN` mais recente e registra a
decisão no resumo.

O mesmo diálogo permite escolher um analista responsável. Nesse caso, entram
somente os clientes atualmente vinculados a ele, ainda separados em uma pasta por
cliente e ainda limitados ao conjunto `MAIN`. Deixe **Todos os responsáveis** para
baixar a carteira completa. O filtro não altera `MAIN`, documentos ou cadastro.

O botão **Baixar conjunto ZIP**, dentro do histórico do cliente, baixa exatamente
a geração escolhida, ainda que ela não seja `MAIN`. Conjuntos excluídos não podem
ser baixados. Os ZIPs são temporários e não passam a ocupar espaço durável depois
que a resposta termina. A preparação expira em cinco minutos quando o download não
é iniciado.

Nos dois fluxos, a montagem ocorre em segundo plano e a interface mostra as etapas
**Selecionando relatórios**, **Verificando documentos**, **Montando arquivo ZIP** e
**Pronto para baixar**, com percentual e contagem de itens quando disponível. Ao
chegar a 100%, o navegador inicia o download automaticamente. O percentual cobre a
preparação no servidor; depois que o navegador assume a transferência, o progresso
em bytes pertence ao próprio gerenciador de downloads do navegador. Se a montagem
falhar, o botão é liberado novamente e a mensagem permanece visível para uma nova
tentativa.

## Tradução das descrições

Descrições e soluções técnicas em inglês dos detalhamentos VM, TAG e Cloud são
traduzidas automaticamente para português do Brasil durante a montagem. Textos
extensos são enviados em partes ordenadas de até 900 caracteres para evitar que o
serviço rejeite o conteúdo. Se uma parte falhar, o relatório mantém apenas aquela
parte no idioma original, continua as demais e mostra um aviso explícito.

A tradução utiliza o Google Translate como serviço externo. Somente os campos
editoriais de descrição, sinopse, solução, remediação e contramedida são enviados:
Plugin Output, IP, hostname, URI, TAG, conta e tabelas de hosts permanecem locais.
O mesmo contrato vale para VM, WAS, relatórios por TAG e Cloud. Há cache por
execução, timeout e retentativa curta para erros transitórios; a indisponibilidade
do tradutor não bloqueia a publicação e o erro não registra o conteúdo enviado.
O parser aceita a forma aninhada retornada pelo endpoint do Google e descarta o
código do idioma de origem. Em uma correção controlada de documentos já publicados,
somente esses mesmos blocos editoriais podem ser alterados; manifesto, hashes no
PostgreSQL e ZIPs posteriores passam a apontar para os DOCX atualizados.

Para excluir um conjunto:

1. clique em **Excluir conjunto**;
2. confira período, quantidade de documentos, arquivos e espaço ocupado;
3. se a geração for o único `MAIN` do período, leia o alerta de que a exclusão
   deixará aquele período sem referência para comparações futuras e decida se quer
   continuar;
4. informe o motivo e digite exatamente `EXCLUIR`;
5. se houver outra geração compatível, escolha obrigatoriamente uma substituta.

Quando não existe substituta compatível, continuar após o alerta autoriza somente
aquela exclusão a deixar o período sem `MAIN`. Cancelar o alerta não altera arquivos
nem banco. A API também exige essa autorização explícita; a confirmação visual não
é apenas informativa.

A exclusão não prossegue se existir geração ativa para o cliente ou se algum alvo
estiver fora da raiz `data`. Quando concluída, remove do disco os DOCX gerais,
customizados, por TAG e Cloud, o manifesto e os demais arquivos registrados; também
remove snapshots compactos VM e Cloud, publicação, documentos, artefatos, eventos e
execução associados
no PostgreSQL. Não há botão de desfazer. Registros legados anteriormente excluídos
de forma lógica ainda podem mostrar **Restaurar**, mas esse não é o fluxo das novas
exclusões.

Uma execução automática bem-sucedida torna-se `MAIN` por padrão. Se o analista
refizer o relatório por falta de dados, deve promover manualmente a melhor versão.
O sistema nunca escolhe a base apenas pelo nome do arquivo.

O backfill de relatórios antigos está disponível na área administrativa. A análise
é exibida antes da aplicação; arquivos ambíguos não devem ser associados sem
revisão.

## Armazenamento

Os documentos publicados e o histórico compacto permanecem. Staging pesado é
removido depois do sucesso. Em falhas, ele fica temporariamente disponível para
diagnóstico e retomada e depois entra na limpeza.
Quando Cloud falha com checkpoint reutilizável, seu staging permanece protegido até
a retentativa ou a janela de retenção aplicável.

Checkpoint é metadado operacional interno. O painel pode informar que ele foi
validado, mas não revela caminho local; não copie caminhos de staging para chamados,
logs compartilhados ou documentação.

Documentos e histórico compacto deixam de permanecer quando o próprio conjunto é
excluído explicitamente pela interface.

### Atualização do padrão editorial de documentos publicados

Use a republicação apenas para aplicar o shell oficial aos DOCX dos conjuntos
`MAIN` registrados no PostgreSQL. Ela não realiza coleta, não altera a seleção
`MAIN` e ignora conjuntos não-MAIN, órfãos, artefatos de QA e a área de descarte.
Execute primeiro a análise:

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
.\.venv\Scripts\python.exe tools\refresh_official_report_documents.py
```

Depois de conferir as contagens, a aplicação controlada exige PostgreSQL disponível.
A composição Open XML preserva os relacionamentos e mantém `w:updateFields`
habilitado; o Word atualiza o sumário nativo ao abrir o arquivo:

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
.\.venv\Scripts\python.exe tools\refresh_official_report_documents.py --apply
```

O resumo deve informar `scope: postgresql-main`. Cada conjunto é substituído
atomicamente. Em falha, o conjunto original permanece;
em sucesso, hashes, manifesto e catálogo PostgreSQL são atualizados juntos e o
manifesto recebe a auditoria `OFFICIAL_REPORT_SHELL_V3`. Uma nova execução ignora
os conjuntos já marcados; `--include-completed` existe somente para reprocessamento
deliberado. Um arquivo aberto no Word falha apenas o próprio conjunto e os demais
continuam; feche o documento e execute novamente para tratar somente o pendente.
Use `--fail-fast` apenas durante diagnóstico controlado.

Não copie `data`, `credentials` ou arquivos `.env` para o Git. Antes de uma limpeza
manual, confirme que nenhum processo está em execução e que os DOCX registrados e
o histórico compacto estão preservados.

## Diagnóstico rápido

| Sintoma | Verificação |
|---|---|
| Todas as APIs falham | servidor antigo, arquivo de credenciais, relógio e acesso ao tenant |
| Rota não encontrada | reinicie o servidor usando a versão atual do projeto |
| Export VM demora | status remoto, fila, chunks, progresso e limites configurados |
| Ativos existem, mas todas as tabelas VM estão zeradas | procure `VM_FINDINGS_EMPTY_WITH_ASSETS`; confirme licença VM, permissões da chave e recência das análises no tenant antes de gerar novamente |
| WAS não aparece | licença/permissão, capacidade habilitada e eventos específicos do WAS |
| Família WAS não aparece | confirme o Plugin ID em `/was/v2/plugins`; sem correspondência real, a coluna é omitida por projeto |
| Cloud não aparece | opção habilitada, `TCS_API_SECRET`, ambiente e resultado de **Testar API Cloud** |
| Cloud falhou sozinho | consulte o alerta do componente e use **Tentar Cloud novamente** |
| Componentes terminaram, mas não montou | reinicie na versão atual e confirme `REMOTE_COMPONENTS_CONSOLIDATING` seguido de `COLLECTION_READY` |
| `CHECKPOINT_COMPONENT_INCOMPLETE` | preserve o staging e retente somente o job/componente indicado; não gere o lote inteiro novamente |
| Customizado sem comparação | existência e compatibilidade da referência `MAIN` anterior |
| Documento por TAG vazio | TAG atual, UUIDs associados, período do dataset e, para TAG acima de 5.000 ativos, UUID/chunks do Asset Export v1 de escopo |
| Disco cresce | execuções falhas retidas e política de limpeza de staging |

Logs podem ser usados para diagnóstico, mas nunca devem ser compartilhados sem
revisão de dados sensíveis.

## Operação por linha de comando

Para intervenção controlada, consulte a ajuda atual:

```powershell
.\.venv\Scripts\python.exe -m tenable_reports --help
.\.venv\Scripts\python.exe -m tenable_reports run-client --help
.\.venv\Scripts\python.exe -m tenable_reports collect-client --help
.\.venv\Scripts\python.exe -m tenable_reports build-client --help
.\.venv\Scripts\python.exe -m tenable_reports orchestrate --help
.\.venv\Scripts\python.exe -m tenable_reports resume-was --help
.\.venv\Scripts\python.exe -m tenable_reports retry-cloud --help
.\.venv\Scripts\python.exe -m tenable_reports run-monthly-batch --help
.\.venv\Scripts\python.exe -m tenable_reports import-web-batch-recovery --help
```

Não execute coleta real ou cancelamento fora da interface sem identificar cliente,
período, UUID e impacto. `collect-client` e `build-client` são fronteiras internas
do dispatcher faseado; não monte manualmente caminhos de checkpoint recebidos da
interface, porque eles não são expostos por esse canal.
