# Relatórios Operacionais por TAG Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gerar relatórios operacionais compactos por TAG, com comparação temporal opcional dentro de cada documento, a partir de uma única coleta geral e com gerenciamento completo pela interface web.

**Architecture:** A coleta VM continua geral. As associações de ativos por TAG são obtidas separadamente, cruzadas com os dados normalizados e transformadas em datasets segmentados efêmeros; o snapshot histórico principal guarda somente resumos compactos por UUID de TAG. Um renderer DOCX específico produz os documentos por TAG, enquanto o manifesto e o PostgreSQL registram metadados que permitem agrupá-los na interface.

**Tech Stack:** Python 3.11+, `python-docx`, Pillow, PostgreSQL/`psycopg`, servidor HTTP e frontend JavaScript existentes, `unittest`/`pytest`, LibreOffice para QA visual.

**Spec:** `docs/superpowers/specs/2026-08-21-relatorios-operacionais-por-tag-design.md`

## Global Constraints

- A coleta VM principal permanece geral e nunca recebe filtro `tag.*`.
- Uma única exportação geral de ativos e uma única exportação geral de findings alimentam todos os documentos do cliente.
- Cada TAG é identificada pelo UUID e pode pertencer a qualquer categoria; TAGs diferentes nunca são combinadas semanticamente.
- `include_temporal_comparison=true` exige `generate_report=true`.
- Comparações usam somente a mesma TAG, snapshots `main` compatíveis e meses de janeiro até o mês analisado no mesmo ano civil.
- Meses ausentes permanecem indisponíveis; nunca são transformados em zero.
- O relatório por TAG contém somente VM, não WAS nem Cloud Security.
- A tabela de hosts contém Asset Name, IP, porta e protocolo; `Output` continua opcional e desligado por padrão.
- Textos do documento vêm do catálogo editorial existente; não adicionar parágrafos com estilo generativo.
- O relatório-base e as análises mensais gerais do customizado não podem mudar quando TAGs forem habilitadas.
- Configurações e snapshots com nomes legados de “rede” continuam legíveis.
- Datasets segmentados são efêmeros; somente DOCX e histórico compacto permanecem após a limpeza segura.
- Nomes de arquivo devem ser válidos no Windows e colisões recebem um fragmento do UUID.

## File Structure

### Novos arquivos

- `src/tenable_reports/application/tag_report_dataset.py` — lê os normalizados uma vez, cria um dataset efêmero para cada escopo de TAG e devolve warnings isolados.
- `src/tenable_reports/presentation/tag_report_docx.py` — monta o relatório operacional compacto por TAG.
- `src/tenable_reports/presentation/monthly_visuals.py` — concentra tabelas e imagens mensais reutilizadas pelo customizado e pelos relatórios por TAG.
- `src/tenable_reports/infrastructure/postgresql_migrations/0004_tag_report_documents.sql` — adiciona metadados opcionais de tipo e TAG aos documentos publicados.
- `tests/test_tag_report_dataset.py` — garante isolamento dos recortes e invariância do dataset geral.
- `tests/test_tag_report_docx.py` — valida estrutura, conteúdo, ausência de dados e comparativo do DOCX por TAG.

### Arquivos modificados

- `src/tenable_reports/config/profile.py` — contrato novo de configuração e leitura legada.
- `src/tenable_reports/application/tag_scope.py` — múltiplas categorias, snapshot v2 e falhas por TAG.
- `src/tenable_reports/domain/history.py` — snapshots genéricos por TAG e séries anuais.
- `src/tenable_reports/application/history.py` — enriquecimento dos datasets por TAG usando referências `main`.
- `src/tenable_reports/infrastructure/postgresql.py` — serialização histórica compatível e metadados dos documentos.
- `src/tenable_reports/presentation/report_filenames.py` — nome seguro e único do relatório por TAG.
- `src/tenable_reports/presentation/customizations_report_docx.py` — usa visuais compartilhados e deixa de renderizar comparação específica de TAG.
- `src/tenable_reports/application/publishing.py` — documentos tipados no manifesto.
- `src/tenable_reports/cli.py` — integra coleta, datasets, histórico, geração e warnings.
- `src/tenable_reports/application/orchestration.py` — preserva progresso e warnings por TAG no resultado do cliente.
- `src/tenable_reports/webapp/server.py` — API de descoberta, persistência da configuração e consulta dos metadados publicados.
- `src/tenable_reports/webapp/static/index.html` — controles de TAG no cadastro do cliente.
- `src/tenable_reports/webapp/static/app.js` — busca, seleção, salvamento e agrupamento dos documentos.
- `src/tenable_reports/webapp/static/app.css` — layout compacto, estados e responsividade.
- `tests/test_profile_environment.py`, `tests/test_tag_scope.py`, `tests/test_history.py`, `tests/test_postgresql.py`, `tests/test_report_filenames.py`, `tests/test_customizations_report_docx.py`, `tests/test_cli.py`, `tests/test_orchestration.py`, `tests/test_webapp.py` — regressão e novos contratos.
- `orchestration/clients.example.json`, `README.md`, `docs/10-escopo-tags-e-comparativo-por-rede.md`, `docs/11-catalogo-visual-e-tabelas-customizadas.md`, `docs/17-interface-web-mvp.md` — exemplos e operação.

---

### Task 1: Configuração genérica de relatórios por TAG

**Files:**
- Modify: `src/tenable_reports/config/profile.py:12-280`
- Modify: `tests/test_profile_environment.py`
- Modify: `orchestration/clients.example.json`

**Interfaces:**
- Produces: `TagReportSelection(tag_uuid, category_uuid, category_name, value, generate_report, include_temporal_comparison)`.
- Produces: `TagReportsConfig(enabled, tags)` em `ClientProfile.report.tag_reports`.
- Preserves: `ClientProfile.report.network_comparison_tags` como entrada legada somente para leitura.
- Preserves: a propriedade `ReportConfig.network_comparison_tags` devolve `legacy_network_comparison_tags`, evitando quebrar CLI e perfis durante as Tasks 1-8.

