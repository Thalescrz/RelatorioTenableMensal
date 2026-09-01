from __future__ import annotations

import json
import subprocess
from pathlib import Path
from uuid import UUID

from tenable_reports.application.web_batches_memory import (
    InMemoryWebBatchRepository,
)
from tenable_reports.domain.web_batches import (
    BatchJobStatus,
    BatchStatus,
    WebBatch,
    WebBatchJob,
)
from tenable_reports.webapp.durable_dashboard_queue import (
    DurableDashboardJobQueue,
)
from tenable_reports.webapp.server import JobQueue


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "tenable_reports" / "webapp" / "static"


def _run_selection_script(source: str) -> object:
    script_path = STATIC / "client_selection.js"
    completed = subprocess.run(
        [
            "node",
            "-e",
            (
                f"const helpers = require({json.dumps(str(script_path))});"
                f"const result = (() => {{ {source} }})();"
                "process.stdout.write(JSON.stringify(result));"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_client_selection_helpers_filter_by_query_analyst_and_unassigned() -> None:
    clients = [
        {
            "client_id": "a",
            "display_name": "TRT A",
            "responsible_analyst_id": "ana-1",
        },
        {
            "client_id": "b",
            "display_name": "TRT B",
            "responsible_analyst_id": "ana-2",
        },
        {
            "client_id": "c",
            "display_name": "Outro",
            "responsible_analyst_id": None,
        },
    ]
    encoded_clients = json.dumps(clients)

    assert _run_selection_script(
        "return helpers.filterClients("
        f"{encoded_clients}, {{ query: 'trt', analystId: 'ana-1' }}"
        ").map(client => client.client_id);"
    ) == ["a"]
    assert _run_selection_script(
        "return helpers.filterClients("
        f"{encoded_clients}, {{ query: '', analystId: 'unassigned' }}"
        ").map(client => client.client_id);"
    ) == ["c"]


def test_client_selection_helper_changes_only_visible_ids() -> None:
    assert _run_selection_script(
        "return helpers.selectionForVisibleClients(['a'], ['b', 'c'], true);"
    ) == ["a", "b", "c"]
    assert _run_selection_script(
        "return helpers.selectionForVisibleClients("
        "['a', 'b', 'c'], ['b', 'c'], false);"
    ) == ["a"]


def test_responsible_analyst_draft_preserves_explicit_empty_value() -> None:
    assert _run_selection_script(
        "return ["
        "helpers.resolveResponsibleAnalystValue(undefined, 'ana-1'),"
        "helpers.resolveResponsibleAnalystValue('', 'ana-1'),"
        "helpers.resolveResponsibleAnalystValue('ana-2', 'ana-1')"
        "];"
    ) == ["ana-1", "", "ana-2"]


def test_frontend_exposes_analyst_filters_selection_modal_and_management() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")

    for element_id in (
        "dashboard-analyst-filter",
        "run-selection-dialog",
        "run-selection-search",
        "run-selection-analyst-filter",
        "run-selection-list",
        "run-selection-count",
        "select-visible-clients",
        "clear-visible-clients",
        "confirm-run-selection",
        "analyst-form",
        "analyst-list",
    ):
        assert f'id="{element_id}"' in html
    assert 'name="responsible_analyst_id"' in html
    assert html.index("client_selection.js") < html.index("app.js")
    assert "/api/analysts" in javascript
    assert "selection_filter_snapshot" in javascript
    assert "responsible_analyst_id" in javascript
    assert "openRunSelection" in javascript


def test_frontend_exposes_contextual_durable_batch_controls() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "app.css").read_text(encoding="utf-8")

    assert 'id="batch-panel"' in html
    assert 'id="batch-choice-dialog"' in html
    assert 'id="retry-incomplete-button"' in html
    assert 'id="rerun-all-button"' in html
    for label in (
        "Pausar após o atual",
        "Parar lote",
        "Retomar lote",
        "Tentar somente falhas e interrompidos",
        "Gerar novamente para todos",
    ):
        assert label in html or label in javascript
    for route in (
        "/pause",
        "/resume",
        "/stop",
        "/retry-incomplete",
        "/rerun-all",
    ):
        assert route in javascript
    assert "export remoto será preservado" in javascript
    assert "window.confirm" in javascript
    assert "button.disabled = true" in javascript
    assert ".batch-panel" in css
    assert ".batch-actions" in css


def test_frontend_offers_retry_instead_of_resume_for_recovered_paused_batch() -> None:
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")

    assert (
        'const recoveredPaused = batch.kind === "RECOVERED" '
        '&& batch.status === "PAUSED";'
    ) in javascript
    assert 'batch.kind === "RECOVERED" ? "Lote recuperado"' in javascript
    assert (
        'if (!recoveredPaused && batch.status === "PAUSED") '
        'actions.push(["resume", "Retomar lote", "primary"]);'
    ) in javascript
    assert (
        'if (recoveredPaused && Number(batch.retryable_count || 0) > 0) '
        'actions.push(["retry-incomplete", "Tentar falhas/interrompidos", "primary"]);'
    ) in javascript


def test_batch_summary_counts_warnings_as_complete_not_retryable(
    tmp_path: Path,
) -> None:
    repository = InMemoryWebBatchRepository()
    batch_id = UUID(int=1200)
    repository.create_batch(
        WebBatch(
            id=batch_id,
            idempotency_key="batch:ui:summary",
            kind="GENERATE_ALL",
            status=BatchStatus.COMPLETE_WITH_FAILURES,
            options={"requests": []},
        ),
        tuple(
            WebBatchJob(
                id=UUID(int=1200 + position),
                batch_id=batch_id,
                client_id=f"client-{position}",
                position=position,
                status=status,
                attempt_number=1,
            )
            for position, status in enumerate(
                (
                    BatchJobStatus.COMPLETE,
                    BatchJobStatus.COMPLETE_WITH_WARNINGS,
                    BatchJobStatus.FAILED,
                    BatchJobStatus.INTERRUPTED,
                    BatchJobStatus.CANCELLED_BY_USER,
                ),
                start=1,
            )
        ),
    )

    def runner(command, cwd, progress_callback=None):
        return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

    executor = JobQueue(
        tmp_path,
        tmp_path / "orchestration" / "clients.json",
        runner,
        start_worker=False,
    )
    queue = DurableDashboardJobQueue(
        repository=repository,
        executor=executor,
        worker_id="worker-ui",
        start_worker=False,
    )
    try:
        summary = queue.batches_snapshot()[0]
    finally:
        queue.close()

    assert summary["total_count"] == 5
    assert summary["completed_count"] == 2
    assert summary["failed_count"] == 1
    assert summary["interrupted_count"] == 1
    assert summary["cancelled_count"] == 1
    assert summary["retryable_count"] == 3
    assert summary["progress_percent"] == 100
