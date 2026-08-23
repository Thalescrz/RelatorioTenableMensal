# Relatórios Tenable

Discovery e arquitetura de uma solução evolutiva para automatizar relatórios técnicos da Tenable, com separação entre coleta, normalização, processamento, histórico, configuração de clientes, apresentação e orquestração.

## Estado atual

A fase de análise foi concluída para os scripts existentes, a documentação oficial e
quatro relatórios DOCX representativos. A geração Word produz o relatório-base, o
DOCX de inteligência/customizações e, quando configurado, relatórios operacionais
compactos por TAG.

- [Análise e arquitetura da solução](docs/01-analise-e-arquitetura.md)
- [Catálogo das APIs Tenable](docs/02-catalogo-apis-tenable.md)
- [Protocolo de análise dos DOCX](docs/03-protocolo-analise-docx.md)
- [Matriz comparativa e contrato dos dois DOCX](docs/04-matriz-e-contrato-dos-relatorios.md)
- [Histórico, regras críticas e tradução](docs/05-historico-regras-criticas-e-traducao.md)
- [Contrato do modelo normalizado da Fase 3](docs/06-modelo-normalizado-fase3.md)
- [Contrato e validação do dataset mensal da Fase 4](docs/07-dataset-mensal-fase4.md)
- [Template Word mínimo e prova da Fase 5](docs/08-template-word-fase5.md)
- [Primeiro relatório-base completo da Fase 6](docs/09-relatorio-base-completo-fase6.md)
- [Relatórios operacionais e comparativo temporal por TAG](docs/10-escopo-tags-e-comparativo-por-rede.md)
- [Perfis declarativos e variações da Fase 7](docs/12-perfis-e-variacoes-fase7.md)
- [Coleta e relatório Web App Scanning da Fase 8](docs/13-was-fase8.md)
- [Histórico e tendências da Fase 9](docs/14-historico-e-tendencias-fase9.md)
- [Orquestração e distribuição controlada da Fase 10](docs/15-orquestracao-e-distribuicao-fase10.md)
- [PostgreSQL: migração e operação](docs/16-postgresql-migracao-e-operacao.md)
- [Interface web local — MVP](docs/17-interface-web-mvp.md)

## Decisão central

Cada execução publica dois documentos gerais a partir do mesmo snapshot imutável e,
quando configurado, documentos operacionais adicionais por TAG:

1. `01-relatorio-base-<cliente>-<periodo>.docx`: núcleo estável e comum, incluindo o Top 5 detalhado de vulnerabilidades VM não mitigadas com seus hosts e, por decisão de produto, o Top 5 detalhado de vulnerabilidades WEB com suas instâncias/URIs.
2. `02-inteligencia-e-customizacoes-<cliente>-<periodo>.docx`: união modular das análises adicionais encontradas nos clientes, ativadas por perfil/capacidade e com comparativos somente quando houver snapshot anterior compatível.
3. `[cliente] Relatório de Vulnerabilidades Tenable TAG <categoria> - <valor> <periodo>.docx`: recorte VM compacto e independente para cada TAG marcada, com comparativo temporal opcional dentro do próprio documento.

Os campos em branco dos documentos de referência foram tratados como anonimização intencional. Hostname, IP, pessoa, cliente e e-mail são dados dinâmicos sensíveis; nenhum valor foi reconstruído.

## Uso local

O gerador Word usa `python-docx`. Para instalar o pacote em modo editável e executar:

```powershell
python -m pip install -e .
python -m tenable_reports validate-profile --profile .\clients\examples\client-profile.json
python -m pytest -q
```

Para uma instalação repetível no Windows, também é possível executar
`.\scripts\setup.ps1`, que cria `.venv`, instala o projeto e o driver PostgreSQL
em modo editável.

As credenciais Tenable ficam somente no `.env` local, ignorado pelo Git. A
conexão compartilhada do banco fica em `credentials/database.env`, também fora
do Git e documentada por `credentials/database.env.example`. Perfis de clientes
nunca contêm chaves.

Para provisionar o PostgreSQL 18 local, aplicar as migrations e importar os
SQLite/manifestos existentes, execute depois do setup:

```powershell
.\scripts\bootstrap_postgresql.ps1
```

A senha administrativa é solicitada de forma oculta e não é gravada. O papel de
aplicação recebe uma senha aleatória e privilégios limitados.

### Painel web local

Para cadastrar clientes, acompanhar a fila e baixar relatórios em uma interface
local simples:

```powershell
.\scripts\run_web.ps1
```