- [ ] **Step 1: Escrever testes que definem o novo contrato e a compatibilidade legada**

```python
def test_profile_accepts_tag_reports_from_different_categories():
    data = {
        "schema_version": 1, "client_id": "client-001",
        "display_name": "Cliente", "tenant_id": "tenant",
        "report": {},
    }
    data["report"]["tag_reports"] = {
        "enabled": True,
        "tags": [
            {"tag_uuid": "tag-a", "category_uuid": "cat-a", "category_name": "Equipe", "value": "Infra", "generate_report": True, "include_temporal_comparison": True},
            {"tag_uuid": "tag-b", "category_uuid": "cat-b", "category_name": "Local", "value": "Fortaleza", "generate_report": True, "include_temporal_comparison": False},
        ],
    }
    profile = ClientProfile.from_dict(data)
    assert [item.tag_uuid for item in profile.report.tag_reports.tags] == ["tag-a", "tag-b"]


def test_profile_rejects_comparison_without_report():
    data = {
        "schema_version": 1, "client_id": "client-001",
        "display_name": "Cliente", "tenant_id": "tenant",
        "report": {},
    }
    data["report"]["tag_reports"] = {"enabled": True, "tags": [{
        "tag_uuid": "tag-a", "category_name": "Equipe", "value": "Infra",
        "generate_report": False, "include_temporal_comparison": True,
    }]}
    with pytest.raises(ProfileError, match="comparativo.*relatório"):
        ClientProfile.from_dict(data)


def test_legacy_network_selectors_remain_available_for_runtime_resolution():
    data = {
        "schema_version": 1, "client_id": "client-001",
        "display_name": "Cliente", "tenant_id": "tenant",
        "report": {},
    }
    data["report"]["network_comparison_tags"] = ["Rede: Matriz"]
    profile = ClientProfile.from_dict(data)
    assert profile.report.legacy_network_comparison_tags == ("Rede: Matriz",)
```

- [ ] **Step 2: Executar os testes e confirmar a falha inicial**

Run: `python -m pytest tests/test_profile_environment.py -q`  
Expected: FAIL porque `TagReportsConfig` e `legacy_network_comparison_tags` ainda não existem.

- [ ] **Step 3: Implementar os dataclasses e a validação determinística**

```python
@dataclass(frozen=True, slots=True)
class TagReportSelection:
    tag_uuid: str
    category_uuid: str
    category_name: str
    value: str
    generate_report: bool = True
    include_temporal_comparison: bool = False


@dataclass(frozen=True, slots=True)
class TagReportsConfig:
    enabled: bool = False
    tags: tuple[TagReportSelection, ...] = ()


@dataclass(frozen=True, slots=True)
class ReportConfig:
    type: str = "vulnerabilities"
    base_modules: tuple[str, ...] = REQUIRED_BASE_MODULES
    intelligence_modules: tuple[str, ...] = ()
    tag_reports: TagReportsConfig = field(default_factory=TagReportsConfig)
    legacy_network_comparison_tags: tuple[str, ...] = ()

    @property
    def network_comparison_tags(self) -> tuple[str, ...]:
        return self.legacy_network_comparison_tags
```

O parser deve rejeitar UUID vazio/duplicado, categoria ou valor vazio e comparação sem relatório. `vm_network_comparison` permanece aceito como módulo legado, mas não é incluído em novos perfis de exemplo.

- [ ] **Step 4: Executar os testes de perfil e configuração**

Run: `python -m pytest tests/test_profile_environment.py tests/test_orchestration.py -q`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tenable_reports/config/profile.py tests/test_profile_environment.py orchestration/clients.example.json
git commit -m "feat: add per-client tag report configuration"
```

### Task 2: Descoberta e snapshot de ativos por TAG

**Files:**
- Modify: `src/tenable_reports/application/tag_scope.py:12-230`
- Modify: `tests/test_tag_scope.py`
- Modify: `tests/test_vm_client.py`

**Interfaces:**
- Consumes: `TagReportSelection` e `TenableVmClient.list_tag_values/list_assets_for_tag`.
- Produces: `TagAssetScope(tag, asset_ids)`.
- Produces: `TagScopeCollection(path, scopes, warnings)`.
- Produces: `collect_tag_scope_snapshot(..., tags) -> TagScopeCollection` com snapshot `schema_version=2`.

- [ ] **Step 1: Substituir o teste que proíbe categorias diferentes por isolamento por UUID**

```python
def test_selectors_accept_different_categories_because_each_tag_is_resolved_alone():
    selected = resolve_tag_selectors(self.tags, ["tag-a", "tag-c"])
    self.assertEqual([item.uuid for item in selected], ["tag-a", "tag-c"])


def test_scope_snapshot_keeps_each_tag_asset_set_separate(tmp_path):
    client = FakeVmClient({
        ("Rede", "Matriz"): [{"id": "asset-a"}, {"id": "asset-shared"}],
        ("Sistema", "Linux"): [{"id": "asset-b"}, {"id": "asset-shared"}],
    })
    result = collect_tag_scope_snapshot(
        client=client, profile=profile(), tags=(tag_a, tag_c),
        output_root=tmp_path, run_id="run-1",
    )
    assert result.scopes[0].asset_ids == frozenset({"asset-a", "asset-shared"})
    assert result.scopes[1].asset_ids == frozenset({"asset-b", "asset-shared"})
```

- [ ] **Step 2: Adicionar teste de falha isolada e proteção contra população incompleta**

```python
def test_one_tag_failure_becomes_warning_without_erasing_other_scopes(tmp_path):
    client = FakeVmClient({("Equipe", "OK"): [{"id": "asset-a"}]}, failures={("Equipe", "Falha"): RuntimeError("limit")})
    result = collect_tag_scope_snapshot(
        client=client, profile=profile(), tags=(tag_ok, tag_failure),
        output_root=tmp_path, run_id="run-1",
    )
    assert [scope.tag.uuid for scope in result.scopes] == ["tag-ok"]
    assert result.warnings[0]["tag_uuid"] == "tag-failure"
    assert result.warnings[0]["code"] == "TAG_SCOPE_UNAVAILABLE"
