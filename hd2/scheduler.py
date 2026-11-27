"""Minimal single-thread scheduler.

The v1 app polled the war API with a hand-written `while True: fetch; sleep()`
loop.  v2 needs several jobs with different cadences (war 60s, note reminders
30s, events manual-only, providers on demand), so the loop is replaced by this
tiny scheduler with the same guarantees:

* **one thread**, jobs run sequentially - no concurrent network calls sneaking in;
* an exception in a job never kills the loop, it only backs that job off
  (exponential, capped), because a buggy provider must not stop the clock;
* intervals may be callables, so `poll_seconds` changes apply on the next tick
  without re-registering anything;
* `snapshot()` exposes what ran, how long it took and why it failed - the
  diagnostics surface for the UI and for HANDOVER debugging.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

LOG = logging.getLogger(__name__)

Number = float | int
Interval = Number | Callable[[], Number]
JobFn = Callable[[], Any]


@dataclass
class Job:
    """One scheduled callable.  Mutated only while the scheduler owns the lock."""

    name: str
    interval: Interval
    fn: JobFn
    run_now: bool = False
    backoff_min: float = 30.0
    backoff_max: float = 300.0
    first_delay: float = 0.0
    failures: int = 0
    runs: int = 0
    next_at: float = 0.0
    last_started: float = 0.0
    last_duration: float = 0.0
    last_error: str = ""

    def interval_seconds(self) -> float:
        raw = self.interval() if callable(self.interval) else self.interval
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = 60.0
        return max(1.0, value)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "interval": round(self.interval_seconds(), 1),
            "runs": self.runs,
            "failures": self.failures,
            "last_started": self.last_started,
            "last_duration_ms": int(self.last_duration * 1000),
            "next_at": self.next_at,
            "last_error": self.last_error,
        }


class Scheduler:
    """Sequential, tolerant job runner (one background thread)."""

    def __init__(self, tick: float = 1.0) -> None:
        self.tick = max(0.05, float(tick))
        self._jobs: list[Job] = []
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- registration ----------------------------------------------------
    def add(
        self,
        name: str,
        interval: Interval,
        fn: JobFn,
        *,
        run_now: bool = False,
        backoff: tuple[float, float] = (30.0, 300.0),
        first_delay: float = 0.0,
    ) -> Job:
        """Register (or replace) a job.  Re-adding the same name keeps its stats."""
        with self._lock:
            for job in self._jobs:
                if job.name == name:
                    job.interval = interval
                    job.fn = fn
                    return job
            now = time.time()
            job = Job(
                name=name,
                interval=interval,
                fn=fn,
                run_now=run_now,
                backoff_min=float(backoff[0]),
                backoff_max=float(backoff[1]),
                first_delay=float(first_delay),
            )
            delay = first_delay if first_delay else (0.0 if run_now else job.interval_seconds())
            job.next_at = now + delay
            self._jobs.append(job)
            return job

    def remove(self, name: str) -> bool:
        with self._lock:
            before = len(self._jobs)
            self._jobs = [job for job in self._jobs if job.name != name]
            return len(self._jobs) != before

    def jobs(self) -> list[Job]:
        with self._lock:
            return list(self._jobs)

    def snapshot(self) -> list[dict]:
        return [job.as_dict() for job in self.jobs()]

    # -- execution -------------------------------------------------------
    def tick_once(self, now: float | None = None) -> int:
        """Run every due job once, synchronously.  Returns how many ran."""
        now = time.time() if now is None else float(now)
        due: list[Job] = []
        with self._lock:
            for job in self._jobs:
                if job.next_at <= now:
                    due.append(job)
        for job in due:
            self._run(job)
        return len(due)

    def _run(self, job: Job) -> None:
        started = time.time()
        try:
            job.fn()
        except Exception as exc:  # noqa: BLE001 - a job must never kill the loop
            job.failures += 1
            job.last_error = f"{type(exc).__name__}: {exc}"
            delay = min(job.backoff_max, job.backoff_min * (2 ** (job.failures - 1)))
            LOG.warning("Scheduler job %r failed (%s); retry in %.0fs", job.name, job.last_error, delay)
            with self._lock:
                job.last_started = started
                job.last_duration = time.time() - started
                job.next_at = time.time() + delay
            return
        elapsed = time.time() - started
        with self._lock:
            job.runs += 1
            job.failures = 0
            job.last_error = ""
            job.last_started = started
            job.last_duration = elapsed
            job.next_at = time.time() + job.interval_seconds()

    # -- lifecycle -------------------------------------------------------
    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self.run, name="hd2-scheduler", daemon=True)
        self._thread.start()
        LOG.info("Scheduler started with %d job(s)", len(self.jobs()))
        return True

    def run(self) -> None:
        """Blocking loop; returns when :meth:`stop` is called."""
        while not self._stop.is_set():
            try:
                self.tick_once()
            except Exception as exc:  # noqa: BLE001 - defensive, tick_once already isolates jobs
                LOG.warning("Scheduler tick error: %s", exc)
            self._stop.wait(self.tick)

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout)
        self._thread = None
