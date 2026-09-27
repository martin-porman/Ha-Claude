"""Bubble routes blueprint.

Endpoints:
- GET /api/bubble/status
- POST /api/bubble/register
- POST /api/set_model
- POST /api/bubble/device-id
- GET /api/bubble/config
- GET /api/bubble/devices
- POST /api/bubble/devices
- PATCH /api/bubble/devices/<device_id>
- DELETE /api/bubble/devices/<device_id>
"""

import logging
import os
from datetime import datetime

from flask import Blueprint, request, jsonify

logger = logging.getLogger(__name__)

bubble_bp = Blueprint('bubble', __name__)


@bubble_bp.route('/api/bubble/status', methods=['GET'])
def api_bubble_status():
    """Diagnostic endpoint: check chat bubble registration status."""
    import api
    loader_path = os.path.join(api.HA_CONFIG_DIR, "www", "ha-claude-chat-bubble.js")
    module_paths = {
        "loader": loader_path,
        "bubble": os.path.join(api.HA_CONFIG_DIR, "www", "ha-claude-chat-bubble.bubble.js"),
        "card": os.path.join(api.HA_CONFIG_DIR, "www", "ha-claude-chat-bubble.card.js"),
        "automation": os.path.join(api.HA_CONFIG_DIR, "www", "ha-claude-chat-bubble.automation.js"),
    }
    module_files = {}
    for name, p in module_paths.items():
        ex = os.path.isfile(p)
        module_files[name] = {
            "path": p,
            "exists": ex,
            "size_bytes": (os.path.getsize(p) if ex else 0),
        }

    ingress_url = api.get_addon_ingress_url()

    resource_info = None
    try:
        ws_result = api.call_ha_websocket("lovelace/resources/list")
        if isinstance(ws_result, dict) and ws_result.get("success") is False:
            resource_info = {"error": "resources API unavailable (YAML mode?)", "raw": str(ws_result)}
        else:
            resources = ws_result
            if isinstance(ws_result, dict):
                resources = ws_result.get("result", [])
            found = []
            if isinstance(resources, list):
                for res in resources:
                    if isinstance(res, dict) and "ha-claude-chat-bubble" in res.get("url", ""):
                        found.append({"id": res.get("id"), "url": res.get("url"), "type": res.get("type")})
            resource_info = {"registered": len(found) > 0, "entries": found, "total_resources": len(resources) if isinstance(resources, list) else 0}
    except Exception as e:
        resource_info = {"error": str(e)}

    return jsonify({
        "bubble_enabled": api.ENABLE_CHAT_BUBBLE,
        "card_button_enabled": api.ENABLE_AMIRA_CARD_BUTTON,
        "automation_button_enabled": api.ENABLE_AMIRA_AUTOMATION_BUTTON,
        "registered_flag": api._chat_bubble_registered,
        "ingress_url": ingress_url or "(empty)",
        "js_file": module_files["loader"],
        "module_files": module_files,
        "lovelace_resource": resource_info,
        "hint": "After registering, do a FULL browser refresh (Ctrl+Shift+R) on your HA dashboard. Loader + 3 modules must all be present.",
    })


@bubble_bp.route('/api/bubble/register', methods=['POST'])
def api_bubble_register():
    """Force re-registration of the chat bubble Lovelace resource."""
    import api
    api._chat_bubble_registered = False
    api._ingress_url_cache = None
    api.setup_chat_bubble()
    return jsonify({"ok": api._chat_bubble_registered, "message": "Re-registration attempted. Check /api/bubble/status for details."})


