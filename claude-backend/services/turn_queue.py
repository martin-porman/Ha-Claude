"""Detached per-session turn runner.

A turn runs in a background thread that outlives the HTTP request that started
it, so closing the tab, switching chat or losing the connection never kills a
running answer — the client simply re-attaches to the event log.

Prompts sent while a turn is running are queued for the same session and run in
order, the way the Claude Code CLI queues input, instead of being dropped.

State lives in ``RUNS`` keyed by session id.  Each ``Run`` keeps an append-only
``events`` list; tails replay it from an index and then follow it live.
"""

import logging
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional

from services import turn_context

logger = logging.getLogger(__name__)

# How long a finished run's event log stays replayable (seconds).
RUN_RETENTION = 300


class Run:
    """One session's queue, worker and append-only event log."""

    def __init__(self) -> None:
        self.pending: deque = deque()
        self.events: List[Dict[str, Any]] = []
        self.worker: Optional[threading.Thread] = None
        self.finished = True          # no worker alive and queue drained
        self.finished_at = 0.0
        self.seq = 0                  # turns started in this run
        self.cond = threading.Condition()

    # -- producer side -----------------------------------------------------
    def emit(self, event: Dict[str, Any]) -> None:
        with self.cond:
            self.events.append(event)
            self.cond.notify_all()

    def snapshot(self) -> Dict[str, Any]:
        with self.cond:
            # Index of the turn currently in flight, so a re-attaching client
            # replays only that turn and not ones already in saved history.
            current_from = 0
            for i in range(len(self.events) - 1, -1, -1):
                if self.events[i].get("type") == "turn_start":
                    current_from = i
                    break
            return {
                "running": not self.finished,
                "pending": len(self.pending),
                "events": len(self.events),
                "turn": self.seq,
                "current_from": current_from,
            }


RUNS: Dict[str, Run] = {}
_LOCK = threading.Lock()


def _reap_locked() -> None:
    """Drop finished runs whose retention window has passed. Caller holds _LOCK."""
    now = time.monotonic()
    for sid in [s for s, r in RUNS.items()
                if r.finished and r.finished_at and now - r.finished_at > RUN_RETENTION]:
        RUNS.pop(sid, None)


def status(session_id: str) -> Dict[str, Any]:
    with _LOCK:
        _reap_locked()
        run = RUNS.get(session_id)
    return run.snapshot() if run else {
        "running": False, "pending": 0, "events": 0, "turn": 0, "current_from": 0,
    }


def submit(session_id: str, job: Dict[str, Any]) -> Dict[str, Any]:
    """Queue a prompt and make sure a worker is running.

    Returns ``{"queued": bool, "from": int}`` — ``from`` is the event index a
    tail should start at to see this prompt and everything after it.
    """
    with _LOCK:
        _reap_locked()
        run = RUNS.get(session_id)
        if run is None:
            run = Run()
            RUNS[session_id] = run
        with run.cond:
            if run.finished:
                # Previous turn's log is spent; start a clean one.
                run.events = []
                run.seq = 0
            start = len(run.events)
            queued = run.worker is not None
            run.pending.append(job)
            run.finished = False
            run.finished_at = 0.0
            if run.worker is None:
                run.worker = threading.Thread(
                    target=_work, args=(session_id, run), daemon=True,
                    name=f"amira-turn-{session_id}",
                )
                run.worker.start()
    return {"queued": queued, "from": start}


def abort(session_id: str) -> bool:
    """Ask the running turn to stop and drop anything still queued."""
    import api
    with _LOCK:
        run = RUNS.get(session_id)
    api.abort_streams[session_id] = True
    # Interrupt the CLI directly: the worker may be parked inside the provider
    # waiting on a read, where a flag alone would never be noticed.
    turn_context.kill(session_id)
    if run is None:
        return False
    with run.cond:
        run.pending.clear()
        run.cond.notify_all()
    return True


def _work(session_id: str, run: Run) -> None:
    import api

    while True:
        with run.cond:
            if not run.pending:
                run.worker = None
                run.finished = True
                run.finished_at = time.monotonic()
                run.events.append({"type": "idle"})
                run.cond.notify_all()
                return
            job = run.pending.popleft()
            run.seq += 1
            seq = run.seq

        api.abort_streams[session_id] = False
        turn_context.set_session(session_id)
        run.emit({"type": "turn_start", "seq": seq, "message": job.get("message", "")})

        aborted = False
        gen = None
        try:
            gen = api.stream_chat_with_ai(
                job.get("message", ""),
                session_id,
                job.get("image"),
                read_only=job.get("read_only", False),
                voice_mode=job.get("voice_mode", False),
                req_language=job.get("language"),
            )
            for event in gen:
                run.emit(event)
                if api.abort_streams.get(session_id):
                    aborted = True
                    break
        except Exception as exc:  # noqa: BLE001 - surfaced to the client
            logger.error(
                f"Turn failed for session {session_id}: {type(exc).__name__}: {exc}",
                extra={"context": "REQUEST"},
            )
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}", extra={"context": "REQUEST"})
            run.emit({"type": "error", "message": str(exc)})
        finally:
            # Closing the generator unwinds stream_chat_with_ai and the provider
            # generator underneath it, whose `finally` kills the CLI subprocess.
            if gen is not None:
                try:
                    gen.close()
                except Exception:
                    pass

        if aborted:
            api.abort_streams[session_id] = False
            with run.cond:
                run.pending.clear()
        run.emit({"type": "turn_done", "seq": seq, "aborted": aborted})


def tail(session_id: str, start: int = 0):
    """Yield SSE frames: replay ``events[start:]`` then follow until idle."""
    import json as _json

    with _LOCK:
        run = RUNS.get(session_id)
    if run is None:
        yield f"data: {_json.dumps({'type': 'idle'})}\n\n"
        return

    index = max(0, start)
    while True:
        with run.cond:
            while index >= len(run.events) and not run.finished:
                if not run.cond.wait(timeout=10):
                    break
            batch = run.events[index:]
            index += len(batch)
            done = run.finished and index >= len(run.events)
        if not batch:
            if done:
                break
            yield ": keep-alive\n\n"
            continue
        for event in batch:
            yield f"data: {_json.dumps(event, ensure_ascii=False)}\n\n"
            if event.get("type") == "idle":
                return
        if done:
            break
    yield f"data: {_json.dumps({'type': 'idle'})}\n\n"
