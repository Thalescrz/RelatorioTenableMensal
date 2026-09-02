"""Phase-aware worker pools whose durable state lives in a batch repository."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from typing import Any
from uuid import UUID

from tenable_reports.application.web_batches import (
    BatchJobResult,
    WebBatchRepository,
    normalize_claim_phases,
)
from tenable_reports.domain.web_batches import (
    BatchJobPhase,
    BatchJobStatus,
    WebBatchJob,
)


DurableRunner = Callable[[WebBatchJob], BatchJobResult]
DurableResultHandler = Callable[[WebBatchJob, BatchJobResult], None]


class DurableWorkerPool:
    """Run a bounded set of workers that claim only the configured phases."""

    def __init__(
        self,
        *,
        repository: WebBatchRepository,
        runner: DurableRunner,
        worker_prefix: str,
        phases: Sequence[BatchJobPhase],
        workers: int,
        poll_interval: float = 0.25,
        start_workers: bool = True,
        reconcile: bool = False,
        result_handler: DurableResultHandler | None = None,
    ) -> None:
        self.repository = repository
        self.runner = runner
        self.worker_prefix = str(worker_prefix or "").strip()
        if not self.worker_prefix:
            raise ValueError("worker_prefix nao pode ser vazio.")
        self.phases = normalize_claim_phases(phases)
        self.worker_count = int(workers)
        if self.worker_count < 1:
            raise ValueError("workers deve ser positivo.")
        self.poll_interval = max(0.01, float(poll_interval))
        self.result_handler = result_handler or (
            lambda job, result: self.repository.complete_job(job.id, result)
        )
        self.worker_ids = tuple(
            f"{self.worker_prefix}-{index}"
            for index in range(1, self.worker_count + 1)
        )
        self._stopping = threading.Event()
        self._wake_event = threading.Event()
        self._lock = threading.RLock()
        self._workers: dict[str, threading.Thread] = {}
        self._started = False
        if reconcile:
            self.repository.reconcile_abandoned_jobs(
                active_worker_ids=set(self.worker_ids)
            )
        if start_workers:
            self.start()

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            if self._stopping.is_set():
                raise RuntimeError("O pool encerrado nao pode ser reiniciado.")
            self._started = True
            for worker_id in self.worker_ids:
                worker = threading.Thread(
                    target=self._work,
                    args=(worker_id,),
                    name=worker_id,
                    daemon=True,
                )
                self._workers[worker_id] = worker
                worker.start()
        self.wake()

    def snapshot(self, batch_id: UUID) -> dict[str, Any]:
        batch = self.repository.get_batch(batch_id)
        if batch is None:
            raise KeyError("Lote nao encontrado.")
        return {
            "batch": batch,
            "jobs": self.repository.list_batch_jobs(batch_id),
            "events": self.repository.list_events(batch_id),
        }

    def capacity_snapshot(self) -> dict[str, Any]:
        with self._lock:
            live_workers = sum(
                worker.is_alive() for worker in self._workers.values()
            )
        return {
            "worker_prefix": self.worker_prefix,
            "phases": tuple(phase.value for phase in self.phases),
            "workers": self.worker_count,
            "live_workers": live_workers,
            "idle_poll_interval_seconds": self.poll_interval,
        }

    def wake(self) -> None:
        self._wake_event.set()

    def wait_until_idle(self, *, timeout: float) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout))
        worker_ids = set(self.worker_ids)
        while time.monotonic() <= deadline:
            active = False
            for batch in self.repository.list_batches(limit=500):
                for job in self.repository.list_batch_jobs(batch.id):
                    if (
                        job.status is BatchJobStatus.QUEUED
                        and job.phase in self.phases
                    ) or (
                        job.status
                        in {
                            BatchJobStatus.RUNNING,
                            BatchJobStatus.INTERRUPT_REQUESTED,
                        }
                        and job.worker_id in worker_ids
                    ):
                        active = True
                        break
                if active:
                    break
            if not active:
                return True
            time.sleep(min(self.poll_interval, 0.05))
        return False

    def close(self) -> None:
        self._signal_stop()
        deadline = time.monotonic() + 5.0
        with self._lock:
            workers = tuple(self._workers.values())
        for worker in workers:
            remaining = max(0.0, deadline - time.monotonic())
            worker.join(timeout=remaining)
        alive = tuple(worker.name for worker in workers if worker.is_alive())
        if alive:
            raise RuntimeError(
                "Workers duraveis nao encerraram: " + ", ".join(alive)
            )

    def _signal_stop(self) -> None:
        self._stopping.set()
        self._wake_event.set()

    def _work(self, worker_id: str) -> None:
        while not self._stopping.is_set():
            job = self.repository.claim_next_job(
                worker_id=worker_id,
                phases=self.phases,
            )
            if job is None:
                self._wake_event.wait(self.poll_interval)
                self._wake_event.clear()
                continue
            try:
                result = self.runner(job)
            except Exception:
                result = BatchJobResult(
                    status=BatchJobStatus.FAILED,
                    exit_code=1,
                    error_code="UNEXPECTED",
                    error_message="Falha operacional sem detalhe.",
                )
            try:
                self.result_handler(job, result)
            except Exception:
                self.repository.complete_job(
                    job.id,
                    BatchJobResult(
                        status=BatchJobStatus.FAILED,
                        exit_code=1,
                        error_code="PHASE_TRANSITION_FAILED",
                        error_message="Falha ao registrar a transicao de fase.",
                    ),
                )


class DurableWorkerPoolGroup:
    """Coordinate sibling pools and reconcile their complete worker set once."""

    def __init__(
        self,
        *,
        repository: WebBatchRepository,
        pools: Sequence[DurableWorkerPool],
        start_workers: bool = True,
    ) -> None:
        self.repository = repository
        self.pools = tuple(pools)
        if not self.pools:
            raise ValueError("Ao menos um pool duravel e obrigatorio.")
        if any(pool.repository is not repository for pool in self.pools):
            raise ValueError("Todos os pools devem usar o mesmo repositorio.")
        worker_ids = tuple(
            worker_id
            for pool in self.pools
            for worker_id in pool.worker_ids
        )
        if len(set(worker_ids)) != len(worker_ids):
            raise ValueError("IDs de workers duraveis devem ser unicos.")
        self.worker_ids = worker_ids
        self.repository.reconcile_abandoned_jobs(
            active_worker_ids=set(self.worker_ids)
        )
        if start_workers:
            self.start()

    def start(self) -> None:
        for pool in self.pools:
            pool.start()

    def wake(self) -> None:
        for pool in self.pools:
            pool.wake()

    def wait_until_idle(self, *, timeout: float) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout))
        for pool in self.pools:
            remaining = max(0.0, deadline - time.monotonic())
            if not pool.wait_until_idle(timeout=remaining):
                return False
        return True

    def capacity_snapshot(self) -> tuple[dict[str, Any], ...]:
        return tuple(pool.capacity_snapshot() for pool in self.pools)

    def close(self) -> None:
        errors: list[RuntimeError] = []
        for pool in self.pools:
            pool._signal_stop()
        for pool in self.pools:
            try:
                pool.close()
            except RuntimeError as exc:
                errors.append(exc)
        if errors:
            raise errors[0]


class DurableJobQueue(DurableWorkerPool):
    """Compatibility facade for the original single LEGACY worker."""

    def __init__(
        self,
        *,
        repository: WebBatchRepository,
        runner: DurableRunner,
        worker_id: str,
        poll_interval: float = 0.25,
        start_worker: bool = True,
    ) -> None:
        normalized_worker_id = str(worker_id or "").strip()
        if not normalized_worker_id:
            raise ValueError("worker_id nao pode ser vazio.")
        self.worker_id = normalized_worker_id
        super().__init__(
            repository=repository,
            runner=runner,
            worker_prefix=normalized_worker_id,
            phases=(BatchJobPhase.LEGACY,),
            workers=1,
            poll_interval=poll_interval,
            start_workers=start_worker,
            reconcile=True,
        )


__all__ = [
    "DurableJobQueue",
    "DurableRunner",
    "DurableWorkerPool",
    "DurableWorkerPoolGroup",
]
