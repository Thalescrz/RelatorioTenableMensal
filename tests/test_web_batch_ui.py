from __future__ import annotations

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

