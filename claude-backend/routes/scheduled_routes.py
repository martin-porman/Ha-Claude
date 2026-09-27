"""Scheduled tasks routes blueprint.

Endpoints:
- GET /api/scheduled/stats
- GET /api/scheduled/tasks
- POST /api/scheduled/tasks
- DELETE /api/scheduled/tasks/<task_id>
- POST /api/scheduled/tasks/<task_id>/toggle
- POST /api/agent/scheduler
- GET /api/agent/scheduler/sessions
- DELETE /api/agent/scheduler/session/<session_id>
"""

import logging
import uuid

from flask import Blueprint, request, jsonify

logger = logging.getLogger(__name__)

scheduled_bp = Blueprint('scheduled', __name__)


@scheduled_bp.route('/api/scheduled/stats', methods=['GET'])
def api_scheduled_stats():
    """Get scheduled tasks statistics."""
    import api
    try:
        if not api.SCHEDULED_TASKS_AVAILABLE:
            return jsonify({"status": "error", "message": "Scheduled tasks not available"}), 501
        import scheduled_tasks
        scheduler = scheduled_tasks.get_scheduler()
        return jsonify({"status": "success", "scheduler_stats": scheduler.get_stats()}), 200
    except Exception as e:
        logger.error(f"Scheduled stats error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@scheduled_bp.route('/api/scheduled/tasks', methods=['GET'])
def api_scheduled_tasks_list():
    """List all registered scheduled tasks."""
    import api
    try:
        if not api.SCHEDULED_TASKS_AVAILABLE:
            return jsonify({"status": "error", "message": "Scheduled tasks not available"}), 501
        import scheduled_tasks
        scheduler = scheduled_tasks.get_scheduler()
        tasks = [
            {
                "task_id": t.task_id,
                "name": t.name,
                "cron": t.cron_expression,
                "description": t.description,
                "enabled": t.enabled,
                "run_count": t.run_count,
                "last_run": t.last_run,
                "next_run": t.next_run,
                "message": t.message,
                "builtin": t.builtin,
            }
            for t in scheduler.tasks.values()
        ]
        return jsonify({"status": "success", "tasks": tasks, "count": len(tasks)}), 200
    except Exception as e:
        logger.error(f"Scheduled tasks list error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@scheduled_bp.route('/api/scheduled/tasks', methods=['POST'])
def api_scheduled_tasks_create():
    """Create a new scheduled task."""
    import api
    try:
        if not api.SCHEDULED_TASKS_AVAILABLE:
            return jsonify({"status": "error", "message": "Scheduled tasks not available"}), 501
        import scheduled_tasks
        data = request.json or {}
        name = data.get("name", "").strip()
        cron = data.get("cron", "").strip()
        message = data.get("message", "").strip()
        if not name or not cron or not message:
            return jsonify({"status": "error", "message": api.tr(
                "scheduler_create_required_fields",
                "name, cron and message are required",
            )}), 400
        task_id = data.get("task_id") or f"task_{uuid.uuid4().hex[:8]}"
        scheduler = scheduled_tasks.get_scheduler()
        if task_id in scheduler.tasks:
            return jsonify({"status": "error", "message": api.tr(
                "scheduler_task_already_exists",
                "Task '{task_id}' already exists",
                task_id=task_id,
            )}), 409
        task = scheduler.add_message_task(
            task_id=task_id, name=name, cron_expression=cron, message=message,
            description=data.get("description", ""), enabled=data.get("enabled", True),
        )
        return jsonify({"status": "success", "task_id": task.task_id,
                        "message": api.tr(
                            "scheduler_task_created",
                            "Task '{name}' created ({cron})",
                            name=name,
                            cron=cron,
                        )}), 201
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    except Exception as e:
        logger.error(f"Scheduled task create error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@scheduled_bp.route('/api/scheduled/tasks/<task_id>', methods=['DELETE'])
def api_scheduled_tasks_delete(task_id):
    """Elimina un task pianificato (non built-in)."""
    import api
    try:
        if not api.SCHEDULED_TASKS_AVAILABLE:
            return jsonify({"status": "error", "message": "Scheduled tasks not available"}), 501
        import scheduled_tasks
        scheduler = scheduled_tasks.get_scheduler()
        t = scheduler.tasks.get(task_id)
        if t and t.builtin:
            return jsonify({"status": "error", "message": api.tr(
                "scheduler_task_delete_builtin_forbidden",
                "Cannot delete a built-in task",
            )}), 403
        ok = scheduler.remove_task(task_id)
        if not ok:
            return jsonify({"status": "error", "message": api.tr(
                "scheduler_task_not_found",
                "Task '{task_id}' not found",
                task_id=task_id,
            )}), 404
        return jsonify({"status": "success", "message": api.tr(
            "scheduler_task_deleted",
            "Task '{task_id}' deleted",
            task_id=task_id,
        )}), 200
    except Exception as e:
        logger.error(f"Scheduled task delete error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@scheduled_bp.route('/api/scheduled/tasks/<task_id>/toggle', methods=['POST'])
def api_scheduled_tasks_toggle(task_id):
    """Enable or disable a scheduled task."""
    import api
    try:
        if not api.SCHEDULED_TASKS_AVAILABLE:
            return jsonify({"status": "error", "message": "Scheduled tasks not available"}), 501
        import scheduled_tasks
        data = request.json or {}
        enabled = data.get("enabled", True)
        scheduler = scheduled_tasks.get_scheduler()
        if task_id not in scheduler.tasks:
            return jsonify({"status": "error", "message": f"Task '{task_id}' not found"}), 404
        scheduler.tasks[task_id].enabled = enabled
        scheduler.save_tasks()
        action = "enabled" if enabled else "disabled"
        return jsonify({"status": "success", "message": f"Task '{task_id}' {action}"}), 200
    except Exception as e:
        logger.error(f"Scheduled task toggle error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@scheduled_bp.route('/api/agent/scheduler', methods=['POST'])
def api_agent_scheduler():
    """Chat con lo SchedulerAgent per creare/elencare/gestire task pianificati in linguaggio naturale."""
    import api
    try:
        if not api.SCHEDULER_AGENT_AVAILABLE:
            return jsonify({"status": "error", "message": api.tr(
                "scheduler_agent_not_available",
                "SchedulerAgent not available",
            )}), 501
        import scheduler_agent
        data = request.json or {}
        user_message = (data.get("message") or "").strip()
        if not user_message:
            return jsonify({"status": "error", "message": api.tr(
                "scheduler_message_required",
                "Field 'message' is required",
            )}), 400
        session_id = data.get("session_id") or "default"
        reply = scheduler_agent.chat(user_message, session_id=session_id)
        history = scheduler_agent.get_session_history(session_id)
        return jsonify({
            "status": "success",
            "reply": reply,
            "session_id": session_id,
            "message_count": len(history),
        }), 200
    except Exception as e:
        logger.error(f"SchedulerAgent chat error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@scheduled_bp.route('/api/agent/scheduler/sessions', methods=['GET'])
def api_agent_scheduler_sessions():
    """Elenca tutte le sessioni attive dello SchedulerAgent."""
    import api
    try:
        if not api.SCHEDULER_AGENT_AVAILABLE:
            return jsonify({"status": "error", "message": api.tr(
                "scheduler_agent_not_available",
                "SchedulerAgent not available",
            )}), 501
        import scheduler_agent
        sessions = scheduler_agent.list_sessions()
        return jsonify({"status": "success", "sessions": sessions, "count": len(sessions)}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@scheduled_bp.route('/api/agent/scheduler/session/<session_id>', methods=['DELETE'])
def api_agent_scheduler_session_delete(session_id):
    """Cancella la cronologia di una sessione SchedulerAgent."""
    import api
    try:
        if not api.SCHEDULER_AGENT_AVAILABLE:
            return jsonify({"status": "error", "message": api.tr(
                "scheduler_agent_not_available",
                "SchedulerAgent not available",
            )}), 501
        import scheduler_agent
        scheduler_agent.clear_session(session_id)
        return jsonify({"status": "success", "message": api.tr(
            "scheduler_session_deleted",
            "Session '{session_id}' deleted",
            session_id=session_id,
        )}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
