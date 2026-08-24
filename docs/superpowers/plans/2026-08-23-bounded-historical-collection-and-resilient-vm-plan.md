# Coleta Histórica Delimitada e Resiliência VM Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduzir coletas desnecessárias e exports presos, permitir reconstrução histórica delimitada com transparência e preservar snapshots compactos capazes de regenerar os relatórios.

**Architecture:** Um roteador escolhe entre replay do snapshot, export VM legado e reconstrução híbrida pela Inventory API. Todas as rotas convergem para o modelo normalizado existente; um catálogo PostgreSQL fornece metadados de plugins e o snapshot compacto substitui a retenção de arquivos brutos.

**Tech Stack:** Python 3.14, biblioteca padrão HTTP/JSON/gzip, PostgreSQL, pytest/unittest, HTML/CSS/JavaScript sem framework e geração DOCX existente.

**Spec:** `docs/superpowers/specs/2026-08-23-bounded-historical-collection-design.md`

## Restrições globais

- Preservar os textos, tabelas e regras já existentes nos relatórios padrão e customizado.
- Manter `/vulns/export` como padrão até cada cliente optar pelo piloto da Inventory API.
- Não ativar `properties` seletivas; os testes deste tenant mostraram pior desempenho.
- Nunca chamar API real em testes automatizados.
- Nunca cancelar automaticamente um UUID reutilizado ou criado por outra execução.
- Não apagar brutos antes da confirmação transacional do snapshot compacto e da publicação dos DOCX.
- Implementar cada tarefa com teste falhando primeiro, alteração mínima e suíte focada antes da suíte completa.

## Task 1: Modelar a decisão da fonte de coleta

**Files:**
- Create: `src/tenable_reports/application/collection_routing.py`
- Create: `tests/test_collection_routing.py`
- Modify: `src/tenable_reports/application/__init__.py`

- [ ] Escrever testes para as quatro rotas: mensal automático, janela terminando agora, snapshot existente e histórico encerrado sem snapshot.
- [ ] Confirmar que o teste falha porque o roteador ainda não existe:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_collection_routing.py -q
```

- [ ] Implementar `CollectionSource`, `CollectionRoute` e uma função pura:

```python
def select_collection_route(
    *,
    period: ReportPeriod,
    now: datetime,
    execution_mode: str,
    snapshot_available: bool,
    historical_source: str,
    fallback_policy: str,
) -> CollectionRoute:
    ...
```

- [ ] Registrar no resultado se a coleta é `authoritative_snapshot`, `current_window` ou `historical_reconstruction`, incluindo avisos obrigatórios.
- [ ] Rodar os testes focados e garantir que nenhuma decisão dependa do relógio global.
- [ ] Commit sugerido: `feat: add deterministic collection source routing`

## Task 2: Adicionar configuração segura por cliente

**Files:**
- Modify: `src/tenable_reports/config/profile.py`
- Modify: `src/tenable_reports/config/environment.py`
- Modify: `tests/test_profile_environment.py`
- Modify: `clients/example.json`

- [ ] Escrever testes para os padrões `historical_source=legacy` e `historical_fallback=warn_legacy`.
- [ ] Escrever testes para aceitar `inventory_beta` e rejeitar valores desconhecidos.
- [ ] Adicionar ao `VmExportConfig`:

```python
historical_source: str = "legacy"
historical_fallback: str = "warn_legacy"
manual_no_progress_seconds: int = 900
automatic_no_progress_seconds: int = 1800
```

- [ ] Permitir override por ambiente/CLI sem gravar segredos no perfil.
- [ ] Garantir retrocompatibilidade com todos os JSON atuais.
- [ ] Rodar:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_profile_environment.py -q
```

- [ ] Commit sugerido: `feat: configure historical collection and stall limits`

## Task 3: Criar o cliente da Inventory Findings API

