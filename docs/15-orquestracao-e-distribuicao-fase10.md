# Fase 10 - Orquestração e distribuição controlada

## Resultado

A Fase 10 encerra a linha de comando operacional do produto. Ela não altera
métricas, janelas, textos editoriais ou o papel das tags: coordena os componentes já
validados nas fases anteriores.

Uma execução completa por cliente realiza, em ordem:

1. coleta geral de ativos e findings VM, e WAS quando habilitado;
2. snapshot separado para cada tag de comparativo, sem filtrar a coleta geral;
3. normalização e dataset do período;
4. preparação histórica pelo `main` anterior no PostgreSQL;
5. geração do relatório-base e do documento de inteligência/customizações;
6. validação estrutural dos dois pacotes DOCX;
7. manifesto de publicação com tamanho e SHA-256 de cada arquivo;
8. registro imutável da execução e promoção automática apenas quando a competência
   ainda não possui `main`.

## Comandos operacionais

### Um cliente

`run-client` executa o fluxo completo. `automatic` usa o mês-calendário anterior;
`manual` usa um mês móvel por padrão e aceita `--days` ou `[--start-at, --end-at)`.

```powershell
.\.venv\Scripts\python.exe -m tenable_reports run-client `
  --mode manual `
  --profile .\clients\cliente-a.json `
  --env-file .\credentials\cliente-a.env `
  --output-root .\data `
  --confirm-live-api
```

### Vários clientes

`orchestrate` lê um arquivo JSON, inicia um processo Python por cliente e limita a
concorrência. Essa separação impede contaminação entre arquivos `.env`, tenants,
falhas e configurações de tags.

```powershell
.\.venv\Scripts\python.exe -m tenable_reports orchestrate `
  --config .\orchestration\clients.json `
  --mode manual `
  --confirm-live-api
```

Use `--dry-run` para validar os comandos sem chamar a API. Use `--client` mais de uma
vez para executar somente um subconjunto da carteira.

## Contrato do arquivo de orquestração

O exemplo versionado é `orchestration/clients.example.json`. Ele possui:

- `orchestration_id`: nome estável da carteira;
- `defaults.output_root`: raiz comum de armazenamento;
- `defaults.max_parallel`: no máximo oito; o exemplo usa dois;
- `clients[].client_id`: deve coincidir com o `client_id` do perfil;
- `clients[].profile`: perfil declarativo do cliente;
- `clients[].env_file`: arquivo local de credenciais;
- `clients[].tags`: seletores não interativos para o comparativo temporal por rede;
- `clients[].enabled`: permite suspender um cliente sem removê-lo.

Chaves, tokens, senhas e segredos embutidos no JSON são rejeitados. Cada cliente deve
possuir seu próprio arquivo em `credentials/`; arquivos terminados em `.env` ficam
ignorados pelo Git.

## Armazenamento e rastreabilidade

Os dois modos permanecem fisicamente separados:

```text
data/
  automatic-monthly/
  manual/
```

Dentro de cada modo, cada cliente recebe raws, snapshots, normalizados, datasets,
histórico e relatórios por `run_id`. A orquestração grava ainda:

```text
orchestration/<orchestration_id>/<run_id>/
  orchestration-manifest.json
  notifications.jsonl
  clients/<client_id>.jsonl
```

Falha de um cliente não cancela os demais. O status global será
`PARTIAL_FAILURE`, e o processo retorna código diferente de zero depois que os outros
clientes terminarem.

## Publicação e notificações

`publication-manifest.json` marca os dois documentos como
`READY_FOR_CONTROLLED_DISTRIBUTION`, registra seus hashes e declara que nenhuma
entrega externa foi realizada. `notifications.jsonl` é uma caixa de eventos local
para futura integração com e-mail, Teams, Slack ou uma aplicação.

Nenhuma mensagem externa é enviada sem integração e autorização explícitas.

## Retenção

A retenção em camadas é aplicada depois da orquestração mensal, com horizontes
independentes para raws com falha, raws concluídos, snapshots/normalizados e
documentos. Use `--no-apply-retention` para suspender a remoção durante manutenção.

Runs ativos, necessários para retry ou sem histórico confirmado são protegidos. Os
DOCX e datasets de uma referência `main` também não podem ser removidos. A interface
web mostra o plano e exige confirmação antes da limpeza manual; o navegador nunca
informa caminhos arbitrários ao servidor.

Somente diretórios de run sob `raw`, `snapshots`, `normalized`, `report-datasets` e
`reports` na raiz do modo atual são candidatos. Banco PostgreSQL, manifests da
orquestração e arquivos fora dessa estrutura não são alvos.

## Retentativas

No agendamento mensal, falhas transitórias classificadas, como limite temporário ou
indisponibilidade da API, respeitam `retry_max_attempts` e
`retry_delay_seconds`. Credenciais inválidas e outras falhas permanentes não entram
em repetição automática. Uma retentativa iniciada pela interface conserva cliente,
modo e período originais e registra o vínculo com o job anterior.

## Agendamento no Windows

O script `scripts/run_monthly_orchestration.ps1` executa o modo automático. O
instalador `scripts/install_monthly_task.ps1` cria, quando invocado deliberadamente,
uma tarefa mensal no dia 1. Exemplo:

```powershell
.\scripts\install_monthly_task.ps1 `
  -Config .\orchestration\clients.json `
  -Time "06:00"
```

O agendamento deve rodar com uma conta que tenha acesso de leitura ao projeto e aos
arquivos `.env`, e escrita em `data/`.

## Limites mantidos

- `Plugin Output` continua opt-in e precisa ser compatível com o perfil.
- Tags afetam apenas o comparativo da mesma rede entre dois períodos.
- A coleta Cloud Security, o tradutor aprovado e os canais externos de distribuição
  continuam dependências independentes; a orquestração registra indisponibilidades,
  mas não fabrica dados.
