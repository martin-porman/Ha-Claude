"""OAuth routes blueprint.

Endpoints:
- GET /api/oauth/codex/start
- POST /api/oauth/codex/exchange
- GET /api/oauth/codex/status
- POST /api/oauth/codex/revoke
- GET /api/oauth/copilot/start
- GET /api/oauth/copilot/poll
- GET /api/oauth/copilot/status
- POST /api/oauth/copilot/revoke
- POST /api/session/claude_web/store
- GET /api/session/claude_web/status
- GET /api/session/claude_web/probe_usage
- POST /api/session/claude_web/clear
- GET /api/oauth/claude_code/status
- POST /api/oauth/claude_code/store
- POST /api/oauth/claude_code/clear
- POST /api/session/chatgpt_web/store
- GET /api/session/chatgpt_web/status
- POST /api/session/chatgpt_web/clear
- POST /api/session/grok_web/store (removed)
- GET /api/session/grok_web/status (removed)
- POST /api/session/grok_web/clear (removed)
- POST /api/session/gemini_web/store
- GET /api/session/gemini_web/status
- POST /api/session/gemini_web/clear
- POST /api/session/perplexity_web/store
- GET /api/session/perplexity_web/status
- POST /api/session/perplexity_web/clear
"""

import logging

from flask import Blueprint, request, jsonify

logger = logging.getLogger(__name__)

oauth_bp = Blueprint('oauth', __name__)


# ---------------------------------------------------------------------------
# OpenAI Codex OAuth flow
# ---------------------------------------------------------------------------

@oauth_bp.route('/api/oauth/codex/start', methods=['GET'])
def api_oauth_codex_start():
    """Start the OpenAI Codex OAuth flow. Returns the authorization URL."""
    try:
        from providers.openai_codex import start_oauth_flow
        authorize_url, state = start_oauth_flow()
        return jsonify({"authorize_url": authorize_url, "state": state}), 200
    except Exception as e:
        logger.error(f"Codex OAuth start error: {e}")
        return jsonify({"error": str(e)}), 500


@oauth_bp.route('/api/oauth/codex/exchange', methods=['POST'])
def api_oauth_codex_exchange():
    """Exchange the redirect URL (or code) for an access token."""
    try:
        from providers.openai_codex import exchange_code
        data = request.json or {}
        redirect_url = data.get("redirect_url", "").strip()
        state = data.get("state", "").strip()
        if not redirect_url or not state:
            return jsonify({"error": "Missing redirect_url or state"}), 400
        token = exchange_code(redirect_url, state)
        return jsonify({"ok": True, "account_id": token.get("account_id")}), 200
    except Exception as e:
        logger.error(f"Codex OAuth exchange error: {e}")
        return jsonify({"error": str(e)}), 400


@oauth_bp.route('/api/oauth/codex/status', methods=['GET'])
def api_oauth_codex_status():
    """Return whether a valid Codex token is available."""
    try:
        from providers.openai_codex import get_token_status
        return jsonify(get_token_status()), 200
    except Exception as e:
        return jsonify({"configured": False, "error": str(e)}), 200


@oauth_bp.route('/api/oauth/codex/revoke', methods=['POST'])
def api_oauth_codex_revoke():
    """Delete the stored Codex OAuth token (logout)."""
    try:
        import os as _os
        import providers.openai_codex as _codex_mod
        _codex_mod._stored_token = None
        token_file = _codex_mod._TOKEN_FILE
        try:
            if _os.path.exists(token_file):
                _os.remove(token_file)
        except Exception:
            pass
        logger.info("Codex: OAuth token revoked by user.")
        return jsonify({"ok": True}), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# GitHub Copilot Device Code OAuth flow
# ---------------------------------------------------------------------------

@oauth_bp.route('/api/oauth/copilot/start', methods=['GET'])
def api_oauth_copilot_start():
    """Start the GitHub Device Code flow."""
    try:
        from providers.github_copilot import start_device_flow
        result = start_device_flow()
        return jsonify(result), 200
    except Exception as e:
        logger.error(f"Copilot OAuth start error: {e}")
        return jsonify({"error": str(e)}), 500