**Files:**
- Create: `src/tenable_reports/infrastructure/tenable_inventory/__init__.py`
- Create: `src/tenable_reports/infrastructure/tenable_inventory/client.py`
- Create: `tests/test_inventory_client.py`
- Create: `tests/fixtures/tenable_inventory/properties.json`
- Create: `tests/fixtures/tenable_inventory/findings_page_1.json`

- [ ] Criar testes com transporte falso para descoberta de propriedades, paginação, `Retry-After`, 401/403, 429 e 5xx.
- [ ] Testar o filtro delimitado sem fazer chamada real:

```python
{
    "field": "last_observed_at",
    "operator": "between",
    "value": ["2026-07-01", "2026-07-31"],
}
```

- [ ] Implementar `InventoryFindingsClient` reutilizando autenticação, timeout e política de retry já adotados para Tenable.
- [ ] Validar a capacidade do tenant com `GET /api/v1/t1/inventory/findings/properties` antes de escolher a rota beta.
- [ ] Implementar paginação determinística, limite máximo por página e detecção de resposta inconsistente.
- [ ] Rodar:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_inventory_client.py -q
```

- [ ] Commit sugerido: `feat: add bounded inventory findings client`

## Task 4: Normalizar findings da Inventory API

**Files:**
- Create: `src/tenable_reports/domain/inventory_normalization.py`
- Create: `tests/test_inventory_normalization.py`
- Modify: `src/tenable_reports/domain/models.py`
- Modify: `src/tenable_reports/domain/normalization.py`

- [ ] Criar fixtures e testes para ACTIVE, RESURFACED, severidades, ativos, IPs, portas, datas, CVEs, CVSS, VPR, descrição, solução e output.
- [ ] Mapear `ACTIVE -> OPEN` e `RESURFACED -> REOPENED`.
- [ ] Criar identidade estável da origem usando `finding_detection_id` e um fingerprint de ativo, nome, porta e protocolo; não fingir que esse identificador é o plugin ID legado.
- [ ] Preservar campos ausentes como `None` e emitir problemas de qualidade estruturados.
- [ ] Fazer ambas as fontes produzirem o mesmo contrato normalizado usado pelos cálculos atuais.
- [ ] Rodar:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_inventory_normalization.py tests\test_normalization.py -q
```

- [ ] Commit sugerido: `feat: normalize inventory findings into report model`

## Task 5: Implementar o catálogo compacto de plugins

**Files:**
- Create: `src/tenable_reports/infrastructure/postgresql_migrations/0005_plugin_catalog.sql`
- Modify: `src/tenable_reports/infrastructure/postgresql.py`
- Create: `src/tenable_reports/application/plugin_catalog.py`
- Modify: `src/tenable_reports/application/collect.py`
- Create: `tests/test_plugin_catalog.py`
- Modify: `tests/test_postgresql.py`

- [ ] Criar testes de migração, upsert idempotente e isolamento por cliente/tenant.
- [ ] Persistir plugin ID, nome normalizado, família, sinopse, descrição, solução, referências, CVSS/VPR, exploitabilidade/frameworks e proveniência.
- [ ] Alimentar o catálogo após cada chunk legado validado e antes de remover dados brutos.
- [ ] Enriquecer findings Inventory somente quando a associação for unívoca.
- [ ] Para nome ambíguo ou ausente, manter o campo vazio e registrar `PLUGIN_METADATA_AMBIGUOUS` ou `PLUGIN_METADATA_MISSING`.
- [ ] Limitar qualquer consulta de detalhe de plugin a IDs numéricos já resolvidos e apenas aos candidatos efetivamente usados no relatório.
- [ ] Rodar:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_plugin_catalog.py tests\test_postgresql.py -q
```

- [ ] Commit sugerido: `feat: persist and apply compact plugin catalog`

## Task 6: Coletar histórico por rota híbrida

**Files:**
- Create: `src/tenable_reports/application/collect_inventory.py`
- Modify: `src/tenable_reports/application/collect.py`
- Modify: `src/tenable_reports/application/vm_export_policy.py`
- Create: `tests/test_inventory_collection.py`
- Modify: `tests/test_collection.py`

- [ ] Testar ACTIVE/RESURFACED com limite inferior e superior na origem.
- [ ] Testar FIXED em segmento separado pelo export legado, com `last_fixed` e recorte superior local.
- [ ] Testar indisponibilidade beta com políticas `fail` e `warn_legacy`.
- [ ] Persistir páginas temporárias comprimidas, checksum, contagem e proveniência para permitir retomada segura durante a execução.
- [ ] Produzir um único `CollectionBundle` com as fontes e avisos usados, sem duplicar findings.
- [ ] Emitir eventos de progresso distintos para `inventory_active`, `inventory_resurfaced` e `legacy_fixed`.
- [ ] Rodar:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_inventory_collection.py tests\test_collection.py tests\test_vm_export_policy.py -q
```