O navegador abre em `http://127.0.0.1:8765`. O botão **Gerar todos** adiciona os
clientes habilitados a uma fila sequencial, enquanto cada card permite iniciar
uma geração pontual individual e acessar os documentos já registrados.

Em **Gerenciar clientes → Coleta VM**, cada cliente possui três ajustes: estratégia
`combined` ou `split`, ativos por chunk e uso de propriedades seletivas. O padrão
seguro é **Combinada**, **1000 ativos por chunk** e **propriedades desativadas**. A
estratégia separada continua experimental e só deve ser usada para diagnóstico.

O botão **Validar export otimizado** exige confirmação porque inicia duas
exportações reais para o mesmo período: uma completa e outra seletiva. O relatório
da validação continua usando a coleta completa. O card apresenta **validação
aprovada** quando contagens, identidades anonimizadas, severidades, estados, Top 5,
datas, cobertura e indicadores de exploração são equivalentes; **revisar** mantém
as propriedades seletivas desativadas. A ativação por cliente também possui fallback
único para payload completo quando a API rejeita `properties` com HTTP 400 ou quando
o contrato retornado está incompleto. Autenticação, limite de taxa e timeout não são
mascarados por esse fallback.

### Validação autenticada do contrato

O comando abaixo inicia um export real pequeno e inspeciona somente o primeiro chunk. Ele permanece bloqueado sem `--confirm-live-api`:

```powershell
python -m tenable_reports contract-check-vm `
  --profile .\clients\examples\client-profile.json `
  --env-file .\.env `
  --confirm-live-api
```

### Coleta do snapshot

```powershell
python -m tenable_reports collect-vm `
  --profile .\clients\examples\client-profile.json `
  --env-file .\.env `
  --output-root .\data
```

`Plugin Output` é enviado como `false` por padrão. A opção `--include-output` precisa ser deliberada; `data/` permanece fora do Git.

A coleta de relatórios usa um único export combinado por padrão, com 1000 ativos por
chunk e payload completo. Cada chunk disponibilizado pela Tenable é baixado e
persistido imediatamente; se o export atingir o timeout, o manifesto parcial fica
disponível para a tentativa automática seguinte do mesmo cliente, consulta e
trabalho lógico. Chunks íntegros já armazenados não são baixados novamente.

