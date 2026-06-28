"""Celery application + Beat schedule.

- **Broker** (Redis): the queue tasks are pushed onto and pulled from.
- **Result backend** (Redis): where task return values/states are stored.
- **Beat**: a scheduler process that, on an interval, enqueues the periodic
  "sweep" task. Beat only *schedules*; the workers do the work. That decoupling
  of "when to run" from "who runs it" is the core async-jobs lesson of Phase 2.

`include=[...]` tells the worker which module to import tasks from, which avoids
a circular import between this module and workers/tasks.py.
"""

from celery import Celery

from core.settings import get_settings

settings = get_settings()

celery_app = Celery(
    "deal_hunter",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["workers.tasks"],
)

celery_app.conf.update(
    timezone="UTC",
    task_track_started=True,
    # Periodic schedule: every N seconds, fan out a refresh sweep.
    beat_schedule={
        "enqueue-due-refreshes": {
            "task": "workers.tasks.enqueue_due_refreshes",
            "schedule": float(settings.refresh_interval_seconds),
        }
    },
)
