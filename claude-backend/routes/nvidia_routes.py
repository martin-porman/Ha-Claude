"""NVIDIA routes blueprint.

Endpoints:
- POST /api/nvidia/test_model
- POST /api/nvidia/test_models
- POST /api/provider/test_models
"""

import json
import logging
import os
import time

import requests
from flask import Blueprint, request, jsonify

logger = logging.getLogger(__name__)

nvidia_bp = Blueprint('nvidia', __name__)


@nvidia_bp.route('/api/nvidia/test_model', methods=['POST'])
def api_nvidia_test_model():
    """Quick NVIDIA chat test for the currently selected model."""
    import api
    from services.model_service import blocklist_nvidia_model, mark_nvidia_model_tested_ok
    if not api.NVIDIA_API_KEY:
        return jsonify({"success": False, "error": api.tr("err_nvidia_api_key")}), 400

    model_id = api.get_active_model()
    if not isinstance(model_id, str) or not model_id.strip():
        return jsonify({"success": False, "error": api.tr("err_nvidia_model_invalid")}), 400

    url = "https://integrate.api.nvidia.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api.NVIDIA_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": "ciao"}],
        "stream": False,
        "max_tokens": 32,
        "temperature": 0.2,
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=15)

        if resp.status_code >= 400:
            if resp.status_code in (404, 400, 422):
                blocklist_nvidia_model(model_id)
                if resp.status_code == 404:
                    reason = "not available (404)"
                elif resp.status_code == 400:
                    reason = "not chat-compatible (400)"
                else:
                    reason = "not chat-compatible (422)"
                return jsonify({
                    "success": False,
                    "blocklisted": True,
                    "model": model_id,
                    "message": api.tr("err_nvidia_model_removed", reason=reason, model_id=model_id),
                }), 200

            return jsonify({
                "success": False,
                "blocklisted": False,
                "model": model_id,
                "message": api.tr("provider_test_failed_http", provider_name="NVIDIA", code=resp.status_code),
            }), 200

        data = resp.json() if resp.content else {}
        ok = bool(isinstance(data, dict) and (data.get("choices") or data.get("id")))
        if ok:
            mark_nvidia_model_tested_ok(model_id)
        return jsonify({"success": ok, "blocklisted": False, "model": model_id}), 200

    except Exception as e:
        return jsonify({
            "success": False,
            "blocklisted": False,
            "model": model_id,
            "message": f"NVIDIA test error: {type(e).__name__}: {e}",
        }), 200