@bubble_bp.route('/api/set_model', methods=['POST'])
def api_set_model():
    import api
    from core.model_utils import normalize_model_name, get_model_provider
    data = request.json or {}

    if "provider" in data:
        api.AI_PROVIDER = data["provider"]

        if "model" not in data:
            api.SELECTED_MODEL = ""
            api.SELECTED_PROVIDER = ""
            default_model = api.PROVIDER_DEFAULTS.get(api.AI_PROVIDER, {}).get("model")
            if default_model:
                api.AI_MODEL = default_model
            logger.info(f"Provider changed to {api.AI_PROVIDER}, reset to default model: {api.AI_MODEL}")

    if "model" in data:
        normalized = normalize_model_name(data["model"])

        _STRICT_PROVIDERS = {"anthropic", "openai", "google"}

        if "provider" in data and api.AI_PROVIDER in _STRICT_PROVIDERS:
            model_provider = get_model_provider(normalized)
            if model_provider not in ("unknown", api.AI_PROVIDER):
                api.SELECTED_MODEL = ""
                api.SELECTED_PROVIDER = ""
                default_model = api.PROVIDER_DEFAULTS.get(api.AI_PROVIDER, {}).get("model")
                if default_model:
                    api.AI_MODEL = default_model
                logger.warning(
                    f"Ignoring incompatible model '{normalized}' for provider '{api.AI_PROVIDER}'. Using default '{api.AI_MODEL}'."
                )
            else:
                api.AI_MODEL = normalized
                api.SELECTED_MODEL = normalized
                api.SELECTED_PROVIDER = api.AI_PROVIDER
        else:
            api.AI_MODEL = normalized
            api.SELECTED_MODEL = normalized
            api.SELECTED_PROVIDER = api.AI_PROVIDER

    logger.info(f"Runtime model changed → {api.AI_PROVIDER} / {api.AI_MODEL}")

    try:
        api.initialize_ai_client()
    except Exception as e:
        logger.exception(f"Failed to reinitialize AI client after model/provider change: {e}")
        return jsonify({
            "success": False,
            "error": "Failed to initialize AI client for selected provider/model",
            "provider": api.AI_PROVIDER,
            "model": api.AI_MODEL,
        }), 500

    try:
        api.save_runtime_selection(api.AI_PROVIDER, api.AI_MODEL)
    except Exception:
        pass

    if api.MODEL_FALLBACK_AVAILABLE:
        try:
            import model_fallback
            model_fallback.clear_cooldown(api.AI_PROVIDER)
        except Exception:
            pass

    try:
        import tools
        _tier = tools._get_tool_tier()
        _TIER_MISSING = {
            "compact": ["update_dashboard", "delete_automation", "create_script", "update_script",
                        "list_config_files", "read_config_file", "get_scripts", "get_dashboards", "get_areas"],
            "extended": ["update_dashboard", "delete_automation", "create_script", "update_script"],
        }
        _missing = _TIER_MISSING.get(_tier, [])
        if _tier in ("compact", "extended") and _missing:
            _tpl = api.get_lang_text("warn_tier_limited") or "⚠️ Limited mode ({tier}): advanced features not available ({missing}). Switch to a more capable model."
            _tier_warning_msg = _tpl.format(tier=_tier, missing=", ".join(_missing))
        else:
            _tier_warning_msg = ""
    except Exception:
        _tier = "full"
        _missing = []
        _tier_warning_msg = ""

    resp = {
        "success": True,
        "provider": api.AI_PROVIDER,
        "model": api.AI_MODEL,
        "tier": _tier,
        "tier_limited": _tier in ("compact", "extended"),
        "tier_missing_tools": _missing,
        "tier_warning_msg": _tier_warning_msg,
    }
    if api.AGENT_CONFIG_AVAILABLE:
        try:
            import agent_config
            mgr = agent_config.get_agent_manager()
            identity = mgr.resolve_identity()
            resp["agent_identity"] = {
                "name": identity.name,
                "emoji": identity.emoji,
            }
        except Exception:
            pass

    return jsonify(resp)


