"""Domain signal for cooperative interruption of a local execution."""

from __future__ import annotations


class ExecutionInterruptedError(RuntimeError):
    """Raised when a persisted local stop request interrupts a run."""

    def __init__(
        self,
        message: str = "Execucao interrompida por solicitacao local.",
        *,
        export_uuid: str | None = None,
        checkpoint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.export_uuid = export_uuid
        self.checkpoint = checkpoint


__all__ = ["ExecutionInterruptedError"]