```

- [ ] **Step 3: Executar os testes e confirmar as falhas**

Run: `python -m pytest tests/test_tag_scope.py tests/test_vm_client.py -q`  
Expected: FAIL no teste de categorias diferentes e nos novos tipos de retorno.

- [ ] **Step 4: Implementar snapshot v2 com uma entrada independente por TAG**

```python
@dataclass(frozen=True, slots=True)
class TagAssetScope:
    tag: VmTag
    asset_ids: frozenset[str]


@dataclass(frozen=True, slots=True)
class TagScopeCollection:
    path: Path
    scopes: tuple[TagAssetScope, ...]
    warnings: tuple[dict[str, Any], ...]

    @property
    def asset_ids(self) -> frozenset[str]:
        return frozenset(value for scope in self.scopes for value in scope.asset_ids)
```

O JSON deve usar `match_operator: "INDEPENDENT_TAG_SCOPES"` e `selected_tags[*].asset_ids`. O leitor continua aceitando o snapshot v1.

- [ ] **Step 5: Executar os testes de TAG e cliente VM**

Run: `python -m pytest tests/test_tag_scope.py tests/test_vm_client.py -q`  
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/tenable_reports/application/tag_scope.py tests/test_tag_scope.py tests/test_vm_client.py
git commit -m "feat: isolate asset scopes for each Tenable tag"
```

### Task 3: Datasets efêmeros por TAG a partir da coleta geral

**Files:**
- Create: `src/tenable_reports/application/tag_report_dataset.py`
- Create: `tests/test_tag_report_dataset.py`
- Modify: `src/tenable_reports/application/report_dataset.py:68-230`
- Modify: `src/tenable_reports/domain/report_dataset.py:560-835`

**Interfaces:**
- Consumes: normalizados gerais, `TagScopeCollection`, `ReportingPeriod` e `ClientProfile`.
- Produces: `TagReportDatasetArtifact(tag, dataset_path, directory, result)`.
- Produces: `TagReportDatasetBundle(artifacts, warnings)`.
- Produces: `build_tag_report_datasets_from_snapshot(...) -> TagReportDatasetBundle`.
- Test fixtures to add in `tests/test_tag_report_dataset.py`: `_write_normalized_run(root: Path, *, run_id: str) -> None`, `_profile_with_tags() -> ClientProfile`, `_july_2026() -> ReportingPeriod` e `_build_dataset_case(root: Path, *, tag_scope: Mapping | None) -> ReportDatasetResult`. Elas devem reutilizar os registros de `normalized_fixture()` em `tests/test_report_dataset.py` e fixar `generated_at=2026-08-01T03:00:00Z`.

- [ ] **Step 1: Escrever fixture com duas TAGs sobrepostas e findings distintos**

```python
def test_tag_datasets_are_isolated_and_general_dataset_is_unchanged(tmp_path):
    without_tags = _build_dataset_case(tmp_path / "without", tag_scope=None)
    tag_scope = {
        "selected_tags": [
            {"uuid": "tag-a", "category_name": "Equipe", "value": "Infra", "asset_ids": ["asset-a", "asset-shared"]},
            {"uuid": "tag-b", "category_name": "Local", "value": "Fortaleza", "asset_ids": ["asset-b", "asset-shared"]},
        ]
    }
    with_tags = _build_dataset_case(tmp_path / "with", tag_scope=tag_scope)
    _write_normalized_run(tmp_path, run_id="run-1")
    bundle = build_tag_report_datasets_from_snapshot(
        profile=_profile_with_tags(), run_id="run-1", period=_july_2026(),
        output_root=tmp_path, include_output=False,
        execution_type="AUTOMATIC_MONTHLY",
    )
    by_uuid = {item.tag.uuid: json.loads(item.dataset_path.read_text(encoding="utf-8")) for item in bundle.artifacts}
    assert by_uuid["tag-a"]["metrics"]["non_mitigated"]["total"] == 2
    assert by_uuid["tag-b"]["metrics"]["non_mitigated"]["total"] == 1
    assert with_tags.dataset.metrics == without_tags.dataset.metrics
    assert with_tags.dataset.top_assets == without_tags.dataset.top_assets
    assert with_tags.dataset.top_open_vulnerabilities == without_tags.dataset.top_open_vulnerabilities
```

- [ ] **Step 2: Escrever testes para Top 10, Top 5, hosts e TAG sem findings**

```python
def test_tag_dataset_contains_operational_payload_even_without_findings(tmp_path):
    artifact = build_empty_tag_bundle(tmp_path).artifacts[0]
    data = json.loads(artifact.dataset_path.read_text(encoding="utf-8"))
    assert data["tag"]["tag_uuid"] == "tag-empty"
    assert data["top_assets"] == []
    assert data["top_open_vulnerabilities"] == []
    assert data["metrics"]["non_mitigated"]["total"] == 0
```

- [ ] **Step 3: Executar os testes e confirmar a ausência do builder**

Run: `python -m pytest tests/test_tag_report_dataset.py -q`  
Expected: FAIL com import de `tag_report_dataset` inexistente.

- [ ] **Step 4: Implementar carregamento único e recorte por `source_asset_id`**

```python
@dataclass(frozen=True, slots=True)
class TagReportDatasetArtifact:
    tag: VmTag
    result: ReportDatasetResult
    directory: Path
    dataset_path: Path


def _slice_rows(assets, findings, asset_ids):
    selected_assets = tuple(item for item in assets if item.source_asset_id in asset_ids)
    selected_keys = {item.asset_key for item in selected_assets}
    selected_findings = tuple(item for item in findings if item.asset_key in selected_keys)
    return selected_assets, selected_findings
```

Para cada escopo, chamar `build_report_dataset(..., was_findings=(), was_collected=False, tag_scope=None)` e acrescentar `tag`, `document_kind="tag"` e proveniência da seleção ao JSON. Gravar em `report-datasets/{client_id}/{run_id}/{period_id}/tags/{tag_uuid}/report-dataset.json`.

