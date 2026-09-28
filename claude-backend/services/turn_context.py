"""Which session a turn belongs to, and the CLI process it owns.

A turn runs in its own thread, so the session id lives in thread-local storage.
Providers register the subprocess they spawn, which is what makes Stop able to
interrupt a turn that is parked waiting on the CLI rather than only between
events.
"""

import logging
import os
import signal
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_local = threading.local()
_procs: Dict[str, Any] = {}
_lock = threading.Lock()


def set_session(session_id: Optional[str]) -> None:
    _local.session_id = session_id


def current_session() -> Optional[str]:
    return getattr(_local, "session_id", None)


def register(proc: Any) -> None:
    """Bind a just-spawned subprocess to the session running in this thread."""
    sid = current_session()
    if not sid:
        return
    with _lock:
        _procs[sid] = proc


def unregister(proc: Any) -> None:
    with _lock:
        for sid, held in list(_procs.items()):
            if held is proc:
                _procs.pop(sid, None)


def kill(session_id: str) -> bool:
    """Terminate the CLI process owned by this session. True if one was killed."""
    with _lock:
        proc = _procs.pop(session_id, None)
    if proc is None:
        return False
    if proc.poll() is not None:
        return False
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
            break
        try:
            proc.wait(timeout=5)
            break
        except Exception:
            continue
    logger.info(f"Stopped CLI process for session {session_id}")
    return True
