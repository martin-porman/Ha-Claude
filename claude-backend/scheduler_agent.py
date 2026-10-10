"""SchedulerAgent — conversational management of Amira's scheduled tasks.

Runs through Claude Code (the claude_code provider, native mode, subscription
OAuth token): Claude reads and changes tasks with Amira's scheduler REST API
(/api/scheduled/tasks) using its own Bash tool. One persistent Claude Code
session per SchedulerAgent session_id.

Endpoint: POST /api/agent/scheduler (routes/scheduled_routes.py)
"""

import json
import logging
import os
import threading

from core.translations import tr

logger = logging.getLogger(__name__)

# ── Language-aware response instruction ────────────────────────────────────────

_RESPOND_INSTRUCTION: dict = {
    "en": "Respond in English.",
    "it": "Rispondi sempre in Italiano.",
    "es": "Responde siempre en Español.",
    "fr": "Réponds toujours en Français.",
}

_CRON_TABLE = """\
| Schedule             | Cron              |
|----------------------|-------------------|
| Every day at 8am     | `0 8 * * *`       |
| Mon–Fri at 9:00      | `0 9 * * 1-5`     |
| Every hour           | `0 * * * *`       |
| Every 30 minutes     | `*/30 * * * *`    |
| Midnight             | `0 0 * * *`       |
| Friday at 6pm        | `0 18 * * 5`      |"""

_SYSTEM_PROMPT_TEMPLATE = """\
You are SchedulerAgent, the assistant for Amira's scheduled tasks.

A scheduled task sends a **message** to Amira's main AI on a cron schedule; the
message is handled as if the user had typed it (weather summaries, device
reports, reminders…). The reply lands in the chat session `cron_<task_id>`.

Manage tasks only through Amira's scheduler REST API (see "Amira scheduled
tasks" above): list, create, delete, enable/disable. Built-in system tasks
(builtin=true) cannot be deleted.

## Cron syntax (5 fields: minute hour day month weekday)
{cron_table}

Convert natural-language times to cron without asking for confirmation.
After creating a task, show its id, cron and next run. Keep **cron** and
**message** in English in the API call.
{respond_instruction}
"""

_SESSIONS_FILE = "/config/amira/scheduler_sessions.json"
_lock = threading.Lock()


def _get_system_prompt() -> str:
    """Returns the system prompt localised to the configured LANGUAGE."""
    lang = os.getenv("LANGUAGE", "en").lower()
    respond = _RESPOND_INSTRUCTION.get(lang, _RESPOND_INSTRUCTION["en"])
    return _SYSTEM_PROMPT_TEMPLATE.format(
        cron_table=_CRON_TABLE,
        respond_instruction=respond,
    )


# ── Session storage (text history, persisted) ──────────────────────────────────
# session_id -> [{"role": "user"|"assistant", "content": str}, ...]

def _load_sessions() -> dict:
    try:
        with open(_SESSIONS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_sessions(sessions: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_SESSIONS_FILE), exist_ok=True)
        tmp = _SESSIONS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(sessions, f, ensure_ascii=False)
        os.replace(tmp, _SESSIONS_FILE)
    except OSError as e:
        logger.warning(f"SchedulerAgent: could not save sessions: {e}")


def clear_session(session_id: str) -> None:
    """Delete a session's history and its Claude Code session."""
    with _lock:
        sessions = _load_sessions()
        sessions.pop(session_id, None)
        _save_sessions(sessions)
    try:
        from providers.claude_code import forget_cli_session
        forget_cli_session(f"scheduler_{session_id}")
    except Exception:
        pass


def get_session_history(session_id: str) -> list:
    """Return the message history of a session (copy)."""
    with _lock:
        return list(_load_sessions().get(session_id, []))


def list_sessions() -> list:
    """Return all session ids."""
    with _lock:
        return list(_load_sessions().keys())


# ── Public chat interface ──────────────────────────────────────────────────────

def chat(user_message: str, session_id: str = "default") -> str:
    """Process one user message through Claude Code and return the reply text."""
    from providers.claude_code import ClaudeCodeProvider, native_tools_enabled
    if not native_tools_enabled():
        return tr(
            "scheduler_needs_native",
            "SchedulerAgent needs Claude Code native mode (CLAUDE_CODE_NATIVE_TOOLS=true).",
        )

    with _lock:
        history = list(_load_sessions().get(session_id, []))

    provider = ClaudeCodeProvider(model=os.getenv("SCHEDULER_AGENT_MODEL", "") or "sonnet")
    messages = (
        [{"role": "system", "content": _get_system_prompt()}]
        + history
        + [{"role": "user", "content": user_message}]
    )
    parts = []
    for evt in provider.stream_chat(messages, {"session_id": f"scheduler_{session_id}", "intent": "scheduler"}):
        if evt.get("type") == "text":
            parts.append(evt.get("text", ""))
        elif evt.get("type") == "error":
            logger.error(f"SchedulerAgent: {evt.get('message')}")
            return tr("scheduler_llm_call_error", "LLM call error: {error}", error=evt.get("message", ""))
    reply = "".join(parts).strip() or tr("scheduler_no_response", "(no response)")

    with _lock:
        sessions = _load_sessions()
        sessions[session_id] = history + [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": reply},
        ]
        _save_sessions(sessions)
    return reply