- [ ] **Step 5: Executar dataset, reconciliação e regressão geral**

Run: `python -m pytest tests/test_tag_report_dataset.py tests/test_report_dataset.py tests/test_normalization.py -q`  
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/tenable_reports/application/tag_report_dataset.py src/tenable_reports/application/report_dataset.py src/tenable_reports/domain/report_dataset.py tests/test_tag_report_dataset.py
git commit -m "feat: derive per-tag datasets from normalized collection"
```

### Task 4: Histórico compacto e série anual por TAG

**Files:**
- Modify: `src/tenable_reports/domain/history.py:12-350`
- Modify: `src/tenable_reports/application/history.py:41-740`
- Modify: `src/tenable_reports/infrastructure/postgresql.py:45-105`
- Modify: `tests/test_history.py`
- Modify: `tests/test_postgresql.py`

**Interfaces:**
- Consumes: caminhos dos datasets por TAG da Task 3.
- Produces: `HistorySnapshot.tag_snapshots` com leitura alternativa de `network_tag_snapshots`.
- Produces: `tag_year_history(snapshots, current, tag_uuid) -> tuple[dict, ...]`.
- Extends: `HistoryPreparation.tag_enriched_dataset_paths: Mapping[str, Path]`.
- Extends: `prepare_dataset_history(..., tag_dataset_paths: Mapping[str, Path])`.

- [ ] **Step 1: Escrever teste de leitura legada e escrita somente no formato novo**

```python
def test_legacy_network_tag_snapshots_load_as_generic_tag_snapshots():
    payload = legacy_snapshot_payload()
    payload["network_tag_snapshots"] = [{"tag_uuid": "tag-a", "network": "Matriz", "assets": []}]
    snapshot = HistorySnapshot.from_dict(payload)
    assert snapshot.tag_snapshots[0]["tag_uuid"] == "tag-a"
    stored = snapshot.to_dict()
    assert "tag_snapshots" in stored
    assert "network_tag_snapshots" not in stored
```

- [ ] **Step 2: Escrever testes da série janeiro-mês corrente e lacunas**

```python
def test_tag_year_history_marks_missing_month_without_zero():
    rows = tag_year_history(
        (snapshot("2026-01", tag_total=10), snapshot("2026-03", tag_total=8)),
        current=snapshot("2026-04", tag_total=7), tag_uuid="tag-a",
    )
    assert [row["period_id"] for row in rows] == ["2026-01", "2026-02", "2026-03", "2026-04"]
    assert rows[1] == {"period_id": "2026-02", "label": "Fevereiro/2026", "availability": "UNAVAILABLE"}
    assert "non_mitigated" not in rows[1]
```

- [ ] **Step 3: Escrever teste que exclui outra TAG, outro ano e snapshot não `main`**

O teste deve fornecer a `prepare_dataset_history` apenas os snapshots devolvidos por `registry.list_main_snapshots_before` e confirmar que o dataset enriquecido de `tag-a` não contém `tag-b` nem dezembro de 2025.

- [ ] **Step 4: Executar os testes e confirmar as falhas de contrato**

Run: `python -m pytest tests/test_history.py tests/test_postgresql.py -q`  
Expected: FAIL porque `tag_snapshots`, `tag_year_history` e `tag_enriched_dataset_paths` não existem.

- [ ] **Step 5: Implementar resumo compacto e enriquecimento por TAG**

```python
def tag_snapshot_from_dataset(data: Mapping[str, Any]) -> dict[str, Any]:
    tag = data["tag"]
    return {
        "tag_uuid": tag["tag_uuid"],
        "category_uuid": tag.get("category_uuid", ""),
        "category_name": tag["category_name"],
        "value": tag["value"],
        "summary": summary_from_dataset(data),
        "top_assets": list(data.get("top_assets") or ()),
    }
```

`prepare_dataset_history` deve incluir esses resumos no snapshot corrente, construir `tag_history` por UUID e gravar cada enriquecido ao lado do dataset canônico da TAG. Execuções não mensais recebem `tag_history_status="INCOMPATIBLE_PERIOD"` e nenhuma série artificial.

- [ ] **Step 6: Executar histórico, PostgreSQL e referência `main`**

Run: `python -m pytest tests/test_history.py tests/test_postgresql.py tests/test_report_reference.py -q`  
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/tenable_reports/domain/history.py src/tenable_reports/application/history.py src/tenable_reports/infrastructure/postgresql.py tests/test_history.py tests/test_postgresql.py
git commit -m "feat: persist compact yearly history per tag"
```

### Task 5: Nome do arquivo e relatório operacional compacto por TAG

**Files:**
- Create: `src/tenable_reports/presentation/tag_report_docx.py`
- Create: `tests/test_tag_report_docx.py`
- Modify: `src/tenable_reports/presentation/report_filenames.py:1-65`
- Modify: `src/tenable_reports/presentation/__init__.py`
- Modify: `tests/test_report_filenames.py`

**Interfaces:**
- Produces: `tag_report_filename(display_name, period, category, value, tag_uuid) -> str`.
- Produces: `TagReportRenderResult(output_path, client_id, period_id, tag_uuid, top_asset_rows, top_open_rows, comparison_rendered)`.
- Produces: `generate_tag_report(template_path, dataset_path, profile, output_path, mask_sensitive=False, translator=None)`.
- Test fixtures to add in `tests/test_tag_report_docx.py`: `_template() -> Path` usa `templates/corporate/base-v1.docx`; `_tag_dataset(tmp_path: Path, *, empty=False, with_history=False) -> Path` parte de `tests/fixtures/report-dataset-phase5.json`, remove WAS/Cloud e acrescenta `tag`; `_profile(*, include_output=False) -> ClientProfile` carrega `clients/examples/client-profile.json` com a opção ajustada.

- [ ] **Step 1: Escrever testes do nome seguro e da colisão**