- [ ] Commit sugerido: `feat: add bounded hybrid historical collection`

## Task 7: Persistir e reproduzir snapshots compactos

**Files:**
- Create: `src/tenable_reports/infrastructure/postgresql_migrations/0006_compact_finding_snapshots.sql`
- Modify: `src/tenable_reports/infrastructure/postgresql.py`
- Modify: `src/tenable_reports/application/history.py`
- Modify: `src/tenable_reports/application/retention.py`
- Create: `tests/test_compact_snapshots.py`
- Modify: `tests/test_history.py`
- Modify: `tests/test_retention.py`

- [ ] Escrever teste de ida e volta: dataset normalizado -> snapshot -> dataset equivalente.
- [ ] Guardar fatos suficientes para tabelas, Top 5, hosts, tags e comparativos, sem copiar os JSON brutos completos.
- [ ] Indexar por cliente, início, fim, modo e versão do schema.
- [ ] Fazer o roteador preferir snapshot exato antes de iniciar qualquer export.
- [ ] Persistir snapshot e referência aos DOCX na mesma unidade transacional possível.
- [ ] Bloquear a limpeza de brutos se snapshot ou publicação falhar.
- [ ] Rodar:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_compact_snapshots.py tests\test_history.py tests\test_retention.py -q
```

- [ ] Commit sugerido: `feat: store replayable compact monthly snapshots`

## Task 8: Integrar roteamento ao CLI e à orquestração

**Files:**
- Modify: `src/tenable_reports/cli.py`
- Modify: `src/tenable_reports/application/orchestration.py`
- Modify: `src/tenable_reports/application/report_dataset.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_orchestration.py`
- Modify: `tests/test_report_dataset.py`

- [ ] Testar que perfis antigos continuam usando o export legado.
- [ ] Testar que snapshot exato elimina chamadas externas.
- [ ] Testar que período histórico beta registra origem, filtros, versão e aviso no manifesto.
- [ ] Adicionar override operacional `--historical-source legacy|inventory-beta`, sem alterar o perfil salvo.
- [ ] Propagar `collection_route`, `reconstruction_status`, fontes e problemas de qualidade ao dataset e ao registro do relatório.
- [ ] Classificar falhas transitórias como `TENABLE_TEMPORARY`, mantendo mensagem concreta em vez de “falha operacional sem detalhes”.
- [ ] Rodar:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_cli.py tests\test_orchestration.py tests\test_report_dataset.py -q
```

- [ ] Commit sugerido: `feat: route report runs through snapshots and bounded sources`

## Task 9: Melhorar detecção e recuperação de export sem progresso

**Files:**
- Modify: `src/tenable_reports/infrastructure/tenable_vm/client.py`
- Modify: `src/tenable_reports/application/vm_export_policy.py`
- Modify: `tests/test_vm_client.py`
- Modify: `tests/test_vm_export_policy.py`

