"""MCP OAuth 2.0 Support.

Implements the OAuth 2.0 Authorization Code flow with PKCE for MCP servers
that require OAuth authentication (e.g. Supermemory, Notion, etc.).

Flow:
  1. GET /api/mcp/server/<name>/oauth/start
       → discovers OAuth server metadata
       → generates PKCE code_verifier + code_challenge
       → saves pending state to disk
       → returns {authorize_url, state} for the UI to open
  2. User completes OAuth in browser, gets redirected to:
       GET /api/mcp/oauth/callback?code=...&state=...
       → exchanges code for access_token + refresh_token
       → saves tokens to /config/amira/mcp_oauth_tokens.json
  3. mcp.py calls get_oauth_headers(server_name) before each HTTP request
       → returns {"Authorization": "Bearer <token>"}
       → auto-refreshes token if expired

References:
  https://spec.modelcontextprotocol.io/specification/2025-03-26/basic/authentication/
  https://datatracker.ietf.org/doc/html/rfc7636 (PKCE)
"""

import base64
import hashlib
import json
import logging
import os
import secrets
import time
from typing import Dict, Optional, Tuple
from urllib.parse import urlencode, urljoin, urlparse, parse_qs

import requests

logger = logging.getLogger(__name__)

# Persistent token store
_TOKENS_FILE = "/config/amira/mcp_oauth_tokens.json"
# In-memory pending states {state: {server_name, code_verifier, redirect_uri, ...}}
_pending: Dict[str, Dict] = {}


# ---------------------------------------------------------------------------
# PKCE helpers
# ---------------------------------------------------------------------------

