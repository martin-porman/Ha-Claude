"""UI routes blueprint.

Endpoints:
- GET /
- GET /ui_bootstrap.js
- GET /ui_main.js
- GET /api/ui_ping
- GET /api/status
"""

import logging
import re

import requests
from flask import Blueprint, Response, jsonify

import chat_ui

logger = logging.getLogger(__name__)

ui_bp = Blueprint('ui', __name__)

# Maps provider name → Python package needed to chat.
# Providers not listed here use httpx (already in core).
_PROVIDER_SDK_MAP = {
    "anthropic": "anthropic",
    "openai": "openai",
    "google": None,
    "nvidia": None,
    "github": None,
    "groq": None,
    "mistral": None,
    "deepseek": None,
    "xai": None,
    "openrouter": None,
    "ollama": None,
    "custom": None,
    "minimax": None,
    "aihubmix": None,
    "siliconflow": None,
    "volcengine": None,
    "dashscope": None,
    "moonshot": None,
    "zhipu": None,
    "perplexity": None,
    "github_copilot": None,
    "openai_codex": None,
    "claude_web": None,
    "claude_code": None,
    "chatgpt_web": None,
    "gemini_web": None,
    "perplexity_web": None,
}


def _check_optional_sdks() -> dict:
    """Quick import-check for every optional dependency. Returns {name: bool}."""
    pkgs = ["anthropic", "openai", "google.genai", "mcp", "telegram", "twilio", "discord", "PyPDF2", "docx"]
    out = {}
    for name in pkgs:
        try:
            __import__(name)
            out[name] = True
        except ImportError:
            out[name] = False
    return out


def _check_provider_sdk(provider: str) -> tuple:
    """Check if the SDK needed by the given provider is installed.

    Returns:
        (True, "")           – SDK available or not needed.
        (False, human_msg)   – SDK missing, human_msg explains what to do.
    """
    import api
    sdk = _PROVIDER_SDK_MAP.get(provider)
    if sdk is None:
        return (True, "")
    try:
        __import__(sdk)
        return (True, "")
    except ImportError:
        _msgs = {
            "en": f"The '{sdk}' package is not installed. Provider '{provider}' cannot work. "
                  f"This can happen on ARM/Raspberry Pi devices where some packages fail to compile.",
            "it": f"Il pacchetto '{sdk}' non è installato. Il provider '{provider}' non può funzionare. "
                  f"Questo può succedere su dispositivi ARM/Raspberry Pi dove alcuni pacchetti non si compilano.",
            "es": f"El paquete '{sdk}' no está instalado. El proveedor '{provider}' no puede funcionar. "
                  f"Esto puede ocurrir en dispositivos ARM/Raspberry Pi.",
            "fr": f"Le package '{sdk}' n'est pas installé. Le fournisseur '{provider}' ne peut pas fonctionner. "
                  f"Cela peut se produire sur les appareils ARM/Raspberry Pi.",
        }
        return (False, _msgs.get(api.LANGUAGE, _msgs["en"]))


@ui_bp.route('/')
def index():
    """Serve the chat UI."""
    try:
        logger.info("Generating chat UI...")
        html = chat_ui.get_chat_ui()
        logger.info("Chat UI generated successfully")

        html = html.encode('utf-8', errors='replace').decode('utf-8', errors='replace')

        return Response(
            html,
            mimetype='text/html; charset=utf-8',
            headers={
                'Cache-Control': 'no-store, max-age=0',
                'Pragma': 'no-cache',
                'Expires': '0',
            }
        )
    except Exception as e:
        logger.error(f"Error generating chat UI: {type(e).__name__}: {str(e)}", exc_info=True)
        return {"error": f"Error generating UI: {type(e).__name__}: {str(e)}"}, 500


