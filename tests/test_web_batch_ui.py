from __future__ import annotations

import json
import subprocess
from pathlib import Path
from uuid import UUID

from tenable_reports.application.web_batches_memory import (
    InMemoryWebBatchRepository,
)
from tenable_reports.domain.web_batches import (
    BatchJobPhase,
    BatchJobStatus,
    BatchStatus,
    WebBatch,
    WebBatchEvent,
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


def test_frontend_exposes_component_status_and_selective_retry_controls() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "app.css").read_text(encoding="utf-8")

    for element_id in (
        "component-retry-dialog",
        "component-retry-list",
        "confirm-component-retry",
    ):
        assert f'id="{element_id}"' in html
    assert (
        "Tentar componentes com falha" in html
        or "Tentar componentes com falha" in javascript
    )
    assert "Selecionar componentes" in html or "Selecionar componentes" in javascript
    assert "/components" in javascript
    assert "/retry-components" in javascript
    assert "component-status-chip" in javascript
    assert ".component-status-chip" in css
    assert "Tentar Cloud novamente" in javascript
    assert "/retry-cloud" in javascript


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


def test_batch_snapshots_expose_safe_phase_counts_and_worker_capacity(
    tmp_path: Path,
) -> None:
    repository = InMemoryWebBatchRepository()
    batch_id = UUID(int=1300)
    checkpoint = tmp_path / "checkpoints" / "client-ready.json"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text("{}", encoding="utf-8")
    jobs = tuple(
        WebBatchJob(
            id=UUID(int=1300 + position),
            batch_id=batch_id,
            client_id=f"client-{position}",
            position=position,
            status=status,
            phase=phase,
            attempt_number=1,
            collection_checkpoint_path=(
                str(checkpoint)
                if phase in {BatchJobPhase.READY_FOR_BUILD, BatchJobPhase.BUILD_RUNNING}
                else None
            ),
            remote_started_at=(
                "2026-09-01T12:00:00Z"
                if phase in {BatchJobPhase.REMOTE_RUNNING, BatchJobPhase.REMOTE_WAITING_DECISION}
                else None
            ),
            remote_ended_at=(
                "2026-09-01T12:30:00Z"
                if phase in {BatchJobPhase.READY_FOR_BUILD, BatchJobPhase.BUILD_RUNNING}
                else None
            ),
            build_started_at=(
                "2026-09-01T12:31:00Z"
                if phase is BatchJobPhase.BUILD_RUNNING
                else None
            ),
            worker_id=(
                "tenable-remote-worker-phase-ui-1"
                if phase is BatchJobPhase.REMOTE_RUNNING
                else "tenable-build-worker-phase-ui-1"
                if phase is BatchJobPhase.BUILD_RUNNING
                else None
            ),
        )
        for position, (phase, status) in enumerate(
            (
                (BatchJobPhase.LEGACY, BatchJobStatus.QUEUED),
                (BatchJobPhase.REMOTE_QUEUED, BatchJobStatus.QUEUED),
                (BatchJobPhase.REMOTE_RUNNING, BatchJobStatus.RUNNING),
                (
                    BatchJobPhase.REMOTE_WAITING_DECISION,
                    BatchJobStatus.WAITING_WAS_DECISION,
                ),
                (BatchJobPhase.READY_FOR_BUILD, BatchJobStatus.QUEUED),
                (BatchJobPhase.BUILD_RUNNING, BatchJobStatus.RUNNING),
                (BatchJobPhase.TERMINAL, BatchJobStatus.COMPLETE),
            ),
            start=1,
        )
    )
    repository.create_batch(
        WebBatch(
            id=batch_id,
            idempotency_key="batch:ui:phases",
            kind="GENERATE_ALL",
            status=BatchStatus.PAUSED,
            options={"requests": []},
        ),
        jobs,
    )
    repository.append_event(
        WebBatchEvent(
            batch_id=batch_id,
            job_id=jobs[4].id,
            event_type="COLLECTION_READY",
            payload={
                "collection_checkpoint_path": str(checkpoint),
                "checkpoint_ready": True,
            },
        )
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
        worker_id="worker-phase-ui",
        remote_runner=lambda job: None,
        build_runner=lambda job: None,
        remote_workers=3,
        start_worker=False,
    )
    try:
        summary = queue.batches_snapshot()[0]
        detail = queue.batch_snapshot(batch_id)
        state_jobs = queue.snapshot()
    finally:
        queue.close()

    assert summary["phase_counts"] == {
        "LEGACY": 1,
        "REMOTE_QUEUED": 1,
        "REMOTE_RUNNING": 1,
        "REMOTE_WAITING_DECISION": 1,
        "READY_FOR_BUILD": 1,
        "BUILD_RUNNING": 1,
        "TERMINAL": 1,
    }
    assert summary["remote_concurrency"] == {"active": 1, "capacity": 3}
    assert summary["build_queue_count"] == 1
    assert summary["build_concurrency"] == {"active": 1, "capacity": 1}
    ready = next(job for job in detail["jobs"] if job["phase"] == "READY_FOR_BUILD")
    assert ready["checkpoint_ready"] is True
    assert ready["remote_ended_at"] == "2026-09-01T12:30:00Z"
    assert next(job for job in state_jobs if job["client_id"] == "client-5")[
        "checkpoint_ready"
    ] is True
    serialized = json.dumps({"detail": detail, "jobs": state_jobs})
    assert "collection_checkpoint_path" not in serialized
    assert str(checkpoint) not in serialized


def test_frontend_exposes_staged_phase_labels_and_unknown_chunk_copy() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "app.css").read_text(encoding="utf-8")

    assert 'id="batch-phase-summary"' in html
    assert 'id="batch-capacity-copy"' in html
    for label in (
        "Coleta remota",
        "Aguardando Tenable/chunks",
        "Pronto para montagem",
        "Montando documento",
        "Concluído",
        "Falhou",
        "0/0 · aguardando a Tenable informar chunks",
    ):
        assert label in javascript
    assert "phase_counts" in javascript
    assert "remote_concurrency" in javascript
    assert "build_queue_count" in javascript
    assert ".batch-phase-summary" in css
