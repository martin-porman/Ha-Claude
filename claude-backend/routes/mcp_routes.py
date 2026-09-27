"""MCP routes blueprint.

Endpoints:
- GET /api/mcp/servers
- GET /api/mcp/server/<server_name>/status
- POST /api/mcp/server/<server_name>/reconnect
- POST /api/mcp/server/<server_name>/start
- POST /api/mcp/server/<server_name>/stop
- GET /api/mcp/tools
- GET /api/mcp/diagnostics
- POST /api/mcp/test/<server_name>/<tool_name>
- POST /api/mcp/install
- GET /api/mcp/server/<server_name>/tools
- GET /api/mcp/conversations/<session_id>/messages
- GET /api/mcp/server/<server_name>/oauth/start
- GET /api/mcp/oauth/callback
- GET /api/mcp/server/<server_name>/oauth/status
- POST /api/mcp/server/<server_name>/oauth/revoke
"""

import json
import logging
import time
from datetime import datetime
from flask import Blueprint, request, jsonify
from core.translations import tr

logger = logging.getLogger(__name__)

mcp_bp = Blueprint('mcp', __name__)

# Mutable shared state imported by reference
from api import conversations


@mcp_bp.route('/api/mcp/servers', methods=['GET'])
def api_mcp_servers_list():
    """List all configured MCP servers and their connection status."""
    import api as _api
    try:
        if not _api.MCP_AVAILABLE:
            return jsonify({
                "status": "error",
                "message": "MCP support not available"
            }), 501

        manager = _api.mcp.get_mcp_manager()
        configured = _api._load_mcp_config_servers()
        autostart_set = set(_api._load_mcp_runtime_state().get("autostart_servers", []))

        # Include every configured server (even if not started yet)
        servers = []
        for name, cfg in configured.items():
            running_server = manager.servers.get(name)
            running = bool(running_server and running_server.is_connected())
            tools = list(running_server.tools.keys()) if running_server and running else []
            transport = "http" if cfg.get("url") else cfg.get("transport", "stdio")
            servers.append({
                "name": name,
                "configured": True,
                "running": running,
                "connected": running,  # backward compatibility
                "state": "running" if running else "stopped",
                "transport": transport,
                "autostart": name in autostart_set,
                "tools_count": len(tools),
                "tools": tools,
            })

        # Also include any currently-registered server not present in config file
        for name, server in manager.servers.items():
            if name in configured:
                continue
            running = server.is_connected()
            tools = list(server.tools.keys()) if running else []
            servers.append({
                "name": name,
                "configured": False,
                "running": running,
                "connected": running,
                "state": "running" if running else "stopped",
                "transport": server.transport_type,
                "autostart": name in autostart_set,
                "tools_count": len(tools),
                "tools": tools,
            })

        return jsonify({
            "status": "success",
            "servers": servers,
            "total_servers": len(servers),
        }), 200
    except Exception as e:
        logger.error(f"MCP list servers error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@mcp_bp.route('/api/mcp/server/<server_name>/status', methods=['GET'])
def api_mcp_server_status(server_name):
    """Get status of a specific MCP server."""
    import api as _api
    try:
        if not _api.MCP_AVAILABLE:
            return jsonify({
                "status": "error",
                "message": "MCP support not available"
            }), 501

        manager = _api.mcp.get_mcp_manager()
        if server_name not in manager.servers:
            return jsonify({
                "status": "error",
                "message": f"Server '{server_name}' not found"
            }), 404

        server = manager.servers[server_name]
        return jsonify({
            "status": "success",
            "server_name": server_name,
            "connected": server.is_connected(),
            "transport": server.transport_type,
            "tools": {name: {"description": tool.get("description", "")}
                     for name, tool in server.tools.items()},
            "tools_count": len(server.tools),
        }), 200
    except Exception as e:
        logger.error(f"MCP server status error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@mcp_bp.route('/api/mcp/server/<server_name>/reconnect', methods=['POST'])
def api_mcp_server_reconnect(server_name):
    """Reconnect to a specific MCP server."""
    import api as _api
    try:
        if not _api.MCP_AVAILABLE:
            return jsonify({
                "status": "error",
                "message": "MCP support not available"
            }), 501

        manager = _api.mcp.get_mcp_manager()
        if server_name not in manager.servers:
            return jsonify({
                "status": "error",
                "message": f"Server '{server_name}' not found"
            }), 404

        server = manager.servers[server_name]
        server.disconnect()
        server.connect()

        return jsonify({
            "status": "success",
            "message": f"Reconnected to '{server_name}'",
            "connected": server.is_connected(),
            "tools_count": len(server.tools),
        }), 200
    except Exception as e:
        logger.error(f"MCP server reconnect error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@mcp_bp.route('/api/mcp/server/<server_name>/start', methods=['POST'])
def api_mcp_server_start(server_name):
    """Start a specific MCP server from the saved config file (no addon restart needed)."""
    import api as _api
    try:
        if not _api.MCP_AVAILABLE:
            return jsonify({"status": "error", "message": "MCP support not available"}), 501

        raw_cfg = _api._load_mcp_config_servers()
        if not raw_cfg:
            return jsonify({"status": "error", "message": "MCP config file not found"}), 404

        if server_name not in raw_cfg:
            return jsonify({"status": "error", "message": f"Server '{server_name}' not in config"}), 404

        n = _api.mcp.initialize_mcp_servers({server_name: raw_cfg[server_name]})
        manager = _api.mcp.get_mcp_manager()
        server = manager.servers.get(server_name)
        if n > 0 and server:
            _api._set_mcp_server_autostart(server_name, True)
            return jsonify({
                "status": "success",
                "connected": True,
                "running": True,
                "autostart": True,
                "tools_count": len(server.tools),
                "message": tr(
                    "mcp_server_started_with_tools",
                    "Server '{server_name}' started with {tools_count} tools",
                    server_name=server_name,
                    tools_count=len(server.tools),
                )
            }), 200
        else:
            return jsonify({"status": "error", "message": tr(
                "mcp_server_connect_failed",
                "Unable to connect to '{server_name}'",
                server_name=server_name,
            )}), 500
    except Exception as e:
        logger.error(f"MCP server start error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@mcp_bp.route('/api/mcp/server/<server_name>/stop', methods=['POST'])
def api_mcp_server_stop(server_name):
    """Stop a specific MCP server and disable its autostart flag."""
    import api as _api
    try:
        if not _api.MCP_AVAILABLE:
            return jsonify({"status": "error", "message": "MCP support not available"}), 501

        manager = _api.mcp.get_mcp_manager()
        removed = manager.remove_server(server_name)
        _api._set_mcp_server_autostart(server_name, False)

        if removed:
            return jsonify({
                "status": "success",
                "running": False,
                "autostart": False,
                "message": tr(
                    "mcp_server_stopped",
                    "Server '{server_name}' stopped",
                    server_name=server_name,
                )
            }), 200

        # Not running is still a valid "stopped" target state.
        return jsonify({
            "status": "success",
            "running": False,
            "autostart": False,
            "message": tr(
                "mcp_server_already_stopped",
                "Server '{server_name}' was already stopped",
                server_name=server_name,
            )
        }), 200
    except Exception as e:
        logger.error(f"MCP server stop error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@mcp_bp.route('/api/mcp/tools', methods=['GET'])
def api_mcp_all_tools():
    """List all available tools from all connected MCP servers."""
    import api as _api
    try:
        if not _api.MCP_AVAILABLE:
            return jsonify({
                "status": "error",
                "message": "MCP support not available"
            }), 501

        manager = _api.mcp.get_mcp_manager()
        all_tools = manager.get_all_tools()

        tools_by_server = {}
        for tool_name, tool_info in all_tools.items():
            server_name = tool_info.get("server", "unknown")
            if server_name not in tools_by_server:
                tools_by_server[server_name] = []
            tools_by_server[server_name].append({
                "name": tool_name,
                "tool_name": tool_info.get("tool_name", ""),
                "description": tool_info.get("description", ""),
            })

        return jsonify({
            "status": "success",
            "tools": all_tools,
            "tools_by_server": tools_by_server,
            "total_tools": len(all_tools),
        }), 200
    except Exception as e:
        logger.error(f"MCP all tools error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@mcp_bp.route('/api/mcp/diagnostics', methods=['GET'])
def api_mcp_diagnostics():
    """Get detailed diagnostics for all MCP servers."""
    import api as _api
    try:
        if not _api.MCP_AVAILABLE:
            return jsonify({
                "status": "error",
                "message": "MCP support not available"
            }), 501

        manager = _api.mcp.get_mcp_manager()
        diagnostics = {
            "timestamp": datetime.now().isoformat(),
            "servers": {},
            "stats": manager.stats() if hasattr(manager, 'stats') else {},
        }

        for name, server in manager.servers.items():
            server_diag = {
                "name": name,
                "connected": server.is_connected(),
                "transport": server.transport_type,
                "tools_count": len(server.tools),
                "tools": list(server.tools.keys()),
                "config": {
                    "transport": server.transport_type,
                    "command": server.config.get("command", "") if server.transport_type == "stdio" else "",
                    "url": server.config.get("url", "") if server.transport_type == "http" else "",
                }
            }
            diagnostics["servers"][name] = server_diag

        return jsonify(diagnostics), 200
    except Exception as e:
        logger.error(f"MCP diagnostics error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@mcp_bp.route('/api/mcp/test/<server_name>/<tool_name>', methods=['POST'])
def api_mcp_test_tool(server_name, tool_name):
    """Test a specific MCP tool with provided arguments."""
    import api as _api
    try:
        if not _api.MCP_AVAILABLE:
            return jsonify({
                "status": "error",
                "message": "MCP support not available"
            }), 501

        data = request.get_json() or {}
        arguments = data.get("arguments", {})

        manager = _api.mcp.get_mcp_manager()
        if server_name not in manager.servers:
            return jsonify({
                "status": "error",
                "message": f"Server '{server_name}' not found"
            }), 404

        server = manager.servers[server_name]
        if not server.is_connected():
            return jsonify({
                "status": "error",
                "message": f"Server '{server_name}' is not connected"
            }), 503

        # Execute tool and measure time
        start_time = time.time()
        try:
            result = server.call_tool(tool_name, arguments)
            elapsed = time.time() - start_time

            # Parse result if it's JSON
            try:
                result_obj = json.loads(result) if isinstance(result, str) else result
            except (json.JSONDecodeError, TypeError):
                result_obj = {"result": result}

            return jsonify({
                "status": "success",
                "server": server_name,
                "tool": tool_name,
                "arguments": arguments,
                "result": result_obj,
                "execution_time_ms": round(elapsed * 1000, 2),
            }), 200
        except Exception as e:
            elapsed = time.time() - start_time
            return jsonify({
                "status": "error",
                "server": server_name,
                "tool": tool_name,
                "error": str(e),
                "execution_time_ms": round(elapsed * 1000, 2),
            }), 500
    except Exception as e:
        logger.error(f"MCP test tool error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@mcp_bp.route('/api/mcp/install', methods=['POST'])
def api_mcp_install():
    """Install pip packages for MCP servers (called manually from UI)."""
    import api as _api
    try:
        data = request.get_json() or {}
        packages = data.get("packages", [])
        if not packages:
            return jsonify({"success": False, "output": tr(
                "mcp_no_packages_specified",
                "No packages specified.",
            )}), 400
        result = _api.mcp.pip_install_packages(packages)
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"success": False, "output": tr(
            "mcp_error_prefix",
            "Error: {error}",
            error=e,
        )}), 500


@mcp_bp.route('/api/mcp/server/<server_name>/tools', methods=['GET'])
def api_mcp_server_tools(server_name):
    """Get tools for a specific MCP server."""
    import api as _api
    try:
        if not _api.MCP_AVAILABLE:
            return jsonify({
                "status": "error",
                "message": "MCP support not available"
            }), 501

        manager = _api.mcp.get_mcp_manager()
        if server_name not in manager.servers:
            return jsonify({
                "status": "error",
                "message": f"Server '{server_name}' not found"
            }), 404

        server = manager.servers[server_name]
        tools_list = []

        for tool_name, tool_info in server.tools.items():
            tools_list.append({
                "name": tool_name,
                "description": tool_info.get("description", ""),
                "inputSchema": tool_info.get("inputSchema", {}),
            })

        return jsonify({
            "status": "success",
            "server": server_name,
            "connected": server.is_connected(),
            "tools": tools_list,
            "total_tools": len(tools_list),
        }), 200
    except Exception as e:
        logger.error(f"MCP server tools error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


# ---------------------------------------------------------------------------
# MCP OAuth 2.0 endpoints
# ---------------------------------------------------------------------------

@mcp_bp.route('/api/mcp/server/<server_name>/oauth/start', methods=['GET'])
def api_mcp_oauth_start(server_name):
    """Begin the OAuth Authorization Code + PKCE flow for an MCP server.

    Returns {authorize_url, state} — the UI opens authorize_url in a popup/tab.
    """
    import api as _api
    try:
        raw_cfg = _api._load_mcp_config_servers()
        srv_cfg = raw_cfg.get(server_name) or {}
        # Accept URL from query param (UI not yet saved) or from saved config
        mcp_url = request.args.get("url", "").strip() or srv_cfg.get("url") or ""
        if not mcp_url:
            return jsonify({"ok": False, "error": f"Server '{server_name}' has no URL or is not HTTP"}), 400

        scope = srv_cfg.get("oauth_scope", "")

        # Use redirect_uri from frontend (browser knows the correct absolute URL)
        redirect_uri = request.args.get("redirect_uri", "").strip()
        if not redirect_uri:
            # Fallback: build from ingress URL
            ingress_url = (_api.get_addon_ingress_url() or "").rstrip("/")
            redirect_uri = f"{ingress_url}/api/mcp/oauth/callback"

        import mcp_oauth
        result = mcp_oauth.start_oauth_flow(server_name, mcp_url, redirect_uri, scope)
        return jsonify(result), 200
    except Exception as e:
        logger.error(f"MCP OAuth start error for '{server_name}': {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@mcp_bp.route('/api/mcp/oauth/callback', methods=['GET'])
def api_mcp_oauth_callback():
    """OAuth redirect callback — exchanges the authorization code for tokens.

    The OAuth provider redirects here after the user grants access.
    Returns an HTML page that closes itself (popup flow) or shows status.
    """
    from flask import make_response
    code = request.args.get("code", "").strip()
    state = request.args.get("state", "").strip()
    error = request.args.get("error", "").strip()

    if error:
        html = f"""<!DOCTYPE html><html><body>
<h2>OAuth error: {error}</h2>
<p>{request.args.get('error_description','')}</p>
<script>window.close();</script>
</body></html>"""
        return make_response(html, 400)

    if not code or not state:
        html = """<!DOCTYPE html><html><body>
<h2>Missing code or state</h2>
<script>window.close();</script>
</body></html>"""
        return make_response(html, 400)

    import mcp_oauth
    result = mcp_oauth.handle_callback(code, state)

    if result.get("ok"):
        server_name = result.get("server_name", "")
        scope = result.get("scope", "")
        html = f"""<!DOCTYPE html><html><body>
<h2>&#10003; Connected: {server_name}</h2>
<p>Scope: {scope or '(default)'}</p>
<p>You can close this window.</p>
<script>
  if (window.opener) {{
    window.opener.postMessage({{type:'mcp_oauth_done', server:'{server_name}', ok:true}}, '*');
  }}
  setTimeout(()=>window.close(), 2000);
</script>
</body></html>"""
        return make_response(html, 200)
    else:
        err = result.get("error", "Unknown error")
        html = f"""<!DOCTYPE html><html><body>
<h2>&#10007; OAuth failed</h2>
<p>{err}</p>
<script>window.close();</script>
</body></html>"""
        return make_response(html, 400)


@mcp_bp.route('/api/mcp/server/<server_name>/oauth/status', methods=['GET'])
def api_mcp_oauth_status(server_name):
    """Return current OAuth token status for an MCP server."""
    import mcp_oauth
    status = mcp_oauth.get_token_status(server_name)
    return jsonify(status), 200


@mcp_bp.route('/api/mcp/server/<server_name>/oauth/revoke', methods=['POST'])
def api_mcp_oauth_revoke(server_name):
    """Revoke (delete) stored OAuth token for an MCP server."""
    import mcp_oauth
    mcp_oauth.delete_token(server_name)
    return jsonify({"ok": True, "server_name": server_name}), 200


@mcp_bp.route('/api/mcp/conversations/<session_id>/messages', methods=['GET'])
def api_conversation_messages(session_id):
    """Get all messages for a conversation session."""
    import api as _api
    msgs = conversations.get(session_id, [])
    # Return only user/assistant text messages for UI display (filter empty content)
    display_msgs = []
    for m in msgs:
        content = m.get("content", "")
        # Skip messages with empty content or only whitespace
        if m.get("role") in ("user", "assistant") and isinstance(content, str) and content.strip():
            # Strip [CONTEXT: ...] blocks from user messages
            if m.get("role") == "user":
                content = _api._strip_context_blocks(content)
                if not content.strip():
                    continue
            msg_data = {"role": m["role"], "content": content}
            # Include model/provider/usage info for assistant messages
            if m.get("role") == "assistant":
                if "model" in m:
                    msg_data["model"] = m["model"]
                if "provider" in m:
                    msg_data["provider"] = m["provider"]
                if "usage" in m:
                    msg_data["usage"] = m["usage"]
            display_msgs.append(msg_data)
    return jsonify({"session_id": session_id, "messages": display_msgs}), 200