@oauth_bp.route('/api/oauth/copilot/poll', methods=['GET'])
def api_oauth_copilot_poll():
    """Poll GitHub for the access token."""
    try:
        from providers.github_copilot import poll_device_flow
        return jsonify(poll_device_flow()), 200
    except Exception as e:
        logger.error(f"Copilot OAuth poll error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@oauth_bp.route('/api/oauth/copilot/status', methods=['GET'])
def api_oauth_copilot_status():
    """Return whether a valid Copilot token is available."""
    try:
        from providers.github_copilot import get_token_status
        return jsonify(get_token_status()), 200
    except Exception as e:
        return jsonify({"configured": False, "error": str(e)}), 200


@oauth_bp.route('/api/oauth/copilot/revoke', methods=['POST'])
def api_oauth_copilot_revoke():
    """Clear the stored GitHub Copilot OAuth token."""
    try:
        from providers.github_copilot import clear_token
        clear_token()
        return jsonify({"ok": True}), 200
    except Exception as e:
        logger.error(f"Copilot: revoke error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# Claude Web session endpoints
# ---------------------------------------------------------------------------

@oauth_bp.route('/api/session/claude_web/store', methods=['POST'])
def api_session_claude_web_store():
    """Store a Claude.ai session key."""
    try:
        from providers.claude_web import store_session_key
        data = request.json or {}
        session_key = data.get("session_key", "").strip()
        if not session_key:
            return jsonify({"error": "Missing session_key"}), 400
        result = store_session_key(session_key)
        return jsonify(result), 200
    except Exception as e:
        logger.error(f"Claude Web session store error: {e}")
        return jsonify({"error": str(e)}), 400


@oauth_bp.route('/api/session/claude_web/status', methods=['GET'])
def api_session_claude_web_status():
    """Return Claude Web session status."""
    try:
        from providers.claude_web import get_session_status
        return jsonify(get_session_status()), 200
    except Exception as e:
        return jsonify({"configured": False, "error": str(e)}), 200


@oauth_bp.route('/api/session/claude_web/probe_usage', methods=['GET'])
def api_session_claude_web_probe_usage():
    """Probe claude.ai endpoints to discover usage/quota data.

    Returns raw responses from all candidate endpoints so we can identify
    which ones expose remaining token/limit info.
    """
    try:
        from providers.claude_web import probe_usage_endpoints
        results = probe_usage_endpoints()
        return jsonify(results), 200
    except Exception as e:
        logger.error(f"ClaudeWeb probe_usage error: {e}")
        return jsonify({"error": str(e)}), 500


@oauth_bp.route('/api/session/claude_web/clear', methods=['POST'])
def api_session_claude_web_clear():
    """Clear stored Claude Web session token."""
    try:
        from providers.claude_web import clear_session
        clear_session()
        return jsonify({"ok": True}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400


# ---------------------------------------------------------------------------
# Claude Code (Claude subscription via the official CLI) endpoints
# ---------------------------------------------------------------------------

@oauth_bp.route('/api/oauth/claude_code/status', methods=['GET'])
def api_oauth_claude_code_status():
    """Return Claude Code CLI install + auth status (never the token)."""
    try:
        from providers.claude_code import get_status
        return jsonify(get_status()), 200
    except Exception as e:
        logger.error(f"Claude Code status error: {e}")
        return jsonify({"installed": False, "logged_in": False, "configured": False, "error": str(e)}), 200


@oauth_bp.route('/api/oauth/claude_code/store', methods=['POST'])
def api_oauth_claude_code_store():
    """Store a `claude setup-token` OAuth token (file mode 600)."""
    try:
        from providers.claude_code import store_token
        data = request.get_json(silent=True) or {}
        token = str(data.get("token", "")).strip()
        if not token:
            return jsonify({"error": "Missing token"}), 400
        return jsonify(store_token(token)), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Claude Code token store error: {e}")
        return jsonify({"error": "Could not store token"}), 500


@oauth_bp.route('/api/oauth/claude_code/clear', methods=['POST'])
def api_oauth_claude_code_clear():
    """Remove the Claude Code token stored via the UI."""
    try:
        from providers.claude_code import clear_token
        clear_token()
        return jsonify({"ok": True}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400


# ---------------------------------------------------------------------------
# ChatGPT Web session endpoints
# ---------------------------------------------------------------------------

@oauth_bp.route('/api/session/chatgpt_web/store', methods=['POST'])
def api_session_chatgpt_web_store():
    """Store a ChatGPT Web access token."""
    try:
        from providers.chatgpt_web import store_access_token
        data = request.json or {}
        access_token = data.get("access_token", "").strip()
        cf_clearance = data.get("cf_clearance", "").strip()
        if not access_token:
            return jsonify({"error": "Missing access_token"}), 400
        result = store_access_token(access_token, cf_clearance=cf_clearance)
        return jsonify(result), 200
    except Exception as e:
        logger.error(f"ChatGPT Web session store error: {e}")
        return jsonify({"error": str(e)}), 400


@oauth_bp.route('/api/session/chatgpt_web/status', methods=['GET'])
def api_session_chatgpt_web_status():
    """Return ChatGPT Web session status."""
    try:
        from providers.chatgpt_web import get_session_status
        return jsonify(get_session_status()), 200
    except Exception as e:
        return jsonify({"configured": False, "error": str(e)}), 200


@oauth_bp.route('/api/session/chatgpt_web/clear', methods=['POST'])
def api_session_chatgpt_web_clear():
    """Clear stored ChatGPT Web access token."""
    try:
        from providers.chatgpt_web import clear_session
        clear_session()
        return jsonify({"ok": True}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400


# ---------------------------------------------------------------------------
# Grok Web session endpoints (provider removed)
# ---------------------------------------------------------------------------

@oauth_bp.route('/api/session/grok_web/store', methods=['POST'])
def api_session_grok_web_store():
    return jsonify({"error": "grok_web provider removed"}), 410


@oauth_bp.route('/api/session/grok_web/status', methods=['GET'])
def api_session_grok_web_status():
    return jsonify({"configured": False, "removed": True, "error": "grok_web provider removed"}), 200


@oauth_bp.route('/api/session/grok_web/clear', methods=['POST'])
def api_session_grok_web_clear():
    return jsonify({"ok": True, "removed": True}), 200


# ---------------------------------------------------------------------------
# Gemini Web session endpoints
# ---------------------------------------------------------------------------

@oauth_bp.route('/api/session/gemini_web/store', methods=['POST'])
def api_session_gemini_web_store():
    """Store Gemini Web session cookies (__Secure-1PSID and __Secure-1PSIDTS)."""
    try:
        from providers.gemini_web import store_session
        data = request.json or {}
        psid   = data.get("psid", "").strip()
        psidts = data.get("psidts", "").strip()
        if not psid or not psidts:
            return jsonify({"error": "Missing psid or psidts"}), 400
        result = store_session(psid, psidts)
        return jsonify(result), 200
    except Exception as e:
        logger.error(f"Gemini Web session store error: {e}")
        return jsonify({"error": str(e)}), 400


@oauth_bp.route('/api/session/gemini_web/status', methods=['GET'])
def api_session_gemini_web_status():
    """Return Gemini Web session status."""
    try:
        from providers.gemini_web import get_session_status
        return jsonify(get_session_status()), 200
    except Exception as e:
        return jsonify({"configured": False, "error": str(e)}), 200


@oauth_bp.route('/api/session/gemini_web/clear', methods=['POST'])
def api_session_gemini_web_clear():
    """Clear stored Gemini Web session."""
    try:
        from providers.gemini_web import clear_session
        clear_session()
        return jsonify({"ok": True}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400


# ---------------------------------------------------------------------------
# Perplexity Web session endpoints
# ---------------------------------------------------------------------------

@oauth_bp.route('/api/session/perplexity_web/store', methods=['POST'])
def api_session_perplexity_web_store():
    """Store Perplexity Web session cookies."""
    try:
        from providers.perplexity_web import store_session
        data = request.json or {}
        csrf_token = (data.get("csrf_token") or "").strip()
        session_token = (data.get("session_token") or "").strip()
        if not csrf_token or not session_token:
            return jsonify({"error": "Missing csrf_token or session_token"}), 400
        result = store_session(csrf_token, session_token)
        return jsonify(result), 200
    except Exception as e:
        logger.error(f"Perplexity Web session store error: {e}")
        return jsonify({"error": str(e)}), 400


@oauth_bp.route('/api/session/perplexity_web/status', methods=['GET'])
def api_session_perplexity_web_status():
    """Return Perplexity Web session status."""
    try:
        from providers.perplexity_web import get_session_status
        return jsonify(get_session_status()), 200
    except Exception as e:
        return jsonify({"configured": False, "error": str(e)}), 200


@oauth_bp.route('/api/session/perplexity_web/clear', methods=['POST'])
def api_session_perplexity_web_clear():
    """Clear stored Perplexity Web session."""
    try:
        from providers.perplexity_web import clear_session
        clear_session()
        return jsonify({"ok": True}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400