```python
def test_tag_filename_contains_category_value_and_period():
    name = tag_report_filename("CLIENTE K", july_period(), "Equipe", "Infraestrutura", "tag-a")
    assert name == "[CLIENTE K] Relatório de Vulnerabilidades Tenable TAG Equipe - Infraestrutura JUL26.docx"


def test_tag_filename_uses_uuid_when_sanitized_label_is_empty():
    name = tag_report_filename("Cliente", july_period(), "///", "***", "12345678-abcd")
    assert "12345678" in name
```

- [ ] **Step 2: Escrever teste estrutural do DOCX com Top 10, Top 5 e hosts completos**

```python
def test_tag_report_contains_only_approved_operational_sections(tmp_path):
    result = generate_tag_report(
        template_path=_template(), dataset_path=_tag_dataset(tmp_path), profile=_profile(),
        output_path=tmp_path / "tag.docx",
    )
    document = Document(result.output_path)
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert "Principais Ativos Vulneráveis" in text
    assert "VULNERABILIDADES E SUAS CORREÇÕES" in text
    assert "SENSOR WAS" not in text
    assert "CLOUD SECURITY" not in text
    host_headers = [[cell.text for cell in table.rows[0].cells] for table in document.tables]
    assert ["ASSET NAME", "IP", "PORTA", "PROTOCOLO"] in host_headers
```

- [ ] **Step 3: Escrever testes de TAG vazia, `Output` e filtros opcionais**

Confirmar que o documento vazio contém `Neste mês não foram identificadas`, que a coluna `Output` aparece somente com `profile.presentation.vm_top5_include_output=True` e que `show_source_filters=True` acrescenta abaixo das tabelas uma nota de validação contendo o UUID, a categoria e o valor da TAG.

- [ ] **Step 4: Executar os testes e confirmar a ausência do renderer**

Run: `python -m pytest tests/test_report_filenames.py tests/test_tag_report_docx.py -q`  
Expected: FAIL com imports inexistentes.

- [ ] **Step 5: Implementar o renderer usando o template e catálogo existentes**

O novo arquivo deve reutilizar as rotinas de estilo, token, capa, tabela de ativos e detalhamento VM de `full_base_report_docx.py`, sem copiar textos do cliente K. O dataset `tag` fornece os tokens de categoria e valor; os títulos operacionais usam o catálogo já aprovado.

- [ ] **Step 6: Executar testes do renderer e do relatório-base**

Run: `python -m pytest tests/test_tag_report_docx.py tests/test_full_base_report_docx.py tests/test_report_filenames.py -q`  
Expected: PASS e nenhuma alteração no relatório-base.

- [ ] **Step 7: Commit**

```bash
git add src/tenable_reports/presentation/tag_report_docx.py src/tenable_reports/presentation/report_filenames.py src/tenable_reports/presentation/__init__.py tests/test_tag_report_docx.py tests/test_report_filenames.py
git commit -m "feat: render compact operational report for each tag"
```

### Task 6: Tabelas e gráficos anuais dentro do relatório da TAG

**Files:**
- Create: `src/tenable_reports/presentation/monthly_visuals.py`
- Modify: `src/tenable_reports/presentation/tag_report_docx.py`
- Modify: `src/tenable_reports/presentation/customizations_report_docx.py:240-690,1023-1080`
- Modify: `tests/test_tag_report_docx.py`
- Modify: `tests/test_customizations_report_docx.py`

**Interfaces:**
- Produces: `render_monthly_table(document, rows, nested_key, total_key)`.
- Produces: `render_monthly_visual_bundle(document, rows, output_dir, scope_label)`.
- Consumes: `dataset["tag_history"]` e `dataset["tag_comparison"]`.
- Test fixtures to add: `_render_tag_with_history(tmp_path: Path, *, include_comparison: bool) -> TagReportRenderResult`, `_render_tag_with_gap(tmp_path: Path) -> TagReportRenderResult`, `_document_text(path: Path) -> str` e `_count_document_images(path: Path) -> int`. As duas primeiras usam `_tag_dataset(..., with_history=True)` da Task 5; a contagem de imagens lê `word/media/` no pacote DOCX.

- [ ] **Step 1: Escrever teste de tabelas nativas e cinco visuais esperados**

```python
def test_enabled_tag_comparison_renders_tables_and_charts(tmp_path):
    result = _render_tag_with_history(tmp_path, include_comparison=True)
    document = Document(result.output_path)
    headers = [[cell.text for cell in table.rows[0].cells] for table in document.tables]
    assert ["Mês", "Crítica", "Alta", "Média", "Baixa", "Total"] in headers
    assert result.comparison_rendered is True
    assert _count_document_images(result.output_path) >= 5
```

- [ ] **Step 2: Escrever teste de lacuna e comparação desabilitada**

```python
def test_missing_month_is_unavailable_and_not_plotted_as_zero(tmp_path):
    result = _render_tag_with_gap(tmp_path)
    document = Document(result.output_path)
    assert any("Fevereiro/2026" in row.cells[0].text and "Indisponível" in row.cells[-1].text for table in document.tables for row in table.rows)


def test_disabled_comparison_has_no_empty_heading_or_chart(tmp_path):
    result = _render_tag_with_history(tmp_path, include_comparison=False)
    assert result.comparison_rendered is False
    assert "Comparativo Mensal" not in _document_text(result.output_path)
```

- [ ] **Step 3: Executar os testes e confirmar as falhas**

Run: `python -m pytest tests/test_tag_report_docx.py tests/test_customizations_report_docx.py -q`  
Expected: FAIL porque o bundle visual compartilhado ainda não existe.

- [ ] **Step 4: Extrair os geradores Pillow sem mudar a saída geral**

Mover para `monthly_visuals.py` as séries, formatação, gráfico agrupado, gráfico de linha e evolução conjunta. As funções devem ignorar valores ausentes, segmentando a linha nos pontos indisponíveis em vez de chamar `_number(None)` e produzir zero.

- [ ] **Step 5: Renderizar três tabelas e os gráficos no relatório da TAG**

