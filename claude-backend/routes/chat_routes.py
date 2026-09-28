"""Chat routes blueprint.

Endpoints:
- POST /api/chat
- POST /api/chat/stream
- GET  /api/chat/tail
- GET  /api/chat/status
- POST /api/chat/abort
- POST /api/chat/skill/deactivate
"""

import json
import logging

import pricing
from flask import Blueprint, request, jsonify, Response, stream_with_context

from services import turn_queue

logger = logging.getLogger(__name__)

chat_bp = Blueprint('chat', __name__)


@chat_bp.route('/api/chat', methods=['POST'])
def api_chat():
    """Chat endpoint."""
    import api
    data = request.get_json(silent=True)
    if data is None or not isinstance(data, dict):
        logger.warning(
            f"Invalid JSON body for /api/chat (content_type={request.content_type}, len={request.content_length})",
            extra={"context": "REQUEST"},
        )
        return jsonify({"error": "Invalid JSON"}), 400
    message = data.get("message", "").strip()
    session_id = data.get("session_id", "default")
    if not message:
        return jsonify({"error": "Empty message"}), 400
    api._last_sync_usage = {}
    response_text = api.chat_with_ai(message, session_id)
    usage_data = None
    if api._last_sync_usage:
        try:
            norm = pricing.normalize_usage(api._last_sync_usage)
            input_tokens = norm["input_tokens"]
            output_tokens = norm["output_tokens"]
            cache_read_tokens = norm["cache_read_tokens"]
            cache_write_tokens = norm["cache_write_tokens"]
            from core.model_utils import get_active_model
            model_name = api._last_sync_usage.get("model") or get_active_model()
            provider_name = api._last_sync_usage.get("provider") or api.AI_PROVIDER
            cost_bd = pricing.calculate_cost_breakdown(
                model_name, provider_name,
                input_tokens, output_tokens,
                cache_read_tokens, cache_write_tokens,
                api.COST_CURRENCY,
            )
            usage_data = {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_tokens": cache_read_tokens,
                "cache_write_tokens": cache_write_tokens,
                "cost": cost_bd["total_cost"],
                "cost_breakdown": {
                    "input": cost_bd["input_cost"],
                    "output": cost_bd["output_cost"],
                    "cache_read": cost_bd["cache_read_cost"],
                    "cache_write": cost_bd["cache_write_cost"],
                },
                "currency": api.COST_CURRENCY,
                "model": model_name,
                "provider": provider_name,
            }
            _cost_val = cost_bd["total_cost"]
            _sym = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}.get(api.COST_CURRENCY, api.COST_CURRENCY)
            if _cost_val > 0:
                logger.info(f"💰 {provider_name}/{model_name}: {input_tokens} in + {output_tokens} out → {_sym}{_cost_val:.6f}")
            else:
                logger.info(f"💰 {provider_name}/{model_name}: {input_tokens} in + {output_tokens} out → free")
            try:
                from usage_tracker import get_tracker
                get_tracker().record(usage_data)
            except Exception:
                pass
        except Exception as e:
            logger.debug(f"Cost enrichment for /api/chat failed: {e}")
    result = {"response": response_text}
    if usage_data:
        result["usage"] = usage_data
    return jsonify(result), 200


@chat_bp.route('/api/chat/stream', methods=['POST'])
def api_chat_stream():
    """Streaming chat endpoint using Server-Sent Events with image support."""
    import api
    data = request.get_json(silent=True)
    if data is None or not isinstance(data, dict):
        logger.warning(
            f"Invalid JSON body for /api/chat/stream (content_type={request.content_type}, len={request.content_length})",
            extra={"context": "REQUEST"},
        )
        return jsonify({"error": "Invalid JSON"}), 400
    message = data.get("message", "").strip()
    session_id = data.get("session_id", "default")
    image_data = data.get("image", None)
    read_only = data.get("read_only", False)
    voice_mode = data.get("voice_mode", False)
    req_language = (data.get("language") or "").lower()[:2] or None
    if not message:
        return jsonify({"error": "Empty message"}), 400
    if read_only:
        logger.info(f"Read-only mode active for session {session_id}")

    job = {
        "message": message,
        "image": image_data,
        "read_only": read_only,
        "voice_mode": voice_mode,
        "language": req_language,
    }
    placed = turn_queue.submit(session_id, job)

    # A turn was already running for this session: the prompt is queued and the
    # client's open tail will carry it. Nothing to stream from this request.
    if placed["queued"]:
        logger.info(f"Prompt queued for busy session {session_id}")
        return jsonify({
            "status": "queued",
            "from": placed["from"],
            "pending": turn_queue.status(session_id)["pending"],
        }), 202

    return Response(
        stream_with_context(turn_queue.tail(session_id, placed["from"])),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
    )


@chat_bp.route('/api/chat/tail', methods=['GET'])
def api_chat_tail():
    """Re-attach to a session's turn that is already running.

    The turn keeps running whatever happens to the connection, so a reload, a
    chat switch or a dropped network only costs the tail, never the answer.
    """
    session_id = request.args.get("session_id", "default")
    try:
        start = int(request.args.get("from", "0"))
    except ValueError:
        start = 0
    return Response(
        stream_with_context(turn_queue.tail(session_id, start)),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
    )


@chat_bp.route('/api/chat/status', methods=['GET'])
def api_chat_status():
    """Whether a turn is running for this session, and how much is queued."""
    session_id = request.args.get("session_id", "default")
    return jsonify(turn_queue.status(session_id)), 200


@chat_bp.route('/api/chat/abort', methods=['POST'])
def api_chat_abort():
    """Stop the running turn and drop anything queued behind it."""
    data = request.get_json() or {}
    session_id = data.get("session_id", "default")
    turn_queue.abort(session_id)
    logger.info(f"Abort requested for session {session_id}")
    return jsonify({"status": "abort_requested"}), 200


@chat_bp.route('/api/chat/skill/deactivate', methods=['POST'])
def api_skill_deactivate():
    """Deactivate the active skill for the current session."""
    import api
    data = request.get_json() or {}
    session_id = data.get("session_id", "default")
    removed = api.session_active_skill.pop(session_id, None)
    logger.info(f"Skill '{removed}' deactivated for session {session_id}")
    return jsonify({"status": "ok", "removed": removed}), 200