@nvidia_bp.route('/api/nvidia/test_models', methods=['POST'])
def api_nvidia_test_models():
    """General NVIDIA model scan."""
    import api
    from services.model_service import (
        NVIDIA_MODEL_BLOCKLIST, NVIDIA_MODEL_TESTED_OK, NVIDIA_MODEL_UNCERTAIN,
        blocklist_nvidia_model, mark_nvidia_model_tested_ok, mark_nvidia_model_uncertain,
        _fetch_nvidia_models_live, get_nvidia_models_cached,
    )
    if not api.NVIDIA_API_KEY:
        return jsonify({"success": False, "error": api.tr("err_nvidia_api_key")}), 400

    body = request.get_json(silent=True) or {}
    logger.info("NVIDIA test_models invoked")
    try:
        max_models = int(body.get("max_models") or 0)
    except Exception:
        max_models = 0
    try:
        cursor = int(body.get("cursor") or 0)
    except Exception:
        cursor = 0

    unlimited_scan = max_models <= 0
    if not unlimited_scan:
        max_models = max(1, min(200, max_models))

    max_seconds = 300.0 if unlimited_scan else 55.0
    per_model_timeout = 10

    all_models = _fetch_nvidia_models_live(api.NVIDIA_API_KEY) or get_nvidia_models_cached(api.NVIDIA_API_KEY) or api.PROVIDER_MODELS.get("nvidia", [])
    all_models = [m for m in (all_models or []) if isinstance(m, str) and m.strip()]
    candidates = list(dict.fromkeys(
        all_models + sorted(NVIDIA_MODEL_BLOCKLIST) + sorted(NVIDIA_MODEL_TESTED_OK) + sorted(NVIDIA_MODEL_UNCERTAIN)
    ))

    if cursor < 0:
        cursor = 0
    if candidates and cursor >= len(candidates):
        cursor = 0

    url = "https://integrate.api.nvidia.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api.NVIDIA_API_KEY}",
        "Content-Type": "application/json",
    }

    started = time.time()
    tested: list = []
    ok: list = []
    removed: list = []
    uncertain: list = []
    events: list = []
    stopped_reason = None
    timeouts = 0
    idx = cursor

    def _is_model_invalid_4xx(status_code: int, body_text: str) -> bool:
        if status_code in (404, 422):
            return True
        if status_code != 400:
            return False
        low = (body_text or "").lower()
        invalid_markers = (
            "model_not_found", "unknown model", "model not found",
            "unsupported model", "model is not supported", "invalid model",
            "no such model", "not chat-compatible",
        )
        non_model_markers = (
            "insufficient", "quota", "credit", "balance", "billing",
            "auth", "unauthorized", "forbidden", "rate limit", "too many requests",
        )
        if any(m in low for m in non_model_markers):
            return False
        return any(m in low for m in invalid_markers)

    while idx < len(candidates):
        model_id = candidates[idx]
        if (not unlimited_scan) and (len(tested) >= max_models):
            break
        if (time.time() - started) > max_seconds:
            stopped_reason = f"timeout ({int(max_seconds)}s)"
            break

        tested.append(model_id)
        logger.info(f"NVIDIA test_models: [{idx + 1}/{len(candidates)}] testing '{model_id}'")
        payload = {
            "model": model_id,
            "messages": [{"role": "user", "content": "ciao"}],
            "stream": False,
            "max_tokens": 16,
            "temperature": 0.0,
        }

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=per_model_timeout)
        except requests.exceptions.ReadTimeout:
            logger.info(f"NVIDIA test_models: timeout on '{model_id}'")
            events.append(f"⏱ timeout: {model_id}")
            uncertain.append(model_id)
            mark_nvidia_model_uncertain(model_id)
            timeouts += 1
            idx += 1
            continue
        except Exception as e:
            logger.warning(f"NVIDIA test_models: network error on '{model_id}': {type(e).__name__}: {e}")
            uncertain.append(model_id)
            mark_nvidia_model_uncertain(model_id)
            events.append(f"⚠️ network {type(e).__name__}: {model_id}")
            idx += 1
            continue

        if resp.status_code == 200:
            events.append(f"✅ ok: {model_id}")
            ok.append(model_id)
            mark_nvidia_model_tested_ok(model_id)
            idx += 1
            continue

        _resp_text = ""
        try:
            _resp_text = resp.text or ""
        except Exception:
            pass

        if _is_model_invalid_4xx(resp.status_code, _resp_text):
            events.append(f"⛔ blocklist (HTTP {resp.status_code}): {model_id}")
            blocklist_nvidia_model(model_id)
            removed.append(model_id)
            idx += 1
            continue

        uncertain.append(model_id)
        mark_nvidia_model_uncertain(model_id)
        events.append(f"⚠️ status {resp.status_code}: {model_id}")
        idx += 1
        continue

    next_cursor = idx
    remaining = max(0, len(candidates) - next_cursor)
    return jsonify({
        "success": True,
        "tested": len(tested),
        "total": len(candidates),
        "ok": len(ok),
        "removed": len(removed),
        "uncertain": len(uncertain),
        "tested_models": tested,
        "ok_models": ok,
        "removed_models": removed,
        "uncertain_models": uncertain,
        "events": events,
        "blocklisted": bool(removed),
        "stopped_reason": stopped_reason,
        "remaining": remaining,
        "next_cursor": next_cursor,
        "timeouts": timeouts,
    }), 200


