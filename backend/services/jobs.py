"""In-memory job registry so the UI can poll progress. Nothing is persisted."""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Optional

from errors import AppError
from schemas.predictions import AnalysisResult
from services.orchestrator import Orchestrator, Progress


@dataclass
class Job:
    id: str
    match_id: int
    status: str = "running"
    progress: Progress = field(default_factory=Progress)
    result: Optional[AnalysisResult] = None
    error: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "job_id": self.id,
            "match_id": self.match_id,
            "status": self.status,
            "steps": self.progress.as_list(),
            "result": self.result.model_dump() if self.result else None,
            "error": self.error,
        }


class JobStore:
    def __init__(self, max_jobs: int = 20):
        self._jobs: dict[str, Job] = {}
        self._tasks: set[asyncio.Task] = set()
        self._max = max_jobs

    def start(self, orchestrator: Orchestrator, match_id: int) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], match_id=match_id)
        self._jobs[job.id] = job
        while len(self._jobs) > self._max:
            self._jobs.pop(next(iter(self._jobs)))
        task = asyncio.create_task(self._run(orchestrator, job))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    async def _run(self, orchestrator: Orchestrator, job: Job) -> None:
        try:
            job.result = await orchestrator.analyze(job.match_id, job.progress)
            job.status = "done"
        except AppError as exc:
            job.status, job.error = "error", exc.to_dict()["error"]
        except Exception as exc:  # noqa: BLE001
            job.status = "error"
            job.error = {"code": "internal_error", "message": f"Unexpected error: {exc}", "agent": None}

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)