Ordem: não mitigadas (tabela, comparativo, volume), mitigadas (tabela, comparativo, volume), novas (tabela), evolução conjunta. Acrescentar ranking anterior e movimentação somente quando houver predecessor da mesma TAG.

- [ ] **Step 6: Remover a comparação específica de TAG do customizado novo**

Retirar a chamada de `_network_comparison` de `generate_customizations_report`; manter a leitura dos módulos legados no perfil. Confirmar que `_monthly_modules` e `_evolution` continuam usando a série geral e produzindo os mesmos módulos renderizados.

- [ ] **Step 7: Executar regressão visual e estrutural**

Run: `python -m pytest tests/test_tag_report_docx.py tests/test_customizations_report_docx.py tests/test_full_base_report_docx.py -q`  
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/tenable_reports/presentation/monthly_visuals.py src/tenable_reports/presentation/tag_report_docx.py src/tenable_reports/presentation/customizations_report_docx.py tests/test_tag_report_docx.py tests/test_customizations_report_docx.py
git commit -m "feat: add yearly tables and charts to tag reports"
```

### Task 7: Metadados de documentos e migração PostgreSQL

**Files:**
- Create: `src/tenable_reports/infrastructure/postgresql_migrations/0004_tag_report_documents.sql`
- Modify: `src/tenable_reports/application/publishing.py:1-145`
- Modify: `src/tenable_reports/infrastructure/postgresql.py:620-705`
- Modify: `src/tenable_reports/webapp/server.py:500-560`
- Modify: `tests/test_postgresql.py`
- Modify: `tests/test_orchestration.py`
- Modify: `tests/test_webapp.py`

**Interfaces:**
- Produces: `PublicationDocument(path, document_kind, tag_uuid=None, tag_category=None, tag_value=None)`.
- Extends: `create_publication_manifest(documents: Sequence[Path | PublicationDocument])`.
- Extends DB: `published_documents.document_kind`, `tag_uuid`, `tag_category`, `tag_value`.
- Test fixtures to add in `tests/test_orchestration.py`: `_period_dict()`, `_dataset_file(tmp_path)` e `_tag_docx(tmp_path)` reaproveitam `_monthly_dataset`, `_write_month` e `generate_full_base_report` já existentes nesse arquivo.

- [ ] **Step 1: Escrever teste de manifesto tipado e compatibilidade com `Path`**

```python
def test_manifest_records_tag_document_metadata(tmp_path):
    manifest = create_publication_manifest(
        output_path=tmp_path / "manifest.json", client_id="cliente", tenant_id="tenant",
        run_id="run-1", execution_type="MANUAL", period=_period_dict(),
        dataset_path=_dataset_file(tmp_path),
        documents=(PublicationDocument(_tag_docx(tmp_path), "tag", "tag-a", "Equipe", "Infra"),),
        history_database=None,
    )
    document = json.loads(manifest.read_text(encoding="utf-8"))["documents"][0]
    assert document["document_kind"] == "tag"
    assert document["tag_uuid"] == "tag-a"
```

- [ ] **Step 2: Escrever teste da migração e consulta web**

Verificar no SQL os quatro campos aditivos e confirmar que `DashboardDatabase.reports()` devolve `document_kind`, `tag_uuid`, `tag_category` e `tag_value`.

- [ ] **Step 3: Executar os testes e confirmar as falhas**

Run: `python -m pytest tests/test_postgresql.py tests/test_orchestration.py tests/test_webapp.py -q`  
Expected: FAIL porque os metadados ainda não são persistidos.

- [ ] **Step 4: Criar a migração aditiva**

```sql
alter table tenable_reports.published_documents add column if not exists document_kind text;
alter table tenable_reports.published_documents add column if not exists tag_uuid text;
alter table tenable_reports.published_documents add column if not exists tag_category text;
alter table tenable_reports.published_documents add column if not exists tag_value text;

alter table tenable_reports.published_documents
    add constraint published_documents_kind_check
    check (document_kind is null or document_kind in ('base', 'custom', 'tag'));

create index if not exists published_documents_tag_idx
on tenable_reports.published_documents (tag_uuid)
where tag_uuid is not null;
```

Antes de criar a constraint, usar `drop constraint if exists published_documents_kind_check` para manter a migração idempotente.

- [ ] **Step 5: Persistir e consultar os metadados opcionais**

Entradas antigas do manifesto continuam aceitas e recebem `document_kind=None`. A tela pode inferir `Geral` apenas para apresentação, sem regravar o histórico.

- [ ] **Step 6: Executar testes de publicação e banco**

Run: `python -m pytest tests/test_postgresql.py tests/test_orchestration.py tests/test_webapp.py -q`  
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/tenable_reports/infrastructure/postgresql_migrations/0004_tag_report_documents.sql src/tenable_reports/application/publishing.py src/tenable_reports/infrastructure/postgresql.py src/tenable_reports/webapp/server.py tests/test_postgresql.py tests/test_orchestration.py tests/test_webapp.py
git commit -m "feat: register tag report metadata in publications"
```

### Task 8: Execução completa, warnings e orquestração

**Files:**
- Modify: `src/tenable_reports/cli.py:125-180,430-510,920-1070,1216-1285`
- Modify: `src/tenable_reports/application/orchestration.py:680-1020`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_orchestration.py`
- Modify: `tests/test_retention.py`

**Interfaces:**
- Consumes: builders, histórico, renderer e `PublicationDocument` das Tasks 2-7.
- Extends: `_CollectedPeriodExecution.tag_artifacts`, `tag_enriched_dataset_paths`, `warnings`.
- Produces no JSON final: `tag_reports_requested`, `tag_reports_generated`, `tag_reports_failed`.
- Produces durante a execução: linhas JSON `{"event":"TAG_REPORT_PROGRESS","current":1,"total":2,"tag_uuid":"tag-a","tag_label":"Equipe: Infra"}`.
- Extends: runners da orquestração com `progress_callback: Callable[[Mapping[str, Any]], None] | None`, preservando o retorno final `CompletedProcess`.
- Test fixtures to add in `tests/test_cli.py`: `_run_client_with_two_tags()` usa os mocks já empregados por `test_complete_run_records_manifest_only_after_history_is_finalized`; `_run_with_tag_renderer_failure(tag_uuid)` aplica `side_effect` somente ao segundo `generate_tag_report`.

- [ ] **Step 1: Escrever teste de uma coleta geral e dois documentos por TAG**

```python
def test_run_client_collects_vm_once_and_generates_selected_tag_reports():
    result, calls, manifest = _run_client_with_two_tags()
    assert calls["asset_export"] == 1
    assert calls["finding_export"] == 1
    assert result["tag_reports_generated"] == 2
    assert [item["document_kind"] for item in manifest["documents"]] == ["base", "custom", "tag", "tag"]
