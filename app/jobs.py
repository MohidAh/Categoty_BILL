"""Async job tracking for long-running operations (PDF upload, AI extraction).

Jobs are stored in-memory (lost on restart). Each job has:
- id (uuid)
- type (e.g., 'upload')
- status: 'pending' | 'running' | 'done' | 'error'
- progress: 0..100
- stage: human-readable description of current step
- events: list of {ts, stage, message, level} for SSE streaming
- result: final payload on success
- error: error message on failure
- bill_id: associated bill (created upfront so user can navigate even mid-job)
"""
import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

JOBS: dict[str, "Job"] = {}


@dataclass
class JobEvent:
    ts: float
    stage: str
    message: str
    level: str = "info"  # info | success | warning | error
    progress: int | None = None


@dataclass
class Job:
    id: str
    type: str
    status: str = "pending"  # pending | running | done | error
    progress: int = 0
    stage: str = "queued"
    events: list[JobEvent] = field(default_factory=list)
    result: Any = None
    error: str | None = None
    bill_id: int | None = None
    created_at: float = field(default_factory=time.time)
    _subscribers: list = field(default_factory=list)

    def emit(self, stage: str, message: str, level: str = "info", progress: int | None = None):
        ev = JobEvent(time.time(), stage, message, level, progress)
        self.events.append(ev)
        self.stage = stage
        if progress is not None:
            self.progress = progress
        # Notify SSE subscribers
        for q in list(self._subscribers):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                pass  # drop if subscriber is slow

    def subscribe(self) -> asyncio.Queue:
        q = asyncio.Queue(maxsize=100)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        if q in self._subscribers:
            self._subscribers.remove(q)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "type": self.type, "status": self.status,
            "progress": self.progress, "stage": self.stage,
            "error": self.error, "bill_id": self.bill_id,
            "result": self.result,
            "events": [{"ts": e.ts, "stage": e.stage, "message": e.message,
                        "level": e.level, "progress": e.progress} for e in self.events[-50:]],
        }


def create_job(job_type: str, bill_id: int | None = None) -> Job:
    jid = uuid.uuid4().hex[:12]
    job = Job(id=jid, type=job_type, bill_id=bill_id)
    JOBS[jid] = job
    return job


def get_job(jid: str) -> Job | None:
    return JOBS.get(jid)


def cleanup_old_jobs(max_age_sec: int = 3600):
    """Remove finished jobs older than max_age_sec."""
    now = time.time()
    to_remove = [jid for jid, j in JOBS.items()
                 if j.status in ("done", "error") and now - j.created_at > max_age_sec]
    for jid in to_remove:
        JOBS.pop(jid, None)