@ui_bp.route('/ui_bootstrap.js')
def ui_bootstrap():
    """Small bootstrap script loaded before the main inline UI."""
    js = r"""
(function () {
    function appendSystem(text) {
        try {
            var container = document.getElementById('chat');
            if (!container) return;
            var div = document.createElement('div');
            div.className = 'message system';
            div.textContent = String(text || '');
            container.appendChild(div);
            container.scrollTop = container.scrollHeight;
        } catch (e) {}
    }

    // Lightweight ping so the add-on logs show the browser executed JS.
    try {
        fetch('./api/ui_ping', { cache: 'no-store' }).catch(function () {});
    } catch (e) {}

    function onSendAttempt(evt) {
        try {
            // If the main UI didn't load, explain it directly.
            if (typeof window.handleButtonClick !== 'function') {
                appendSystem('❌ UI error: main script not loaded (handleButtonClick missing).');
                try { fetch('./api/ui_ping?send=1', { cache: 'no-store' }).catch(function () {}); } catch (e) {}
                if (evt && evt.preventDefault) evt.preventDefault();
                return false;
            }
        } catch (e) {}
        return true;
    }

    function bind() {
        try {
            var btn = document.getElementById('sendBtn');
            if (btn && !btn._bootstrapBound) {
                btn._bootstrapBound = true;
                btn.addEventListener('click', onSendAttempt, true);
            }
            var input = document.getElementById('input');
            if (input && !input._bootstrapKeyBound) {
                input._bootstrapKeyBound = true;
                input.addEventListener('keydown', function (e) {
                    if (e && e.key === 'Enter' && !e.shiftKey) {
                        onSendAttempt(e);
                    }
                }, true);
            }
        } catch (e) {}
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', bind);
    } else {
        bind();
    }
})();
"""
    return js, 200, {
        'Content-Type': 'application/javascript; charset=utf-8',
        'Cache-Control': 'no-store, max-age=0',
        'Pragma': 'no-cache',
        'Expires': '0',
    }


@ui_bp.route('/ui_main.js')
def ui_main():
    """Serve the main UI script as an external JS file."""
    html = chat_ui.get_chat_ui()
    m = re.search(r"<script(?!\s+src\s*=)[^>]*>\s*(.*?)\s*</script>", html, flags=re.S | re.I)
    js = (m.group(1) if m else "")
    if not js:
        logger.error("ui_main.js extraction failed: no inline <script> found")
        return js, 200, {'Content-Type': 'application/javascript; charset=utf-8'}

    lines = js.split('\n')
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if i < len(lines) - 1 and line.rstrip().endswith('/') and not line.rstrip().endswith('//'):
            next_line = lines[i + 1]
            if re.match(r'^\s*[^/]*?/[igm]*', next_line):
                result.append(line.rstrip() + ' ' + next_line.lstrip())
                i += 2
                continue
        result.append(line)
        i += 1
    js = '\n'.join(result)

    return js, 200, {
        'Content-Type': 'application/javascript; charset=utf-8',
        'Cache-Control': 'no-store, max-age=0',
        'Pragma': 'no-cache',
        'Expires': '0',
    }


@ui_bp.route('/api/ui_ping', methods=['GET'])
def api_ui_ping():
    """No-op endpoint used only to confirm that the browser executed JS."""
    return ("", 204)


@ui_bp.route('/api/status')
def api_status():
    """Debug endpoint to check HA connection status."""
    import api
    token = api.get_ha_token()
    ha_ok = False
    ha_msg = ""
    try:
        resp = requests.get(f"{api.HA_URL}/api/", headers=api.get_ha_headers(), timeout=10)
        ha_ok = resp.status_code == 200
        ha_msg = f"{resp.status_code}: {resp.text[:200]}"
    except Exception as e:
        ha_msg = str(e)

    pkg_status = _check_optional_sdks()
    missing = [k for k, v in pkg_status.items() if not v]
    provider_sdk_ok, provider_sdk_msg = _check_provider_sdk(api.AI_PROVIDER)

    return jsonify({
        "version": api.VERSION,
        "provider": api.AI_PROVIDER,
        "model": api.get_active_model(),
        "api_key_set": bool(api.get_api_key()),
        "ha_url": api.HA_URL,
        "supervisor_token_present": bool(token),
        "supervisor_token_length": len(token),
        "ha_connection_ok": ha_ok,
        "ha_response": ha_msg,
        "provider_sdk_available": provider_sdk_ok,
        "provider_sdk_message": provider_sdk_msg,
        "optional_packages": pkg_status,
        "missing_packages": missing,
        "platform": __import__('platform').machine(),
    })
