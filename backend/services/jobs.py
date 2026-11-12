"""In-memory job registry so the UI can poll progress. Jobs belong to the user who started them.

Runs in a single API process (render.yaml pins one instance): a job lives only in this process, which is fine because
the result that matters (the prediction and its analysis) is written to the database when the job finishes.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from errors import AppError
from schemas.predictions import AnalysisResult
from services.orchestrator import Orchestrator, Progress

log = logging.getLogger("football_ai.jobs")
ANALYSIS_TIMEOUT_SECONDS = 300


@dataclass
class Job:
    id: str
    match_id: int
    user_id: str
    status: str = "running"
    progress: Progress = field(default_factory=Progress)
    result: Optional[AnalysisResult | dict] = None
    error: Optional[dict] = None
    finished_at: Optional[float] = None

    def to_dict(self) -> dict:
        result = self.result.model_dump() if isinstance(self.result, AnalysisResult) else self.result
        return {"job_id": self.id, "match_id": self.match_id, "status": self.status, "steps": self.progress.as_list(),
                "result": result, "error": self.error}


class JobStore:
    def __init__(self, max_jobs: int = 500, ttl_seconds: float = 1800, max_concurrent: int = 3):
        self._jobs: dict[str, Job] = {}
        self._tasks: set[asyncio.Task] = set()
        self._max, self._ttl = max_jobs, ttl_seconds
        self.limiter = asyncio.Semaphore(max_concurrent)  # protects the Groq and football-data.org rate limits

    def _prune(self) -> None:
        now = time.monotonic()
        for jid in [j.id for j in self._jobs.values() if j.finished_at and now - j.finished_at > self._ttl]:
            del self._jobs[jid]
        for j in sorted((j for j in self._jobs.values() if j.finished_at), key=lambda j: j.finished_at)[: max(0, len(self._jobs) - self._max)]:
            del self._jobs[j.id]  # never evict a running job

    def running_for(self, user_id: str) -> Optional[Job]:
        return next((j for j in self._jobs.values() if j.user_id == user_id and j.status == "running"), None)

    def start(self, orchestrator: Orchestrator, match_id: int, user_id: str,
              on_failure: Optional[Callable[[], Awaitable[None]]] = None) -> Job:
        self._prune()
        job = Job(id=uuid.uuid4().hex[:16], match_id=match_id, user_id=user_id)
        self._jobs[job.id] = job
        task = asyncio.create_task(self._run(orchestrator, job, on_failure))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    def start_cached(self, match_id: int, user_id: str, result: dict) -> Job:
        """A job that is already finished, serving the stored analysis of this match (no LLM calls)."""
        self._prune()
        job = Job(id=uuid.uuid4().hex[:16], match_id=match_id, user_id=user_id, status="done", result=result, finished_at=time.monotonic())
        for step in job.progress.steps.values():
            step.status = "done"
        self._jobs[job.id] = job
        return job

    async def _run(self, orchestrator: Orchestrator, job: Job, on_failure) -> None:
        try:
            async with self.limiter:
                job.result = await asyncio.wait_for(orchestrator.analyze(job.match_id, job.progress), ANALYSIS_TIMEOUT_SECONDS)
            job.status = "done"
        except AppError as exc:
            job.status, job.error = "error", exc.to_dict()["error"]
        except asyncio.TimeoutError:
            job.status, job.error = "error", {"code": "timeout", "message": "The analysis took too long. Please try again.", "agent": None}
        except Exception:  # noqa: BLE001
            log.exception("analysis job %s failed", job.id)
            job.status, job.error = "error", {"code": "internal_error", "message": "Something went wrong. Please try again.", "agent": None}
        finally:
            job.finished_at = time.monotonic()
        if job.status == "error" and on_failure:
            try:
                await on_failure()
            except Exception:  # noqa: BLE001
                log.exception("on_failure hook failed for job %s", job.id)

    def get(self, job_id: str, user_id: Optional[str] = None) -> Optional[Job]:
        job = self._jobs.get(job_id)
        if job is None or (user_id is not None and job.user_id != user_id):
            return None
        return job
