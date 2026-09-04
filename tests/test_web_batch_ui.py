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
        encoding="utf-8",
    )
    return json.loads(completed.stdout)


def _run_report_request_guard_script(source: str) -> object:
    script_path = STATIC / "report_request_guard.js"
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


def _run_dashboard_refresh_script(source: str) -> object:
    script_path = STATIC / "dashboard_refresh.js"
    completed = subprocess.run(
        [
            "node",
            "-e",
            (
                f"const helpers = require({json.dumps(str(script_path))});"
                "Promise.resolve().then(async () => {"
                f"return await (async () => {{ {source} }})();"
                "}).then(result => process.stdout.write(JSON.stringify(result)))"
                ".catch(error => { console.error(error); process.exit(1); });"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _run_batch_retryability_script(source: str) -> object:
    script_path = STATIC / "batch_retryability.js"
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
        encoding="utf-8",
    )
    return json.loads(completed.stdout)


def test_retryability_view_distinguishes_effective_and_recorded_classification() -> None:
    result = _run_batch_retryability_script(
        "return ["
        "helpers.retryabilityView({"
        "retryable: true, effective_error_code: 'TENABLE_TEMPORARY', "
        "recorded_error_code: 'UNEXPECTED', retryability_reason: 'Falha transitória.'"
        "}),"
        "helpers.retryabilityView({"
        "retryable: false, effective_error_code: 'PROFILE_INVALID', "
        "recorded_error_code: 'PROFILE_INVALID', retryability_reason: 'Corrija o perfil.'"
        "}),"
        "helpers.retryabilityView({retryable: null})"
        "];"
    )

    assert result == [
        {
            "visible": True,
            "label": "Retentável",
            "tone": "retryable",
            "effectiveCode": "TENABLE_TEMPORARY",
            "recordedCopy": "Registrado originalmente como UNEXPECTED.",
            "reason": "Falha transitória.",
        },
        {
            "visible": True,
            "label": "Não retentável",
            "tone": "non-retryable",
            "effectiveCode": "PROFILE_INVALID",
            "recordedCopy": "",
            "reason": "Corrija o perfil.",
        },
        {
            "visible": False,
            "label": "",
            "tone": "",
            "effectiveCode": "",
            "recordedCopy": "",
            "reason": "",
        },
    ]


def test_refresh_coordinator_coalesces_periodic_requests() -> None:
    result = _run_dashboard_refresh_script(
        "const calls = [];"
        "let releaseFirst;"
        "const first = new Promise(resolve => { releaseFirst = resolve; });"
        "const coordinator = helpers.createRefreshCoordinator({"
        "load: () => { calls.push('load'); return first; },"
        "apply: value => calls.push(`apply:${value.revision}`),"
        "onError: error => calls.push(`error:${error.message}`),"
        "});"
        "const a = coordinator.refresh();"
        "const b = coordinator.refresh();"
        "releaseFirst({revision: 1});"
        "await Promise.all([a, b]);"
        "return {calls, running: coordinator.isRunning()};"
    )

    assert result == {"calls": ["load", "apply:1"], "running": False}


def test_refresh_coordinator_runs_one_requested_follow_up_in_series() -> None:
    result = _run_dashboard_refresh_script(
        "const calls = [];"
        "let releaseFirst;"
        "const first = new Promise(resolve => { releaseFirst = resolve; });"
        "const coordinator = helpers.createRefreshCoordinator({"
        "load: () => { calls.push('load'); return calls.length === 1 "
        "? first : Promise.resolve({revision: 2}); },"
        "apply: value => calls.push(`apply:${value.revision}`),"
        "onError: error => calls.push(`error:${error.message}`),"
        "});"
        "const a = coordinator.refresh();"
        "const b = coordinator.refresh({ensureAfterCurrent: true});"
        "const c = coordinator.refresh({ensureAfterCurrent: true});"
        "releaseFirst({revision: 1});"
        "await Promise.all([a, b, c]);"
        "return {calls, running: coordinator.isRunning()};"
    )

    assert result == {
        "calls": ["load", "apply:1", "load", "apply:2"],
        "running": False,
    }


def test_refresh_coordinator_reports_error_and_releases_running_state() -> None:
    result = _run_dashboard_refresh_script(
        "const calls = [];"
        "const coordinator = helpers.createRefreshCoordinator({"
        "load: () => Promise.reject(new Error('indisponivel'))," 
        "apply: () => calls.push('apply'),"
        "onError: error => calls.push(`error:${error.message}`),"
        "});"
        "await coordinator.refresh();"
        "return {calls, running: coordinator.isRunning()};"
    )

    assert result == {"calls": ["error:indisponivel"], "running": False}


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


def test_client_selection_maps_only_active_jobs_as_conflicts() -> None:
    result = _run_selection_script(
        "return helpers.conflictingJobsByClient(["
        "{ job_id: 'job-a', client_id: 'a', status: 'FAILED' },"
        "{ job_id: 'job-b', client_id: 'b', status: 'RUNNING', batch_id: 'batch-b' },"
        "{ job_id: 'job-c', client_id: 'c', status: 'WAITING_WAS_DECISION', batch_id: 'batch-c' }"
        "]);"
    )

    assert result == {
        "b": {
            "job_id": "job-b",
            "client_id": "b",
            "status": "RUNNING",
            "batch_id": "batch-b",
        },
        "c": {
            "job_id": "job-c",
            "client_id": "c",
            "status": "WAITING_WAS_DECISION",
            "batch_id": "batch-c",
        },
    }


def test_responsible_analyst_draft_preserves_explicit_empty_value() -> None:
    assert _run_selection_script(
        "return ["
        "helpers.resolveResponsibleAnalystValue(undefined, 'ana-1'),"
        "helpers.resolveResponsibleAnalystValue('', 'ana-1'),"
        "helpers.resolveResponsibleAnalystValue('ana-2', 'ana-1')"
        "];"
    ) == ["ana-1", "", "ana-2"]


def test_client_update_merges_saved_fields_without_losing_operational_state() -> None:
    assert _run_selection_script(
        "return helpers.mergeSavedClient(["
        "{ client_id: 'a', responsible_analyst_id: null, job: { status: 'RUNNING' } }"
        "], { client_id: 'a', responsible_analyst_id: 'analyst-1' });"
    ) == [
        {
            "client_id": "a",
            "responsible_analyst_id": "analyst-1",
            "job": {"status": "RUNNING"},
        }
    ]


def test_client_save_confirms_immediately_without_waiting_for_full_state() -> None:
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    start = javascript.index('clientForm.addEventListener("submit"')
    end = javascript.index('$("#run-form").addEventListener', start)
    submit = javascript[start:end]

    assert 'button.textContent = "Salvando' in submit
    assert "mergeSavedClient" in submit
    assert "await refresh()" not in submit


def test_run_submission_confirms_without_waiting_for_full_state() -> None:
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    start = javascript.index('$("#run-form").addEventListener')
    end = javascript.index('$("#confirm-component-retry")', start)
    submit = javascript[start:end]

    assert '$("#run-dialog").close()' in submit
    assert "await refresh()" not in submit
    assert "ensureAfterCurrent: true" in submit
    assert "void refresh(" in submit


def test_report_request_guard_rejects_a_late_response_from_previous_client() -> None:
    assert _run_report_request_guard_script(
        "const guard = helpers.createLatestRequestGuard();"
        "const first = guard.begin('client-a');"
        "const second = guard.begin('client-b');"
        "return [guard.isCurrent(first), guard.isCurrent(second), "
        "guard.currentClientId()];"
    ) == [False, True, "client-b"]


def test_frontend_uses_request_guard_when_loading_client_reports() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")

    assert html.index("report_request_guard.js") < html.index("app.js")
    assert "reportRequestGuard.begin(clientId)" in javascript
    assert "reportRequestGuard.isCurrent(reportRequest)" in javascript


def test_frontend_exposes_analyst_filters_selection_modal_and_management() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")

    for element_id in (
        "dashboard-analyst-filter",
        "dashboard-status-filter",
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


def test_frontend_exposes_partial_status_and_status_filters() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")

    for label in (
        "Todos os status",
        "Em andamento",
        "Concluídos",
        "Parcialmente concluídos",
        "Falhos",
        "Aguardando decisão",
        "Interrompidos",
        "Ainda não gerados",
    ):
        assert label in html
    assert 'PARTIALLY_COMPLETE' in javascript
    assert 'Parcialmente concluído' in javascript
    assert 'state.statusFilter' in javascript
    assert 'matchesStatusFilter' in javascript
    assert 'data-open-alert-run' in javascript
    assert 'report-set-target' in javascript
    assert 'scrollIntoView' in javascript


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
        "Tentar falhas, parciais e interrompidos",
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


def test_frontend_exposes_recent_batch_selector_and_client_detail() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    assert 'id="batch-select"' in html
    assert 'id="batch-client-dialog"' in html
    assert "data-open-batch-clients" in javascript
    assert "/api/batches/" in javascript
    assert "was_attempts" in javascript
    assert "data-copy-vm-uuid" in javascript
    assert "Verificar export preservado" in javascript
    assert "job aceito; a Tenable ainda não anunciou" in javascript
    assert "batchKindLabel(item.kind)" in javascript
    assert "item.id.slice(0, 8)" in javascript
    assert ".batch-client-row" in css


def test_derived_batch_action_selects_the_new_batch_before_refresh() -> None:
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    start = javascript.index("async function runBatchAction")
    end = javascript.index("function renderBatches", start)
    action = javascript[start:end]

    assert "const response = await api" in action
    assert "response.batch?.id" in action
    assert "state.selectedBatchId = derivedBatchId" in action
    assert action.index("state.selectedBatchId = derivedBatchId") < action.index(
        "await refresh()"
    )


def test_generate_all_uses_per_client_conflicts_instead_of_global_batch_lock() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")

    assert "Use os controles do lote antes de iniciar outro" not in javascript
    assert "conflictingJobsByClient" in javascript
    assert "data-stop-conflicting-job" in javascript
    assert "/api/jobs/${encodeURIComponent(jobId)}/stop" in javascript
    assert "Coletas remotas em paralelo" in html


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
        'actions.push(["retry-incomplete", "Tentar falhas, parciais e interrompidos", "primary"]);'
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
                error_code=(
                    "TENABLE_TEMPORARY"
                    if status is BatchJobStatus.FAILED
                    else None
                ),
                error_message=(
                    "Tempo maximo excedido aguardando o export VM."
                    if status is BatchJobStatus.FAILED
                    else None
                ),
                payload=(
                    {
                        "run_id": "published-partial-run",
                        "retryable_components": ["WAS"],
                    }
                    if status is BatchJobStatus.PARTIALLY_COMPLETE
                    else {}
                ),
                run_id=(
                    "published-partial-run"
                    if status is BatchJobStatus.PARTIALLY_COMPLETE
                    else None
                ),
            )
            for position, status in enumerate(
                (
                    BatchJobStatus.COMPLETE,
                    BatchJobStatus.COMPLETE_WITH_WARNINGS,
                    BatchJobStatus.PARTIALLY_COMPLETE,
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

    assert summary["total_count"] == 6
    assert summary["completed_count"] == 2
    assert summary["partial_count"] == 1
    assert summary["failed_count"] == 1
    assert summary["interrupted_count"] == 1
    assert summary["cancelled_count"] == 1
    assert summary["retryable_count"] == 4
    assert summary["non_retryable_count"] == 0
    assert summary["progress_percent"] == 100


def test_batch_snapshot_exposes_effective_and_recorded_retryability(
    tmp_path: Path,
) -> None:
    repository = InMemoryWebBatchRepository()
    batch_id = UUID(int=1250)
    repository.create_batch(
        WebBatch(
            id=batch_id,
            idempotency_key="batch:ui:effective-retryability",
            kind="GENERATE_ALL",
            status=BatchStatus.COMPLETE_WITH_FAILURES,
            options={"requests": []},
        ),
        (
            WebBatchJob(
                id=UUID(int=1251),
                batch_id=batch_id,
                client_id="transient-client",
                position=1,
                status=BatchJobStatus.FAILED,
                attempt_number=1,
                error_code="UNEXPECTED",
                error_message="Export VM ficou sem progresso por 2598 segundos.",
            ),
            WebBatchJob(
                id=UUID(int=1252),
                batch_id=batch_id,
                client_id="definitive-client",
                position=2,
                status=BatchJobStatus.FAILED,
                attempt_number=1,
                error_code="PROFILE_INVALID",
                error_message="Perfil do cliente invalido.",
            ),
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
        worker_id="worker-ui-effective-retryability",
        start_worker=False,
    )
    try:
        summary = queue.batches_snapshot()[0]
        detail = queue.batch_snapshot(batch_id)
    finally:
        queue.close()

    assert summary["retryable_count"] == 1
    assert summary["non_retryable_count"] == 1
    jobs = {job["client_id"]: job for job in detail["jobs"]}
    assert jobs["transient-client"]["recorded_error_code"] == "UNEXPECTED"
    assert jobs["transient-client"]["effective_error_code"] == "TENABLE_TEMPORARY"
    assert jobs["transient-client"]["retryable"] is True
    assert jobs["definitive-client"]["recorded_error_code"] == "PROFILE_INVALID"
    assert jobs["definitive-client"]["effective_error_code"] == "PROFILE_INVALID"
    assert jobs["definitive-client"]["retryable"] is False


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