```

- [ ] **Step 2: Escrever teste de falha isolada**

```python
def test_tag_render_failure_keeps_general_documents_and_emits_warning():
    result, manifest = _run_with_tag_renderer_failure("tag-b")
    assert result["status"] == "complete_with_warnings"
    assert result["tag_reports_generated"] == 1
    assert result["tag_reports_failed"] == 1
    assert {item["document_kind"] for item in manifest["documents"]} == {"base", "custom", "tag"}
    assert result["warnings"][0]["tag_uuid"] == "tag-b"
```

- [ ] **Step 3: Escrever teste de retenção dos novos intermediários**

Confirmar que `report-datasets/.../tags` só pode ser removido depois de documento validado, histórico confirmado e manifesto registrado, usando os mesmos gates dos datasets gerais.

- [ ] **Step 4: Executar os testes e confirmar as falhas**

Run: `python -m pytest tests/test_cli.py tests/test_orchestration.py tests/test_retention.py -q`  
Expected: FAIL porque a execução ainda publica apenas base e customizado.

- [ ] **Step 5: Integrar a ordem transacional da execução**

Implementar: escopos por TAG → coleta geral → normalização geral → dataset geral e datasets por TAG → preparo do histórico geral e por TAG → base → customizado → documentos por TAG → manifesto com todos os documentos válidos → finalização histórica → registro PostgreSQL.

- [ ] **Step 6: Propagar progresso e warnings estruturados**

Cada warning deve conter `code`, `tag_uuid`, `tag_label`, `stage` e `message`. Antes de gerar cada documento TAG, `run-client` escreve e libera imediatamente uma linha `TAG_REPORT_PROGRESS` em stdout. O runner da orquestração usa `subprocess.Popen`, lê stdout linha a linha, encaminha esses eventos ao callback e ainda acumula stdout/stderr para montar o mesmo `CompletedProcess` esperado pelos testes. A orquestração preserva warnings no payload do cliente; falha geral continua retornando `FAILED`, enquanto falhas exclusivas de TAG retornam `COMPLETE` com `status=complete_with_warnings` no payload.

- [ ] **Step 7: Executar testes de CLI, orquestração e retenção**

Run: `python -m pytest tests/test_cli.py tests/test_orchestration.py tests/test_retention.py -q`  
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/tenable_reports/cli.py src/tenable_reports/application/orchestration.py tests/test_cli.py tests/test_orchestration.py tests/test_retention.py
git commit -m "feat: orchestrate general and per-tag report generation"
```

### Task 9: API e interface web para gerenciar TAGs

**Files:**
- Modify: `src/tenable_reports/webapp/server.py:250-470,500-610,1179-1385`
- Modify: `src/tenable_reports/webapp/static/index.html:84-125`
- Modify: `src/tenable_reports/webapp/static/app.js:1-370`
- Modify: `src/tenable_reports/webapp/static/app.css:180-420`
- Modify: `tests/test_webapp.py`

**Interfaces:**
- Produces: `GET /api/clients/{client_id}/tags`.
- Accepts em POST/PATCH cliente: `tag_reports_enabled` e `tag_reports`.
- Returns em relatórios: metadados para grupos `base`, `custom`, `tag`.
- Consumes em tempo real: eventos `TAG_REPORT_PROGRESS` encaminhados pela orquestração.

- [ ] **Step 1: Escrever teste da rota de descoberta sem exposição de credenciais**

```python
def test_get_client_tags_uses_local_credentials_and_returns_safe_values():
    response = request("GET", "/api/clients/cliente-a/tags")
    assert response.status == 200
    assert response.json["tags"][0] == {
        "tag_uuid": "tag-a", "category_uuid": "cat-a",
        "category_name": "Equipe", "value": "Infraestrutura",
    }
    assert "access" not in json.dumps(response.json).lower()
    assert "secret" not in json.dumps(response.json).lower()
```

- [ ] **Step 2: Escrever testes de persistência e TAG indisponível**

Salvar duas TAGs de categorias diferentes, recarregar `DashboardConfigStore` e confirmar os mesmos UUIDs e opções. Simular que uma TAG salva não veio na consulta e confirmar `available=false` na resposta combinada da edição.

- [ ] **Step 3: Escrever testes de `401`, `403`, `429` e cliente inexistente**

As respostas devem usar códigos HTTP adequados e `_safe_error`; nenhuma deve conter headers de autenticação nem conteúdo do `.env`.

- [ ] **Step 4: Executar testes do backend web e confirmar as falhas**

Run: `python -m pytest tests/test_webapp.py -q`  
Expected: FAIL porque a rota e o novo payload ainda não existem.

- [ ] **Step 5: Implementar persistência no `DashboardConfigStore` e a rota GET**

O método `list_client_tags(client_id)` carrega o `.env`, cria `TenableVmClient`, chama `list_tag_values()`, normaliza com `parse_tag_values()` e devolve `fetched_at`. O GET não altera o perfil; somente POST/PATCH salva a seleção.

- [ ] **Step 6: Substituir o campo textual pelo seletor compacto**

Adicionar chave de ativação, botão `Buscar TAGs da Tenable`, pesquisa, grupos por categoria e duas caixas por linha. Desabilitar `Incluir comparativo temporal` enquanto `Gerar relatório` estiver desmarcado e remover o comparativo automaticamente se o relatório for desmarcado.