- [ ] Testar separadamente timeout total e timeout desde o último progresso.
- [ ] Considerar progresso apenas quando estado/chunks realmente avançarem; `total_chunks=1` sozinho não significa conclusão.
- [ ] Aplicar 15 minutos sem progresso para manual e valor configurável maior para automático.
- [ ] Cancelar automaticamente somente export `origin=created` da execução atual; nunca cancelar `reused`.
- [ ] Preservar UUID, filtros, chunks, último progresso e resultado do cancelamento no erro estruturado.
- [ ] Implementar nova tentativa somente por política explícita; na primeira recuperação, alternar `combined` para `split` para isolar ACTIVE/REOPENED de FIXED.
- [ ] Rodar:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_vm_client.py tests\test_vm_export_policy.py -q
```

- [ ] Commit sugerido: `fix: stop and diagnose VM exports with no progress`

## Task 10: Expor controles e transparência na interface web

**Files:**
- Modify: `src/tenable_reports/webapp/server.py`
- Modify: `src/tenable_reports/webapp/static/index.html`
- Modify: `src/tenable_reports/webapp/static/app.js`
- Modify: `src/tenable_reports/webapp/static/styles.css`
- Modify: `tests/test_webapp.py`

- [ ] Testar API web para salvar a opção beta por cliente sem expor credenciais.
- [ ] Testar o aviso para período histórico sem snapshot e a confirmação do analista.
- [ ] Exibir rota, UUID, origem, chunks e tempo desde o último progresso.
- [ ] Exibir selo `RECONSTRUÍDO` no histórico e uma explicação curta da limitação temporal.
- [ ] Implementar “Cancelar export e tentar novamente” com confirmação contendo cliente e UUID.
- [ ] Impedir que a ação cancele UUID diferente do export ativo daquele cliente.
- [ ] Validar desktop e largura reduzida, além dos testes:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_webapp.py -q
```

- [ ] Commit sugerido: `feat: expose historical source and export recovery in web UI`

## Task 11: Validar equivalência e fazer rollout controlado

**Files:**
- Create: `scripts/validate_collection_equivalence.py`
- Create: `tests/test_collection_equivalence.py`
- Modify: `README.md`
- Modify: `DESIGN.md`
- Modify: `AGENTS.md`

- [ ] Criar comparador offline de snapshots, sem credenciais, para contagens por estado/severidade, ativos, Top 5, duplicidades e cobertura de metadados.
- [ ] Testar o comparador com fixtures contendo igualdade, diferença legítima de estado e enriquecimento ambíguo.
- [ ] Executar toda a suíte:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

- [ ] Fazer homologação ao vivo, sequencial e explicitamente autorizada, em pelo menos três clientes e três períodos.
- [ ] Exigir igualdade para ACTIVE/RESURFACED e investigar toda divergência FIXED antes de promover essa fonte.
- [ ] Confirmar que DOCX padrão, customizado e por TAG permanecem visual e numericamente equivalentes.
- [ ] Medir duração, bytes recebidos, páginas/chunks, uso em disco e tempo sem progresso por rota.
- [ ] Manter rollback imediato por perfil para `historical_source=legacy`.
- [ ] Atualizar documentação operacional, diagnóstico e recuperação pela interface.
- [ ] Commit sugerido: `docs: document bounded collection rollout and recovery`

## Ordem de liberação

1. Tasks 1–4 atrás de feature flag, sem mudar o padrão.
2. Tasks 5–7 para garantir enriquecimento e regeneração antes da limpeza.
3. Tasks 8–10 para integrar e tornar o comportamento visível ao analista.
4. Task 11 em homologação; só depois habilitar `inventory_beta` por cliente.
5. Manter FIXED na rota legada até a matriz de equivalência aprovar sua migração.

## Evidência operacional que motivou o plano

- A execução manual de 30 dias em 2026-08-23 criou o export `d9671217-4efc-4459-9653-38a4be27fcf9`.
- O export ficou 15 minutos em `PROCESSING`, com `total_chunks=0` e `completed_chunks=0`.
- O cancelamento foi aplicado somente a esse UUID e confirmado como `CANCELLED`.
- A execução terminou como falha transitória e não publicou relatório parcial.

Essa evidência será usada como caso de aceitação para a Task 9, sem reutilizar os dados dessa tentativa.