def _pkce_pair() -> Tuple[str, str]:
    """Generate (code_verifier, code_challenge) for PKCE."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


# ---------------------------------------------------------------------------
# Token persistence
# ---------------------------------------------------------------------------

def _load_tokens() -> Dict:
    try:
        if os.path.isfile(_TOKENS_FILE):
            with open(_TOKENS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_tokens(tokens: Dict) -> None:
    try:
        os.makedirs(os.path.dirname(_TOKENS_FILE), exist_ok=True)
        with open(_TOKENS_FILE, "w", encoding="utf-8") as f:
            json.dump(tokens, f, indent=2)
    except Exception as e:
        logger.error(f"MCP OAuth: failed to save tokens: {e}")


def get_token(server_name: str) -> Optional[Dict]:
    """Return stored token dict for server, or None."""
    return _load_tokens().get(server_name)


def save_token(server_name: str, token_data: Dict) -> None:
    """Persist token data for a server."""
    tokens = _load_tokens()
    tokens[server_name] = token_data
    _save_tokens(tokens)
    logger.info(f"MCP OAuth [{server_name}]: token saved")


def delete_token(server_name: str) -> None:
    """Remove token for a server."""
    tokens = _load_tokens()
    if server_name in tokens:
        del tokens[server_name]
        _save_tokens(tokens)
        logger.info(f"MCP OAuth [{server_name}]: token revoked")


# ---------------------------------------------------------------------------
# OAuth server discovery
# ---------------------------------------------------------------------------

def _discover_oauth_metadata(mcp_url: str) -> Optional[Dict]:
    """Discover OAuth server metadata from MCP server.

    Tries:
      1. <base>/.well-known/oauth-authorization-server   (MCP spec)
      2. <base>/.well-known/openid-configuration         (OIDC fallback)
    """
    parsed = urlparse(mcp_url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    for path in [
        "/.well-known/oauth-authorization-server",
        "/.well-known/openid-configuration",
    ]:
        url = base + path
        try:
            resp = requests.get(url, timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                logger.info(f"MCP OAuth: discovered metadata at {url}")
                return data
        except Exception:
            continue

    # Some servers embed OAuth info at a non-standard path derived from the MCP URL
    # e.g. https://mcp.supermemory.ai/mcp → try https://mcp.supermemory.ai/oauth
    try:
        oauth_url = base + "/oauth"
        resp = requests.get(oauth_url + "/.well-known/oauth-authorization-server", timeout=8)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass

    logger.warning(f"MCP OAuth: could not discover metadata for {mcp_url}")
    return None


def _build_oauth_endpoints(mcp_url: str, metadata: Optional[Dict]) -> Dict[str, str]:
    """Build OAuth endpoint URLs from discovered metadata or MCP URL base."""
    parsed = urlparse(mcp_url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    if metadata:
        return {
            "authorization_endpoint": metadata.get("authorization_endpoint", base + "/oauth/authorize"),
            "token_endpoint": metadata.get("token_endpoint", base + "/oauth/token"),
            "registration_endpoint": metadata.get("registration_endpoint", ""),
        }

    # Fallback: standard paths
    return {
        "authorization_endpoint": base + "/oauth/authorize",
        "token_endpoint": base + "/oauth/token",
        "registration_endpoint": "",
    }


# ---------------------------------------------------------------------------
# Dynamic client registration (RFC 7591, optional)
# ---------------------------------------------------------------------------

def _register_client(registration_endpoint: str, redirect_uri: str) -> Optional[Dict]:
    """Attempt dynamic client registration if the server supports it."""
    if not registration_endpoint:
        return None
    try:
        payload = {
            "redirect_uris": [redirect_uri],
            "client_name": "Amira Home Assistant Assistant",
            "token_endpoint_auth_method": "none",  # public client
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
        }
        resp = requests.post(registration_endpoint, json=payload, timeout=8)
        if resp.status_code in (200, 201):
            data = resp.json()
            logger.info(f"MCP OAuth: dynamic client registered, client_id={data.get('client_id')}")
            return data
    except Exception as e:
        logger.debug(f"MCP OAuth: dynamic registration failed: {e}")
    return None


# ---------------------------------------------------------------------------
# Start flow
# ---------------------------------------------------------------------------

def start_oauth_flow(
    server_name: str,
    mcp_url: str,
    redirect_uri: str,
    scope: str = "",
) -> Dict:
    """Begin the OAuth Authorization Code + PKCE flow.

    Returns:
        {
            "authorize_url": "https://...",   # open in browser
            "state": "abc123",                # opaque, stored server-side
        }
    """
    metadata = _discover_oauth_metadata(mcp_url)
    endpoints = _build_oauth_endpoints(mcp_url, metadata)

    # Try dynamic registration to get a client_id
    client_id = "amira"  # fallback: many public MCP servers accept any client_id
    registration_info = _register_client(endpoints["registration_endpoint"], redirect_uri)
    if registration_info and registration_info.get("client_id"):
        client_id = registration_info["client_id"]

    code_verifier, code_challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)

    # Persist pending state (survives page reloads; cleared after exchange)
    _pending[state] = {
        "server_name": server_name,
        "mcp_url": mcp_url,
        "code_verifier": code_verifier,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "token_endpoint": endpoints["token_endpoint"],
        "created_at": time.time(),
    }

    params: Dict = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    if scope:
        params["scope"] = scope

    authorize_url = endpoints["authorization_endpoint"] + "?" + urlencode(params)
    logger.info(f"MCP OAuth [{server_name}]: flow started → {endpoints['authorization_endpoint']}")

    return {
        "authorize_url": authorize_url,
        "state": state,
        "client_id": client_id,
    }


# ---------------------------------------------------------------------------
# Handle callback
# ---------------------------------------------------------------------------

def handle_callback(code: str, state: str) -> Dict:
    """Exchange authorization code for tokens.

    Returns:
        {"ok": True, "server_name": "...", "scope": "..."}
        or {"ok": False, "error": "..."}
    """
    pending = _pending.pop(state, None)
    if not pending:
        return {"ok": False, "error": "Unknown or expired state parameter"}

    # Expire after 10 minutes
    if time.time() - pending["created_at"] > 600:
        return {"ok": False, "error": "OAuth state expired (>10 min). Please restart the flow."}

    server_name = pending["server_name"]
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": pending["redirect_uri"],
        "client_id": pending["client_id"],
        "code_verifier": pending["code_verifier"],
    }

    try:
        resp = requests.post(
            pending["token_endpoint"],
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        if resp.status_code not in (200, 201):
            logger.error(f"MCP OAuth [{server_name}]: token exchange failed HTTP {resp.status_code}: {resp.text[:200]}")
            return {"ok": False, "error": f"Token exchange failed (HTTP {resp.status_code})"}

        token_data = resp.json()
        token_data["_saved_at"] = time.time()
        token_data["_server_name"] = server_name
        token_data["_mcp_url"] = pending["mcp_url"]
        save_token(server_name, token_data)

        logger.info(f"MCP OAuth [{server_name}]: tokens obtained, scope={token_data.get('scope','')}")
        return {
            "ok": True,
            "server_name": server_name,
            "scope": token_data.get("scope", ""),
            "expires_in": token_data.get("expires_in"),
        }

    except Exception as e:
        logger.error(f"MCP OAuth [{server_name}]: callback error: {e}")
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Token usage and auto-refresh
# ---------------------------------------------------------------------------

def get_oauth_headers(server_name: str) -> Dict[str, str]:
    """Return Authorization headers for server, auto-refreshing if needed.

    Returns {} if no token is stored (server does not use OAuth or not yet authorized).
    """
    token_data = get_token(server_name)
    if not token_data:
        return {}

    access_token = token_data.get("access_token", "")
    if not access_token:
        return {}

    # Auto-refresh if token expires within 60 seconds
    expires_in = token_data.get("expires_in")
    saved_at = token_data.get("_saved_at", 0)
    if expires_in and (time.time() - saved_at) > (expires_in - 60):
        refreshed = _try_refresh(server_name, token_data)
        if refreshed:
            access_token = refreshed.get("access_token", access_token)

    return {"Authorization": f"Bearer {access_token}"}


def _try_refresh(server_name: str, token_data: Dict) -> Optional[Dict]:
    """Attempt token refresh. Returns new token_data or None."""
    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        return None

    # We need the token endpoint — stored when we got the token
    mcp_url = token_data.get("_mcp_url", "")
    if not mcp_url:
        return None

    metadata = _discover_oauth_metadata(mcp_url)
    endpoints = _build_oauth_endpoints(mcp_url, metadata)

    try:
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": token_data.get("client_id", "amira"),
        }
        resp = requests.post(
            endpoints["token_endpoint"],
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        if resp.status_code in (200, 201):
            new_data = resp.json()
            new_data["_saved_at"] = time.time()
            new_data["_server_name"] = server_name
            new_data["_mcp_url"] = mcp_url
            # Keep refresh_token if not returned in new response
            if "refresh_token" not in new_data and refresh_token:
                new_data["refresh_token"] = refresh_token
            save_token(server_name, new_data)
            logger.info(f"MCP OAuth [{server_name}]: token refreshed")
            return new_data
    except Exception as e:
        logger.warning(f"MCP OAuth [{server_name}]: refresh failed: {e}")
    return None


# ---------------------------------------------------------------------------
# Status helper
# ---------------------------------------------------------------------------

def get_token_status(server_name: str) -> Dict:
    """Return human-readable token status for the UI."""
    token_data = get_token(server_name)
    if not token_data:
        return {"configured": False, "status": "not_connected"}

    access_token = token_data.get("access_token", "")
    if not access_token:
        return {"configured": False, "status": "invalid_token"}

    expires_in = token_data.get("expires_in")
    saved_at = token_data.get("_saved_at", 0)
    elapsed = time.time() - saved_at

    if expires_in:
        remaining = expires_in - elapsed
        if remaining <= 0:
            has_refresh = bool(token_data.get("refresh_token"))
            return {
                "configured": True,
                "status": "expired",
                "can_refresh": has_refresh,
                "scope": token_data.get("scope", ""),
            }
        return {
            "configured": True,
            "status": "active",
            "expires_in_seconds": int(remaining),
            "scope": token_data.get("scope", ""),
        }

    return {
        "configured": True,
        "status": "active",
        "expires_in_seconds": None,  # no expiry info
        "scope": token_data.get("scope", ""),
    }