@nvidia_bp.route('/api/provider/test_models', methods=['POST'])
def api_provider_test_models():
    """Generic model scan for selected API providers (OpenRouter, Mistral)."""
    import api
    from services.model_service import (
        MODEL_BLOCKLIST_FILE,
        mark_provider_model_tested_ok, mark_provider_model_uncertain, blocklist_model,
    )
    body = request.get_json(silent=True) or {}
    provider = str(body.get("provider") or "").strip().lower()
    if provider not in {"openrouter", "mistral"}:
        return jsonify({"success": False, "error": "Unsupported provider for batch test"}), 400

    provider_keys = {
        "openrouter": api.OPENROUTER_API_KEY,
        "mistral": api.MISTRAL_API_KEY,
    }
    provider_urls = {
        "openrouter": "https://openrouter.ai/api/v1/chat/completions",
        "mistral": "https://api.mistral.ai/v1/chat/completions",
    }
    api_key = provider_keys.get(provider) or ""
    if not api_key:
        return jsonify({"success": False, "error": f"{provider}: API key not configured"}), 400

    try:
        max_models = int(body.get("max_models") or 0)
    except Exception:
        max_models = 0
    try:
        cursor = int(body.get("cursor") or 0)
    except Exception:
        cursor = 0

    unlimited_scan = max_models <= 0
    if not unlimited_scan:
        max_models = max(1, min(200, max_models))

    max_seconds = 300.0 if unlimited_scan else 55.0
    per_model_timeout = 12

    all_models = [m for m in (api.PROVIDER_MODELS.get(provider) or []) if isinstance(m, str) and m.strip()]
    blocked_from_file: list = []
    tested_from_file: list = []
    uncertain_from_file: list = []
    try:
        if os.path.isfile(MODEL_BLOCKLIST_FILE):
            with open(MODEL_BLOCKLIST_FILE, "r", encoding="utf-8") as fh:
                _blk = json.load(fh) or {}
            _pv = _blk.get(provider)
            if isinstance(_pv, dict):
                _b = _pv.get("blocked") or []
                _t = _pv.get("tested_ok") or []
                _u = _pv.get("uncertain") or []
                blocked_from_file = [m for m in _b if isinstance(m, str)]
                tested_from_file = [m for m in _t if isinstance(m, str)]
                uncertain_from_file = [m for m in _u if isinstance(m, str)]
    except Exception:
        pass

    all_models = list(dict.fromkeys(
        all_models + blocked_from_file + tested_from_file + uncertain_from_file
    ))

    if cursor < 0:
        cursor = 0
    if all_models and cursor >= len(all_models):
        cursor = 0

    url = provider_urls[provider]
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    started = time.time()
    tested: list = []
    ok: list = []
    removed: list = []
    uncertain: list = []
    events: list = []
    stopped_reason = None
    timeouts = 0
    idx = cursor

    while idx < len(all_models):
        model_id = all_models[idx]
        if (not unlimited_scan) and (len(tested) >= max_models):
            break
        if (time.time() - started) > max_seconds:
            stopped_reason = f"timeout ({int(max_seconds)}s)"
            break

        tested.append(model_id)
        payload = {
            "model": model_id,
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
            "max_tokens": 16,
            "temperature": 0.0,
        }

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=per_model_timeout)
        except requests.exceptions.ReadTimeout:
            uncertain.append(model_id)
            mark_provider_model_uncertain(provider, model_id)
            events.append(f"⏱ timeout: {model_id}")
            timeouts += 1
            idx += 1
            continue
        except Exception as e:
            uncertain.append(model_id)
            mark_provider_model_uncertain(provider, model_id)
            events.append(f"⚠️ network {type(e).__name__}: {model_id}")
            idx += 1
            continue

        if resp.status_code == 200:
            ok.append(model_id)
            mark_provider_model_tested_ok(provider, model_id)
            events.append(f"✅ ok: {model_id}")
            idx += 1
            continue

        _resp_text = ""
        try:
            _resp_text = resp.text or ""
        except Exception:
            pass

        if resp.status_code in (404, 422) or (resp.status_code == 400 and "model" in _resp_text.lower()):
            removed.append(model_id)
            blocklist_model(provider, model_id)
            events.append(f"⛔ blocklist (HTTP {resp.status_code}): {model_id}")
            idx += 1
            continue

        uncertain.append(model_id)
        mark_provider_model_uncertain(provider, model_id)
        events.append(f"⚠️ status {resp.status_code}: {model_id}")
        idx += 1
        continue

    next_cursor = idx
    remaining = max(0, len(all_models) - next_cursor)
    return jsonify({
        "success": True,
        "provider": provider,
        "tested": len(tested),
        "total": len(all_models),
        "ok": len(ok),
        "removed": len(removed),
        "uncertain": len(uncertain),
        "tested_models": tested,
        "ok_models": ok,
        "removed_models": removed,
        "uncertain_models": uncertain,
        "events": events,
        "blocklisted": bool(removed),
        "stopped_reason": stopped_reason,
        "remaining": remaining,
        "next_cursor": next_cursor,
        "timeouts": timeouts,
    }), 200