- [ ] **Step 7: Agrupar documentos e mostrar progresso/alertas por TAG**

`renderReports()` deve criar os grupos `Geral`, `Customizado` e `Por TAG` usando `document_kind`; documentos TAG exibem `tag_category: tag_value`. O `JobQueue` executa o processo com leitura incremental, converte `TAG_REPORT_PROGRESS` em `job.tag_progress={current,total,label}` sob o mesmo lock de `progress` e expõe o valor em `/api/state`. Warnings usam o badge já existente no card.

- [ ] **Step 8: Executar testes e smoke test local da interface**

Run: `python -m pytest tests/test_webapp.py -q`  
Expected: PASS.

Run: `powershell.exe -ExecutionPolicy Bypass -File .\scripts\run_web.ps1`  
Expected: servidor inicia; editar cliente, buscar TAGs, salvar e reabrir preserva a seleção.

- [ ] **Step 9: Commit**

```bash
git add src/tenable_reports/webapp/server.py src/tenable_reports/webapp/static/index.html src/tenable_reports/webapp/static/app.js src/tenable_reports/webapp/static/app.css tests/test_webapp.py
git commit -m "feat: manage Tenable tag reports from web interface"
```

### Task 10: Documentação, migração real e verificação completa

**Files:**
- Modify: `README.md`
- Modify: `docs/10-escopo-tags-e-comparativo-por-rede.md`
- Modify: `docs/11-catalogo-visual-e-tabelas-customizadas.md`
- Modify: `docs/17-interface-web-mvp.md`
- Modify: `scripts/bootstrap_postgresql.ps1` somente se a aplicação automática de `0004` exigir ajuste comprovado.
- Create: `scripts/render_tag_report_fixture.py` — gera em `.tmp/e2e-tag-reports` uma execução sanitizada com base, customizado, uma TAG com comparativo e uma TAG sem comparativo.

**Interfaces:**
- Documents: operação inteiramente pela interface web, comportamento legado e baseline inicial.
- Verifies: pacote Python, PostgreSQL, DOCX e frontend.

- [ ] **Step 1: Atualizar a documentação sem manter “rede” como conceito técnico**

Explicar que “rede” é apenas um possível valor/categoria de TAG; documentar `Buscar TAGs`, as duas seleções por TAG, o nome dos arquivos, a coleta única, o comportamento sem histórico e os dados retidos.

- [ ] **Step 2: Executar todas as migrações em um banco de teste**

Run: `.\.venv\Scripts\python.exe -m tenable_reports database-bootstrap --database-env-file .\credentials\database.env --admin-env-file .\credentials\postgresql-admin.env`  
Expected: migrações `0001` a `0004` aplicadas uma vez; segunda execução não altera o schema nem falha.

- [ ] **Step 3: Executar a suíte completa**

Run: `python -m pytest -q`  
Expected: todos os testes passam, incluindo os novos testes por TAG.

- [ ] **Step 4: Executar verificação integrada do repositório**

Run: `git diff --check`  
Expected: nenhuma saída.

Run: `git status --short`  
Expected: somente arquivos deliberadamente alterados pelo plano, sem `.env`, credenciais, `.tmp`, `data` ou DOCX de teste.

- [ ] **Step 5: Gerar fixture fim a fim com duas TAGs**

Run: `python scripts/render_tag_report_fixture.py`  
Expected: `.tmp/e2e-tag-reports/publication-manifest.json` contém exatamente quatro documentos: base, customizado e dois por TAG; somente `tag-equipe-infraestrutura.docx` possui a seção temporal.

- [ ] **Step 6: Renderizar todos os DOCX com LibreOffice e revisar visualmente**

Run: `python scripts/prepare_docx_qa_render.py .tmp/e2e-tag-reports/tag-equipe-infraestrutura.docx .tmp/qa-tag-reports/tag-equipe-infraestrutura.docx`  
Expected: cópia preparada sem marcadores de campos incompatíveis.

Run: `C:\Codex\LibreOfficePortable\App\libreoffice\program\soffice.exe --headless --convert-to pdf --outdir .tmp\qa-tag-reports .tmp\qa-tag-reports\tag-equipe-infraestrutura.docx`  
Expected: PDF criado; repetir os dois comandos para base, customizado e TAG sem comparativo. A revisão visual confirma capa e contracapa preservadas, datas corretas, nenhum título órfão, tabela cortada, página vazia acidental ou texto sobreposto.

- [ ] **Step 7: Validar invariância dos documentos gerais**

Gerar a mesma fixture com `tag_reports.enabled=false` e `true`; comparar métricas, tabelas e textos do base e do customizado. As únicas diferenças permitidas no manifesto são os documentos TAG adicionais e seus metadados.

- [ ] **Step 8: Confirmar limpeza segura**

Executar a limpeza pela interface após a publicação de teste e confirmar que DOCX e snapshot histórico compacto permanecem, enquanto `raw`, `snapshots`, `normalized` e `report-datasets/.../tags` elegíveis são removidos.

- [ ] **Step 9: Commit final de documentação e evidências**

```bash
git add README.md docs/10-escopo-tags-e-comparativo-por-rede.md docs/11-catalogo-visual-e-tabelas-customizadas.md docs/17-interface-web-mvp.md scripts/bootstrap_postgresql.ps1 scripts/render_tag_report_fixture.py
git commit -m "docs: document operational reports by tag"
```

## Execution Order and Review Gates

1. Tasks 1-2 estabelecem configuração e escopo remoto sem alterar documentos.
2. Tasks 3-4 criam dados segmentados e histórico compacto; revisar reconciliação antes de apresentação.
3. Tasks 5-6 criam o DOCX e os visuais; renderizar amostras antes de integrar a publicação.
4. Tasks 7-8 integram persistência e execução; revisar atomicidade do `main` e limpeza.
5. Task 9 expõe o recurso na interface.
6. Task 10 aplica migração, executa regressão completa e valida visualmente.

Cada gate exige `git status --short` sem artefatos inesperados e os testes indicados em PASS antes da próxima tarefa.