O valor 1000 segue a faixa de 1000 a 3000 recomendada pela
[documentação oficial da Tenable](https://developer.tenable.com/docs/vm-and-was-integrations)
para exports de vulnerabilidades. Os chunks podem terminar em paralelo e fora de
ordem; chunks vazios contam como processados, mas não aparecem para download.

A espera possui duas fases independentes: até 30 minutos na fila e até 2 horas em
processamento. Durante o processamento, a aplicação avisa após 30 minutos sem
novos chunks, mas não interrompe o trabalho apenas por esse aviso. O polling começa
em 10 segundos e aumenta gradualmente até 30 segundos quando não há mudança. Os
valores podem ser ajustados por cliente no arquivo de credenciais:

~~~dotenv
TENABLE_EXPORT_POLL_SECONDS=10
TENABLE_EXPORT_MAX_POLL_SECONDS=30
TENABLE_EXPORT_QUEUE_TIMEOUT_SECONDS=1800
TENABLE_EXPORT_PROCESSING_TIMEOUT_SECONDS=7200
TENABLE_EXPORT_STALL_WARNING_SECONDS=1800
~~~

Um export só é cancelado automaticamente quando foi criado pela execução atual e
chegou ao limite sem qualquer progresso remoto ou local. Jobs fornecidos,
preexistentes ou retomados nunca são cancelados automaticamente. `Plugin Output`
continua estritamente opcional e só entra na lista seletiva quando a coluna Output
está habilitada.

### Validação do contrato de ativos v2

O comando inicia um export real, mas inspeciona somente nomes e tipos de campos do primeiro chunk:

```powershell
python -m tenable_reports contract-check-assets `
  --profile .\clients\examples\client-profile.json `
  --env-file .\.env `
  --confirm-live-api
```

### Coleta e normalização da Fase 3

O comando publica ativos e findings sob o mesmo `run_id`, preserva os dois raws e gera `assets.jsonl`, `findings.jsonl`, `quality-issues.jsonl` e um manifesto reconciliado:

```powershell
python -m tenable_reports collect-phase3 `
  --profile .\clients\examples\client-profile.json `
  --env-file .\.env `
  --output-root .\data `
  --confirm-live-api
```

O vínculo usa exclusivamente `finding.asset.uuid == asset.id`. IP e hostname são atributos mutáveis e nunca servem como fallback de identidade. O manifesto contém hashes SHA-256, contagens de rejeições, duplicatas, vínculos e órfãos. Dados reais permanecem em `data/`, ignorado pelo Git.

### Coleta mensal recomendada — Fase 4

Existem dois fluxos deliberadamente separados.

**Automático mensal:** executado no primeiro dia do mês e sempre referente ao mês-calendário anterior completo. Uma execução automática em 1º de agosto seleciona `[01/jul 00:00, 01/ago 00:00)`. Seus artefatos ficam em `data/automatic-monthly/`.

```powershell
python -m tenable_reports preview-period `
  --profile .\clients\examples\client-profile.json `
  --mode automatic

python -m tenable_reports collect-monthly `
  --profile .\clients\examples\client-profile.json `
  --env-file .\.env `
  --output-root .\data `
  --confirm-live-api
```

**Manual/pontual:** por padrão cobre um mês-calendário móvel até o instante da execução. Por exemplo, uma execução em 13 de agosto às 10h cobre 13 de julho às 10h até 13 de agosto às 10h. Seus artefatos ficam em `data/manual/`.

```powershell
python -m tenable_reports collect-manual `
  --profile .\clients\examples\client-profile.json `
  --env-file .\.env `
  --output-root .\data `
  --confirm-live-api
```

O fluxo recomendado para relatórios por TAG fica no cadastro do cliente na interface
web. Ative **Relatórios por TAG**, clique em **Buscar TAGs da Tenable** e escolha, em
cada linha, se deseja **Gerar relatório** e se aquele documento deve incluir o
**Comparativo temporal**. É possível selecionar TAGs de categorias diferentes e
habilitar o comparativo em apenas parte delas.

O relatório geral continua usando todos os ativos e findings do período. A mesma
coleta VM é normalizada uma vez e só então recortada localmente pelos UUIDs de ativos
de cada TAG. Cada documento compara a mesma TAG em dois momentos, nunca uma TAG com
outra. “Rede” é apenas um possível nome de categoria ou valor de TAG.

Os seletores do terminal permanecem disponíveis para perfis legados:

```powershell
python -m tenable_reports collect-manual `
  --profile .\clients\examples\client-profile-all-customizations.json `
  --env-file .\.env `
  --select-tags `
  --confirm-live-api
```

O terminal apresenta as categorias e aceita valores como `1,3-5` ou `todos`. Para
uma execução não interativa legada, repita `--tag` usando o UUID ou
`Categoria: Valor`, ou use `report.network_comparison_tags`. Novas configurações devem usar
`report.tag_reports`, preferencialmente pela interface.

```powershell
python -m tenable_reports collect-monthly `
  --profile .\clients\examples\client-profile-all-customizations.json `
  --tag "Rede: Matriz" `
  --tag "Rede: Filial" `
  --confirm-live-api
```

Uma execução aceita vários valores e categorias. Cada TAG gera um documento próprio;
falha em uma TAG é registrada como alerta e não interrompe os documentos gerais nem
as outras TAGs. As tabelas de ativos terminam com `Exploitable`.

O analista pode substituir o padrão manual por `--days N` ou por um intervalo específico. O fim é exclusivo:

```powershell
# Últimos 10 dias até a execução
python -m tenable_reports collect-manual `
  --profile .\clients\examples\client-profile.json `
  --env-file .\.env `
  --days 10 `
  --confirm-live-api

# Período específico [início, fim)
python -m tenable_reports collect-manual `
  --profile .\clients\examples\client-profile.json `
  --env-file .\.env `
  --start-at 2026-06-01T00:00:00-03:00 `
  --end-at 2026-07-01T00:00:00-03:00 `
  --confirm-live-api
```

Em todos os modos, o fluxo usa duas barreiras: `since` e estados/severidades na API para reduzir volume; depois, o domínio reaplica `[início, fim)` e classifica cada ativo/finding com um motivo auditável.

Um snapshot normalizado já existente também pode ser reprocessado sem chamar a API:

```powershell
python -m tenable_reports build-report-dataset `
  --profile .\clients\examples\client-profile.json `
  --run-id <RUN_ID> `
  --output-root .\data\automatic-monthly `
  --mode automatic
```

O dataset traz não mitigadas, mitigadas, ressurgidas, aging, matriz por sistema operacional, Top 10 de ativos com a coluna final `Exploitable` e Top 5 detalhados VM com hosts e referências. `Output` continua opcional por `--include-output` e só pode ser publicado se tiver sido coletado.

A severidade informativa permanece desativada em todos os perfis conhecidos (`include_info_severity=false`) e não entra nas métricas nem nos rankings.

Quando o perfil declara a capacidade `was`, os mesmos comandos coletam o export
WAS em paralelo lógico ao VM, mas em snapshots e normalizadores próprios. A coleta
geral de VM e WAS continua independente das TAGs selecionadas. Os recortes afetam
somente os datasets efêmeros e os documentos operacionais por TAG.

### Geração do Word mínimo — Fase 5

O template controlado pode ser reconstruído de forma determinística:

```powershell
python -m tenable_reports build-base-template `
  --assets-dir .\templates\corporate\assets `
  --output .\templates\corporate\base-v1.docx
```

O relatório usa somente o perfil e um `report-dataset.json` já materializado; nenhuma API é consultada durante a renderização:

```powershell
python -m tenable_reports generate-base-docx `
  --profile .\clients\examples\client-profile.json `
  --dataset .\data\report-datasets\<cliente>\<run_id>\<periodo>\report-dataset.json `
  --template .\templates\corporate\base-v1.docx `
  --output .\01-relatorio-base-<cliente>-<periodo>.docx
```

`IP Address` e `Asset Name` são publicados conforme o dataset por padrão. Para demonstrações, homologação ou envio a terceiros, `--mask-sensitive` deixa essas duas colunas vazias. `Exploitable` é sempre a última coluna e deve ser subconjunto de `Total`; `Output` não integra esse template.

### Geração fiel do par de relatórios

O comando abaixo usa o mesmo template controlado e o dataset materializado. `--mask-sensitive` deixa vazios IP, hostname, URI, repositório e demais identificadores sensíveis:

```powershell
python -m tenable_reports generate-report-pair `
  --profile .\clients\examples\client-profile-all-customizations.json `
  --dataset .\data\report-datasets\<cliente>\<run_id>\<periodo>\report-dataset.json `
  --template .\templates\corporate\base-v1.docx `
  --base-output .\01-relatorio-base-<cliente>-<periodo>.docx `
  --custom-output .\02-inteligencia-e-customizacoes-<cliente>-<periodo>.docx `
  --mask-sensitive
```

O primeiro DOCX preserva os parágrafos comuns dos quatro exemplos, as três tabelas de controle, os dois Top 5 detalhados, `Exploitable` na última coluna de ativos e as matrizes correntes. Se WAS não tiver dados, permanecem os textos e cabeçalhos originais, sem frases artificiais e sem zeros inventados.

O segundo DOCX recebe comparativos gerais e módulos adicionais somente quando eles
estão habilitados em `report.intelligence_modules` e possuem dados. As análises
mensais gerais continuam nele. A comparação específica de uma TAG fica no relatório
daquela TAG. Comparações históricas nunca usam zero como substituto de um predecessor
ausente.

Os perfis da Fase 7 mantêm `report.base_modules` como núcleo obrigatório e
validam os IDs permitidos em `report.intelligence_modules`. Módulos WAS e Cloud
Security exigem as respectivas capacidades em `scope`; módulos sem dados
compatíveis são omitidos do Word e registrados na saída JSON da CLI. Os exemplos
`client-profile-vm-standard.json` e `client-profile-intelligence-expanded.json`
demonstram dois perfis contrastantes sobre o mesmo dataset.

O inventário dos gráficos e das tabelas customizadas observadas nos quatro modelos está em [`docs/11-catalogo-visual-e-tabelas-customizadas.md`](docs/11-catalogo-visual-e-tabelas-customizadas.md). O conjunto mensal aceita vistas configuráveis, como `Geral` e `Servidores`, sem regras condicionais por nome de cliente.

`Output` só aparece quando `vm_top5_include_output` ou `was_top5_include_output` está habilitado no perfil e o campo foi coletado. A geração falha explicitamente se o perfil pedir essa coluna sem a cobertura correspondente no dataset. O pipeline de apresentação também possui um ponto de integração para traduzir descrição e solução por blocos, preservando a ordem e sem enviar `Plugin Output` ao tradutor.

Para uma validação local sem API e sem dados reais, a fixture abaixo gera exatamente
quatro DOCX em `.tmp/e2e-tag-reports`: geral, customizado, uma TAG com comparativo e
uma TAG sem comparativo. O manifesto também registra a prova de que habilitar TAGs não
alterou o conteúdo dos dois documentos gerais.

```powershell
.\.venv\Scripts\python.exe .\scripts\render_tag_report_fixture.py
```

### Execução completa e multi-cliente - Fase 10

Para um único cliente, `run-client` une coleta, histórico, os dois DOCX e o
manifesto com hashes:

```powershell
.\.venv\Scripts\python.exe -m tenable_reports run-client `
  --mode manual `
  --profile .\clients\examples\client-profile.json `
  --env-file .\.env `
  --output-root .\data `
  --confirm-live-api
```

Para vários clientes, copie `orchestration\clients.example.json` para
`orchestration\clients.json`, crie um perfil e um `.env` por cliente e valide sem
API:

```powershell
.\.venv\Scripts\python.exe -m tenable_reports validate-orchestration `
  --config .\orchestration\clients.json

.\.venv\Scripts\python.exe -m tenable_reports orchestrate `
  --config .\orchestration\clients.json `
  --mode manual `
  --days 10 `
  --dry-run
```

Depois do `dry-run`, remova essa opção e acrescente `--confirm-live-api`. O script
`.\scripts\run_manual_orchestration.ps1` oferece o mesmo fluxo pontual; o script
`.\scripts\run_monthly_orchestration.ps1` executa o mês anterior completo. A
instalação opcional da tarefa do Windows fica em
`.\scripts\install_monthly_task.ps1` e só ocorre quando o usuário a invoca.

Cada cliente roda em um processo separado. Uma falha não interrompe os demais;
logs JSONL e o manifesto global ficam sob `data\<modo>\orchestration\`. Credenciais
nunca entram no JSON da carteira: cada entrada referencia seu próprio arquivo em
`credentials\`.

### Armazenamento e reciclagem

As coletas VM e WEB e os arquivos normalizados são gravados compactados durante a
execução. Depois que todos os DOCX solicitados passam pela validação, o manifesto é
registrado e o snapshot histórico é confirmado no PostgreSQL, as pastas pesadas
`raw`, `snapshots`, `normalized` e `report-datasets` — inclusive os datasets por TAG
— são removidas automaticamente. Permanecem os DOCX publicados e o histórico
compacto geral e por UUID de TAG necessário para comparar os próximos meses.

Uma execução que falhou preserva o staging compactado por sete dias, permitindo
diagnóstico e nova tentativa; depois desse prazo ele pode aparecer como elegível na
limpeza. Os documentos não entram em retenção automática e só são excluídos por uma
ação explícita do analista. Como os dados brutos são reciclados após o sucesso, um
relatório antigo não pode ser totalmente regenerado sem uma nova coleta na API.

Na interface, o quadro **Armazenamento** mostra espaço livre, temporários e
pendências. **Revisar limpeza** primeiro apresenta uma prévia com quantidade e
tamanho; somente a confirmação seguinte remove os candidatos que passaram pelas
proteções do PostgreSQL. Os padrões ficam em `orchestration/clients.json`:
`cleanup_after_publish=true`, `failed_staging_days=7` e `logs_days=90`.

O painel local iniciado por `.\scripts\run_web.ps1` permite acompanhar a fila e o
progresso de cada TAG, testar APIs, editar clientes, buscar TAGs disponíveis, consultar
documentos agrupados em **Geral**, **Customizado** e **Por TAG**, definir
a geração `MAIN`, excluir/restaurar logicamente relatórios e repetir trabalhos com
falha. A área **Admin** analisa e aplica o backfill seguro das referências históricas,
sem substituir um `MAIN` existente; ambiguidades continuam sob decisão do analista.
A opção `presentation.show_source_filters` também pode ser controlada no cadastro do
cliente. Quando habilitada, cada tabela de dados dos relatórios base e customizado
recebe logo abaixo uma nota discreta de **Validação rápida na Tenable**, com a tela,
os filtros reproduzíveis e a regra local mínima de contagem/ranking. Tabelas de
controle do documento não recebem a nota. O indicador geral `Exploitable` usa
exclusivamente `plugin.exploit_available`; a matriz por framework permanece
segregada pelos flags individuais de cada framework.

O fluxo recomendado está na interface: **Admin → Analisar → Aplicar promoções
seguras**. A linha de comando abaixo permanece disponível apenas como alternativa de
manutenção para planejar a referência histórica inicial sem mutação:

```powershell
.\.venv\Scripts\python.exe -m tenable_reports backfill-report-main `
  --database-env-file .\credentials\database.env `
  --dry-run
```

Somente depois de revisar ambiguidades, use `--apply`. O comando não altera nem
exclui os documentos existentes.

## Limites atuais

A coleta WAS em produção, o histórico PostgreSQL, a orquestração, a interface local
e os lançadores de agendamento já estão integrados. Ainda faltam a coleta Cloud
Security, o provedor efetivo de tradução e canais externos de distribuição. O export WAS
não fornece uma métrica global CES/AES; portanto, o relatório preserva os textos
editoriais e publica somente métricas WEB comprovadas, sem fabricar um indicador
substituto.
