from __future__ import annotations

import queue
import threading
import uuid

from src.pipeline.pipeline_job_runner import PipelineJobRunner


class PipelineJobDispatcher:
    """Single local daemon worker for controlled prototype pipeline jobs."""

    def __init__(self, session_factory, *, runner: PipelineJobRunner | None = None):
        self.runner = runner or PipelineJobRunner(session_factory)
        self._queue: queue.Queue[uuid.UUID | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def submit(self, job_id: uuid.UUID | str) -> None:
        normalized = uuid.UUID(str(job_id))
        self._ensure_started()
        self._queue.put(normalized)

    def _ensure_started(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._consume,
                name="erp-assistant-pipeline-worker",
                daemon=True,
            )
            self._thread.start()

    def _consume(self) -> None:
        while True:
            job_id = self._queue.get()
            try:
                if job_id is None:
                    return
                try:
                    self.runner.run(job_id)
                except Exception:
                    # El runner persiste errores esperados; una falla inesperada
                    # no debe matar el worker y bloquear los siguientes jobs.
                    continue
            finally:
                self._queue.task_done()

    def shutdown(self) -> None:
        thread = self._thread
        if thread is None or not thread.is_alive():
            return
        self._queue.put(None)
