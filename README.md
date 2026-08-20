# Relatórios Tenable

Discovery e arquitetura de uma solução evolutiva para automatizar relatórios técnicos da Tenable, com separação entre coleta, normalização, processamento, histórico, configuração de clientes, apresentação e orquestração.

## Estado atual

A fase de análise foi concluída para os scripts existentes, a documentação oficial e quatro relatórios DOCX representativos. A geração Word produz dois arquivos: um relatório-base fiel ao texto e às tabelas comuns dos modelos e um segundo DOCX de inteligência/customizações habilitadas por perfil.

- [Análise e arquitetura da solução](docs/01-analise-e-arquitetura.md)
- [Catálogo das APIs Tenable](docs/02-catalogo-apis-tenable.md)
- [Protocolo de análise dos DOCX](docs/03-protocolo-analise-docx.md)
- [Matriz comparativa e contrato dos dois DOCX](docs/04-matriz-e-contrato-dos-relatorios.md)
- [Histórico, regras críticas e tradução](docs/05-historico-regras-criticas-e-traducao.md)
- [Contrato do modelo normalizado da Fase 3](docs/06-modelo-normalizado-fase3.md)
- [Contrato e validação do dataset mensal da Fase 4](docs/07-dataset-mensal-fase4.md)
- [Template Word mínimo e prova da Fase 5](docs/08-template-word-fase5.md)
- [Primeiro relatório-base completo da Fase 6](docs/09-relatorio-base-completo-fase6.md)
- [Perfis declarativos e variações da Fase 7](docs/12-perfis-e-variacoes-fase7.md)
- [Coleta e relatório Web App Scanning da Fase 8](docs/13-was-fase8.md)
- [Histórico e tendências da Fase 9](docs/14-historico-e-tendencias-fase9.md)
- [Orquestração e distribuição controlada da Fase 10](docs/15-orquestracao-e-distribuicao-fase10.md)
- [PostgreSQL: migração e operação](docs/16-postgresql-migracao-e-operacao.md)
- [Interface web local — MVP](docs/17-interface-web-mvp.md)

## Decisão central

Cada execução publicará dois documentos gerados a partir do mesmo snapshot imutável:

1. `01-relatorio-base-<cliente>-<periodo>.docx`: núcleo estável e comum, incluindo o Top 5 detalhado de vulnerabilidades VM não mitigadas com seus hosts e, por decisão de produto, o Top 5 detalhado de vulnerabilidades WEB com suas instâncias/URIs.
2. `02-inteligencia-e-customizacoes-<cliente>-<periodo>.docx`: união modular das análises adicionais encontradas nos clientes, ativadas por perfil/capacidade e com comparativos somente quando houver snapshot anterior compatível.

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

O tenant validado aceita sintaticamente `properties`, mas os jobs seletivos testados terminaram com todos os chunks falhos. Assim, a coleta usa o payload completo por padrão, ainda com `include_plugin_output=false`. `--select-properties` permanece experimental e não deve ser usado em publicação sem novo teste de contrato.

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

Para gerar também o comparativo dos principais ativos vulneráveis por rede, o analista
pode listar e selecionar vários valores de uma categoria de tag antes da coleta:

```powershell
python -m tenable_reports collect-manual `
  --profile .\clients\examples\client-profile-all-customizations.json `
  --env-file .\.env `
  --select-tags `
  --confirm-live-api
```

O terminal primeiro apresenta as categorias e depois aceita valores como `1,3-5` ou
`todos`. Para uma execução agendada e não interativa, repita `--tag` usando o UUID ou
`Categoria: Valor`, ou grave os mesmos seletores em
`report.network_comparison_tags` no perfil:

```powershell
python -m tenable_reports collect-monthly `
  --profile .\clients\examples\client-profile-all-customizations.json `
  --tag "Rede: Matriz" `
  --tag "Rede: Filial" `
  --confirm-live-api
```

Uma execução aceita vários valores da mesma categoria. A seleção não filtra o
relatório-base nem suas métricas: ela serve somente para criar os snapshots das redes
usados no segundo DOCX. Cada rede selecionada é comparada com ela mesma no período
anterior (`Matriz atual × Matriz anterior`, por exemplo), nunca com outra rede. Cada
tabela termina com `Exploitable`.

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
geral de VM e WAS continua independente das tags selecionadas para o comparativo por
rede; essas tags afetam somente os snapshots customizados de rede.

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

O segundo DOCX recebe comparativos e módulos adicionais somente quando eles estão habilitados em `report.intelligence_modules` e possuem dados. Comparações históricas nunca usam zero como substituto de um predecessor ausente. Quando `vm_network_comparison` está habilitado, o dataset guarda um snapshot corrente para cada tag selecionada; o bloco só é publicado depois de parear esse snapshot com o período anterior da mesma tag.

Os perfis da Fase 7 mantêm `report.base_modules` como núcleo obrigatório e
validam os IDs permitidos em `report.intelligence_modules`. Módulos WAS e Cloud
Security exigem as respectivas capacidades em `scope`; módulos sem dados
compatíveis são omitidos do Word e registrados na saída JSON da CLI. Os exemplos
`client-profile-vm-standard.json` e `client-profile-intelligence-expanded.json`
demonstram dois perfis contrastantes sobre o mesmo dataset.

O inventário dos gráficos e das tabelas customizadas observadas nos quatro modelos está em [`docs/11-catalogo-visual-e-tabelas-customizadas.md`](docs/11-catalogo-visual-e-tabelas-customizadas.md). O conjunto mensal aceita vistas configuráveis, como `Geral` e `Servidores`, sem regras condicionais por nome de cliente.

`Output` só aparece quando `vm_top5_include_output` ou `was_top5_include_output` está habilitado no perfil e o campo foi coletado. A geração falha explicitamente se o perfil pedir essa coluna sem a cobertura correspondente no dataset. O pipeline de apresentação também possui um ponto de integração para traduzir descrição e solução por blocos, preservando a ordem e sem enviar `Plugin Output` ao tradutor.

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
execução. Depois que os dois DOCX passam pela validação e o snapshot histórico é
confirmado no PostgreSQL, as pastas pesadas `raw`, `snapshots`, `normalized` e
`report-datasets` daquele `run_id` são removidas automaticamente. Permanecem os
DOCX publicados e o histórico compacto necessário para comparar os próximos meses.

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

O painel local iniciado por `.\scripts\run_web.ps1` permite acompanhar a fila,
testar APIs, editar clientes, consultar documentos agrupados por execução, definir
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