@bubble_bp.route('/api/bubble/device-id', methods=['POST'])
def api_bubble_device_id():
    """Set or generate a unique device ID for bubble device-specific configuration."""
    try:
        import hashlib
        import uuid
        data = request.get_json() or {}
        device_id = data.get("device_id", "").strip()
        device_name = data.get("device_name", "").strip()
        fingerprint = str(data.get("fingerprint") or "").strip()

        if not device_id:
            if fingerprint:
                device_id = hashlib.md5(fingerprint.encode("utf-8")).hexdigest()[:12]
            else:
                device_id = hashlib.md5(str(uuid.uuid4()).encode()).hexdigest()[:12]

        if not all(c.isalnum() or c in '-_' for c in device_id):
            return jsonify({"success": False, "error": "Device ID can only contain alphanumeric, dash, and underscore"}), 400

        logger.info(f"Bubble device registered: id={device_id}, name={device_name}")

        return jsonify({
            "success": True,
            "device_id": device_id,
            "device_name": device_name or "Device",
            "instruction": "Store this device_id in your browser's localStorage as 'ha-claude-device-id' to enable device-specific bubble control"
        }), 200
    except Exception as e:
        logger.error(f"Error setting device ID: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bubble_bp.route('/api/bubble/config', methods=['GET'])
def api_bubble_config():
    """Get current bubble configuration and device visibility rules."""
    import api
    try:
        return jsonify({
            "success": True,
            "enabled": api.ENABLE_CHAT_BUBBLE
        }), 200
    except Exception as e:
        logger.error(f"Error getting bubble config: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bubble_bp.route('/api/bubble/devices', methods=['GET'])
def api_bubble_devices_list():
    """Get list of discovered devices and their bubble visibility settings."""
    import api
    try:
        devices = api.load_device_config()
        return jsonify({
            "success": True,
            "devices": devices
        }), 200
    except Exception as e:
        logger.error(f"Error listing devices: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bubble_bp.route('/api/bubble/devices', methods=['POST'])
def api_bubble_devices_create():
    """Register or update a device in the bubble tracking system."""
    import api
    try:
        data = request.get_json() or {}
        device_id = (data.get("device_id") or "").strip()
        device_name = (data.get("device_name") or "").strip()
        device_type = (data.get("device_type") or "desktop").lower()
        fingerprint = str(data.get("fingerprint") or "").strip()
        if len(fingerprint) > 512:
            fingerprint = fingerprint[:512]

        if not device_id or len(device_id) < 4:
            return jsonify({"success": False, "error": "Invalid device_id"}), 400

        if device_type not in ("phone", "tablet", "desktop"):
            device_type = "desktop"

        devices = api.load_device_config()

        canonical_id = None
        if fingerprint:
            for did, meta in devices.items():
                if not isinstance(meta, dict):
                    continue
                if (meta.get("fingerprint") == fingerprint) and (meta.get("device_type") == device_type):
                    canonical_id = did
                    break
        if canonical_id and canonical_id != device_id:
            logger.info(f"Device dedupe by fingerprint: {device_id} -> {canonical_id}")
            device_id = canonical_id

        is_new_device = device_id not in devices
        if is_new_device:
            devices[device_id] = {
                "name": device_name or f"{device_type.capitalize()}",
                "device_type": device_type,
                "fingerprint": fingerprint,
                "enabled": True,
                "first_seen": datetime.now().isoformat(),
                "last_seen": datetime.now().isoformat(),
            }
        else:
            devices[device_id]["last_seen"] = datetime.now().isoformat()
            if device_name:
                devices[device_id]["name"] = device_name
            if fingerprint and not devices[device_id].get("fingerprint"):
                devices[device_id]["fingerprint"] = fingerprint

        api.save_device_config(devices)
        if is_new_device:
            logger.info(f"Device registered: {device_id} ({device_type})")
        else:
            logger.debug(f"Device updated: {device_id}")

        is_enabled = devices[device_id].get("enabled", False)

        return jsonify({
            "success": True,
            "device_id": device_id,
            "enabled": is_enabled,
            "message": f"Device registered (enabled: {is_enabled})"
        }), 200
    except Exception as e:
        logger.error(f"Error registering device: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bubble_bp.route('/api/bubble/devices/<device_id>', methods=['PATCH'])
def api_bubble_devices_patch(device_id):
    """Enable or disable bubble for a specific device."""
    import api
    try:
        device_id = device_id.strip()
        data = request.get_json() or {}

        devices = api.load_device_config()
        if device_id not in devices:
            return jsonify({"success": False, "error": "Device not found"}), 404

        if "enabled" in data:
            if data["enabled"] is None:
                devices[device_id]["enabled"] = not devices[device_id].get("enabled", False)
            else:
                devices[device_id]["enabled"] = bool(data["enabled"])

        if "name" in data and data["name"]:
            devices[device_id]["name"] = str(data["name"]).strip()

        api.save_device_config(devices)
        logger.info(f"Device updated: {device_id} (enabled: {devices[device_id].get('enabled')})")

        return jsonify({
            "success": True,
            "device": devices[device_id]
        }), 200
    except Exception as e:
        logger.error(f"Error updating device: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bubble_bp.route('/api/bubble/devices/<device_id>', methods=['DELETE'])
def api_bubble_devices_delete(device_id):
    """Remove a device from tracking."""
    import api
    try:
        device_id = device_id.strip()
        devices = api.load_device_config()

        if device_id in devices:
            del devices[device_id]
            api.save_device_config(devices)
            logger.info(f"Device deleted: {device_id}")
            return jsonify({"success": True, "message": f"Device '{device_id}' removed"}), 200

        return jsonify({"success": False, "error": "Device not found"}), 404
    except Exception as e:
        logger.error(f"Error deleting device: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
