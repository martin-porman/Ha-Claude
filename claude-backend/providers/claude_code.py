"""Claude Code provider — chat with Claude using a Claude Pro/Max SUBSCRIPTION.

This provider runs the official Claude Code CLI (`claude`) headless, exactly as
Anthropic ships it. It never calls the Messages API directly with OAuth tokens
and never imitates Claude Code: every request is a real `claude -p` process.

Authentication (first match wins):
  1. Add-on option `claude_code_oauth_token` → env CLAUDE_CODE_OAUTH_TOKEN
  2. Token pasted in the Amira UI (🔑) → /data/claude_code_token.json (chmod 600)
  3. Credentials stored by `claude auth login` in $CLAUDE_CONFIG_DIR

To get a long-lived token, run `claude setup-token` on any computer with
Claude Code installed and sign in with your Claude Pro/Max account; it prints
a token starting with sk-ant-oat01-.

Each request is stateless: the conversation (including tool-simulator results)
is flattened into one prompt fed on stdin. With CLAUDE_CODE_NATIVE_TOOLS=true the
CLI keeps its built-in tools, settings, MCP and skills and runs without permission
prompts; otherwise built-in tools are disabled (--tools "") and Home Assistant
tools go through the tool simulator, like the other no-native-tool providers.
"""

import json
import logging
import os
import queue
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from typing import Any, Dict, Generator, List, Optional, Tuple

from .enhanced import EnhancedProvider

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------
_TOKEN_FILE = "/data/claude_code_token.json"
_PERSISTENT_BIN = "/data/claude-home/.local/bin/claude"   # maintained by the s6 run script
_WORK_DIR = "/data/claude-work"                            # empty cwd → no CLAUDE.md discovered
_TOKEN_PREFIX = "sk-ant-oat"
_TOKEN_MIN_LEN = 40
_TOKEN_MAX_LEN = 512
_DEFAULT_MODEL = "sonnet"
_DEFAULT_TIMEOUT = 300.0
# Linux caps a single argv string at 128 KiB (MAX_ARG_STRLEN); stay well below.
_SYSTEM_PROMPT_ARGV_LIMIT = 32 * 1024

# Env vars that would make the CLI bill an API key / another endpoint instead
# of the user's subscription.
_STRIPPED_ENV = (
    "ANTHROPIC_API_KEY",
    "CLAUDE_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
)

_AUTH_ERROR_MARKERS = (
    "401",
    "authentication",
    "oauth",
    "invalid api key",
    "please run /login",
    "not logged in",
)
_RATE_LIMIT_MARKERS = ("usage limit", "rate limit", "rate_limit", "429", "limit reached")

_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\[\]-]*$")

AUTH_HELP = (
    "Claude Code: not signed in to a Claude subscription. On any computer with "
    "Claude Code, run `claude setup-token`, sign in with your Claude Pro/Max "
    "account and paste the sk-ant-oat01-… token via the 🔑 button (or the "
    "claude_code_oauth_token add-on option)."
)

_binary_cache: Dict[str, Tuple[float, bool, str]] = {}   # path → (mtime, runs, version)


# ---------------------------------------------------------------------------
# Binary resolution
# ---------------------------------------------------------------------------

def _probe_binary(path: str) -> Tuple[bool, str]:
    """Return (runs, version) for a claude binary, cached by mtime."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return False, ""
    cached = _binary_cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1], cached[2]
    runs, version = False, ""
    try:
        out = subprocess.run(
            [path, "--version"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
        )
        runs = out.returncode == 0
        version = (out.stdout or "").strip().splitlines()[0] if runs and out.stdout else ""
    except Exception as e:
        logger.debug(f"ClaudeCode: {path} --version failed: {e}")
    _binary_cache[path] = (mtime, runs, version)
    return runs, version


def resolve_binary() -> Optional[str]:
    """Locate the claude binary: CLAUDE_CODE_BIN → persistent /data install → PATH."""
    override = os.getenv("CLAUDE_CODE_BIN", "").strip()
    if override:
        if os.path.isfile(override) and os.access(override, os.X_OK):
            return override
        return shutil.which(override)
    if os.path.isfile(_PERSISTENT_BIN) and os.access(_PERSISTENT_BIN, os.X_OK):
        runs, _ = _probe_binary(_PERSISTENT_BIN)
        if runs:
            return _PERSISTENT_BIN
    return shutil.which("claude")


def get_cli_version(binary: Optional[str] = None) -> str:
    binary = binary or resolve_binary()
    if not binary:
        return ""
    return _probe_binary(binary)[1]


# ---------------------------------------------------------------------------
# Token handling
# ---------------------------------------------------------------------------

def _config_dir() -> str:
    return os.getenv("CLAUDE_CONFIG_DIR", "").strip() or os.path.expanduser("~/.claude")


def _cli_credentials_present() -> bool:
    return os.path.isfile(os.path.join(_config_dir(), ".credentials.json"))


def _load_stored_token() -> str:
    try:
        if os.path.exists(_TOKEN_FILE):
            with open(_TOKEN_FILE, encoding="utf-8") as f:
                return str((json.load(f) or {}).get("token", "")).strip()
    except Exception as e:
        logger.warning(f"ClaudeCode: could not load stored token: {e}")
    return ""


def get_token() -> Tuple[str, str]:
    """Return (token, source) where source is env | stored | cli | ''."""
    env_token = os.getenv("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
    if env_token:
        return env_token, "env"
    stored = _load_stored_token()
    if stored:
        return stored, "stored"
    if _cli_credentials_present():
        return "", "cli"
    return "", ""


def validate_token_format(token: str) -> str:
    """Return the cleaned token or raise ValueError."""
    tok = (token or "").strip()
    if not tok:
        raise ValueError("Empty token")
    if not tok.startswith(_TOKEN_PREFIX):
        raise ValueError("Invalid token: expected a `claude setup-token` token starting with sk-ant-oat01-")
    if not (_TOKEN_MIN_LEN <= len(tok) <= _TOKEN_MAX_LEN) or any(c.isspace() for c in tok):
        raise ValueError("Invalid token length/format — copy the whole sk-ant-oat01-… token")
    return tok


def store_token(token: str) -> Dict[str, Any]:
    """Validate and persist a setup-token (file mode 600)."""
    tok = validate_token_format(token)
    os.makedirs(os.path.dirname(_TOKEN_FILE) or ".", exist_ok=True)
    tmp = _TOKEN_FILE + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"token": tok, "stored_at": int(time.time())}, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, _TOKEN_FILE)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    logger.info("ClaudeCode: setup-token stored")
    return {"ok": True, "configured": True, "source": "stored"}


def clear_token() -> None:
    """Remove the token stored via the UI (env/add-on option and CLI login are untouched)."""
    try:
        if os.path.exists(_TOKEN_FILE):
            os.remove(_TOKEN_FILE)
    except Exception as e:
        logger.warning(f"ClaudeCode: could not remove stored token: {e}")


def is_available() -> bool:
    return bool(resolve_binary()) and bool(get_token()[1])


# ---------------------------------------------------------------------------
# Subprocess environment
# ---------------------------------------------------------------------------

def build_env(token: str = "") -> Dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _STRIPPED_ENV}
    env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    if token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    env["DISABLE_AUTOUPDATER"] = "1"
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    return env


def native_tools_enabled() -> bool:
    return os.getenv("CLAUDE_CODE_NATIVE_TOOLS", "false").strip().lower() == "true"


def _work_dir() -> str:
    path = os.getenv("CLAUDE_CODE_WORK_DIR", "").strip() or _WORK_DIR
    try:
        os.makedirs(path, exist_ok=True)
        return path
    except OSError:
        fallback = os.path.join(tempfile.gettempdir(), "amira-claude-work")
        os.makedirs(fallback, exist_ok=True)
        return fallback


def get_status() -> Dict[str, Any]:
    """Status for the UI. Never includes the token."""
    binary = resolve_binary()
    token, source = get_token()
    status: Dict[str, Any] = {
        "installed": bool(binary),
        "version": get_cli_version(binary) if binary else "",
        "binary": binary or "",
        "source": source,
        "logged_in": False,
        "auth_method": "none",
        "configured": False,
    }
    if not binary:
        status["error"] = "Claude Code CLI not found in the add-on image"
        return status
    try:
        out = subprocess.run(
            [binary, "auth", "status", "--json"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=15,
            env=build_env(token),
            cwd=_work_dir(),
        )
        data = json.loads(out.stdout or "{}")
        status["logged_in"] = bool(data.get("loggedIn"))
        status["auth_method"] = data.get("authMethod") or "none"
        if data.get("subscriptionType"):
            status["subscription"] = data.get("subscriptionType")
    except subprocess.TimeoutExpired:
        status["error"] = "claude auth status timed out"
    except Exception as e:
        status["error"] = f"claude auth status failed: {e}"
    status["configured"] = status["logged_in"]
    return status


# ---------------------------------------------------------------------------
# Provider class
# ---------------------------------------------------------------------------

class ClaudeCodeProvider(EnhancedProvider):
    """Claude subscription via the official Claude Code CLI (headless)."""

    _MODELS = ["sonnet", "opus", "fable", "haiku",
               "claude-opus-5-5", "claude-sonnet-5", "claude-fable-5-1", "claude-haiku-4-5"]

    def __init__(self, api_key: str = "", model: str = ""):
        super().__init__(api_key, model)
        try:
            self.timeout = float(os.getenv("CLAUDE_CODE_TIMEOUT", "") or _DEFAULT_TIMEOUT)
        except ValueError:
            self.timeout = _DEFAULT_TIMEOUT

    @staticmethod
    def get_provider_name() -> str:
        return "claude_code"

    def validate_credentials(self) -> Tuple[bool, str]:
        if not resolve_binary():
            return False, "Claude Code CLI not installed"
        if not get_token()[1]:
            return False, AUTH_HELP
        return True, ""

    def get_available_models(self) -> List[str]:
        return list(self._MODELS)

    def _resolve_model(self) -> str:
        m = (self.model or "").strip()
        for prefix in ("claude_code/", "claude-code/"):
            if m.startswith(prefix):
                m = m[len(prefix):]
        return m or _DEFAULT_MODEL

    # EnhancedProvider calls _do_stream from stream_chat_with_caching
    def _do_stream(
        self,
        messages: List[Dict[str, Any]],
        intent_info: Optional[Dict[str, Any]] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        yield from self.stream_chat(messages, intent_info)

    # ------------------------------------------------------------------
    # Prompt building
    # ------------------------------------------------------------------

    @staticmethod
    def _text_of(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text" or "text" in block:
                        parts.append(str(block.get("text", "")))
                    elif block.get("type") in ("image", "image_url"):
                        parts.append("[image omitted]")
                elif isinstance(block, str):
                    parts.append(block)
            return "\n".join(p for p in parts if p)
        return "" if content is None else str(content)

    def _build_system_prompt(self, base_system: str, intent_info: Optional[Dict[str, Any]]) -> str:
        intent_name = (intent_info or {}).get("intent", "")
        tool_schemas = (intent_info or {}).get("tool_schemas") or []

        if intent_name == "create_html_dashboard":
            head = (
                "You are a creative Home Assistant HTML dashboard designer.\n"
                "The user wants a UNIQUE, beautiful STANDALONE HTML page — NOT YAML, NOT a Lovelace card.\n\n"
                "MANDATORY RULES:\n"
                "• Output a COMPLETE <!DOCTYPE html>...</html> page wrapped in ```html ... ```\n"
                "• YOUR FIRST LINE OF OUTPUT MUST BE: ```html\n"
                "• NEVER output YAML or ANY Lovelace / Home Assistant card format\n"
                "• Do NOT produce JSON, markdown lists, or explanatory text — ONLY the HTML block\n"
                "• Use a modern dark design with CSS animations, gradients, and card-based layout\n"
                "• Poll HA states via: fetch('/api/states/ENTITY_ID', {headers:{Authorization:'Bearer '+tok}})\n"
                "  where tok comes from: localStorage.getItem('hassTokens') parsed as JSON .access_token\n"
                "• Refresh every 5 seconds with setInterval\n"
                "• Include ALL the entity_ids provided in the CONTEXT section of the user message\n"
                "• The HTML is automatically saved — no tool call, no explanation needed\n"
            )
        else:
            intent_base_prompt = (intent_info or {}).get("prompt", "")
            if (intent_info or {}).get("active_skill"):
                head = intent_base_prompt or ""
            elif native_tools_enabled():
                head = (
                    "You are running inside a Home Assistant add-on called Amira, as root, with "
                    "Claude Code's built-in tools (Bash, Read, Write, Edit, Glob, Grep, WebFetch) and "
                    "no permission prompts. Use them to act directly; do NOT emit <tool_call> XML.\n"
                    "- HA config: /config (also /share, /ssl, /media, /backup, /addons, /addon_configs)\n"
                    "- HA REST: http://supervisor/core/api/ and WebSocket: ws://supervisor/core/websocket, "
                    "auth with $SUPERVISOR_TOKEN (python3 has websocket-client and aiohttp)\n"
                    "- Supervisor API: http://supervisor/ with $SUPERVISOR_TOKEN\n"
                    "- HAOS host (outside this container): run `hostsh '<command>'` — root shell on the host with host mounts, network and processes (e.g. `hostsh 'ls /'`)\n"
                    "- Persistent workspace for git clones and indexing: $AMIRA_WORKSPACE (/share/amira-workspace). "
                    "Clone repos there, then index with the codebase-memory MCP and query the graph instead of grepping.\n"
                    "- Web search: the kindly-web-search MCP (get_content for a known URL, web_search to discover sources).\n"
                    "- Self-improvement: you may install and keep skills, plugins and MCP servers. Skills go in "
                    "/data/claude/skills/<name>/SKILL.md; plugins via `claude plugin marketplace add` / `claude plugin install`; "
                    "MCP servers via `claude mcp add` or /data/claude/.claude.json. npm (-g) and uv/uvx installs persist under /data. "
                    "All of /data and /share survive restarts and add-on updates; the container filesystem does not.\n"
                    "Read live state before answering questions about devices; never guess."
                )
                if intent_base_prompt:
                    head = intent_base_prompt + "\n\n" + head
            else:
                from providers.tool_simulator import get_simulator_system_prompt
                head = (
                    "You are running inside a Home Assistant integration called Amira. "
                    "You have NO built-in tools here; for Home Assistant operations use ONLY "
                    "<tool_call> XML blocks as described below. Writing 'done' without a "
                    "<tool_call> does NOT change anything in Home Assistant.\n\n"
                    + get_simulator_system_prompt(tool_schemas)
                )
                if intent_base_prompt:
                    head = intent_base_prompt + "\n\n" + head

        system = head
        if base_system:
            system += "\n\n" + base_system
        return system

    def _build_prompt(self, turns: List[Dict[str, Any]], intent_info: Optional[Dict[str, Any]]) -> str:
        history: List[str] = []
        last_user = ""
        for m in turns:
            role = m.get("role", "")
            text = self._text_of(m.get("content", ""))
            if role == "user":
                last_user = text
                history.append(f"Human: {text}")
            elif role == "assistant":
                history.append(f"Assistant: {text}")

        if history and history[-1].startswith("Human: "):
            prior = history[:-1]
        else:
            prior, last_user = history, ""

        if prior:
            prompt = (
                "[CONVERSATION HISTORY]\n" + "\n\n".join(prior) + "\n[/CONVERSATION HISTORY]\n\n"
                f"Human: {last_user}"
            )
        else:
            prompt = last_user

        active_skill = (intent_info or {}).get("active_skill")
        if active_skill:
            prompt += (
                f"\n\n[⚠️ SKILL ACTIVE: {active_skill} — ONLY output `type: custom:{active_skill}` "
                f"cards — ALWAYS wrap YAML in ```yaml fences]"
            )
        # A prompt starting with "/" is parsed by the CLI as a slash command.
        if prompt.lstrip().startswith("/"):
            prompt = "Human: " + prompt
        return prompt

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    @staticmethod
    def _map_error(message: str) -> str:
        low = (message or "").lower()
        if any(m in low for m in _AUTH_ERROR_MARKERS):
            return f"{AUTH_HELP}\n(CLI: {message.strip()[:200]})"
        if any(m in low for m in _RATE_LIMIT_MARKERS):
            return f"Claude Code: subscription usage limit reached — {message.strip()[:300]}"
        return f"Claude Code error: {message.strip()[:500]}"

    @staticmethod
    def _kill(proc: subprocess.Popen) -> None:
        if proc.poll() is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass
        try:
            proc.wait(timeout=5)
        except Exception:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        intent_info: Optional[Dict[str, Any]] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        binary = resolve_binary()
        if not binary:
            yield {
                "type": "error",
                "message": "Claude Code: CLI binary not found. Rebuild/update the add-on or set CLAUDE_CODE_BIN.",
            }
            return

        token, source = get_token()
        if not source:
            yield {"type": "error", "message": AUTH_HELP}
            return

        model = self._resolve_model()
        if not _MODEL_RE.match(model):
            yield {"type": "error", "message": f"Claude Code: invalid model name '{model}'"}
            return

        from providers.tool_simulator import flatten_tool_messages
        flat = flatten_tool_messages(messages)
        base_system = "\n\n".join(
            self._text_of(m.get("content", "")) for m in flat if m.get("role") == "system"
        ).strip()
        turns = [m for m in flat if m.get("role") in ("user", "assistant")]
        system_prompt = self._build_system_prompt(base_system, intent_info)
        prompt = self._build_prompt(turns, intent_info)
        if not prompt.strip():
            yield {"type": "error", "message": "Claude Code: empty prompt"}
            return

        cmd = [
            binary, "-p",
            "--output-format", "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--model", model,
            "--no-session-persistence",
        ]
        if native_tools_enabled():
            # Full Claude Code: built-in tools, settings, MCP, skills; no permission prompts.
            cmd += ["--dangerously-skip-permissions"]
        else:
            cmd += [
                "--tools", "",
                "--setting-sources", "",
                "--strict-mcp-config",
                "--disable-slash-commands",
            ]
        sp_file = None
        if len(system_prompt.encode("utf-8")) > _SYSTEM_PROMPT_ARGV_LIMIT:
            fd, sp_file = tempfile.mkstemp(prefix="amira-sp-", suffix=".txt")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(system_prompt)
            cmd += ["--system-prompt-file", sp_file]
        else:
            cmd += ["--system-prompt", system_prompt]

        logger.info(
            f"ClaudeCode: running CLI (model={model}, auth={source}, "
            f"prompt={len(prompt)} chars, system={len(system_prompt)} chars)"
        )

        proc: Optional[subprocess.Popen] = None
        try:
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=build_env(token),
                    cwd=_work_dir(),
                    start_new_session=True,
                )
            except OSError as e:
                yield {"type": "error", "message": f"Claude Code: could not start CLI: {e}"}
                return

            # Let Stop reach the CLI even while this generator is parked on a read.
            try:
                from services import turn_context
                turn_context.register(proc)
            except Exception:
                pass

            yield from self._pump(proc, prompt)
        finally:
            if proc is not None:
                try:
                    from services import turn_context
                    turn_context.unregister(proc)
                except Exception:
                    pass
                self._kill(proc)
            if sp_file:
                try:
                    os.remove(sp_file)
                except OSError:
                    pass

    def _pump(self, proc: subprocess.Popen, prompt: str) -> Generator[Dict[str, Any], None, None]:
        lines: "queue.Queue[Optional[bytes]]" = queue.Queue()
        stderr_chunks: List[bytes] = []

        def _write_stdin():
            try:
                proc.stdin.write(prompt.encode("utf-8"))
            except Exception:
                pass
            finally:
                try:
                    proc.stdin.close()
                except Exception:
                    pass

        def _read_stdout():
            try:
                for raw in iter(proc.stdout.readline, b""):
                    lines.put(raw)
            finally:
                lines.put(None)

        def _read_stderr():
            try:
                for raw in iter(proc.stderr.readline, b""):
                    if sum(len(c) for c in stderr_chunks) < 16384:
                        stderr_chunks.append(raw)
            except Exception:
                pass

        for target in (_write_stdin, _read_stdout, _read_stderr):
            threading.Thread(target=target, daemon=True).start()

        deadline = time.monotonic() + self.timeout
        streamed_text = False
        result: Optional[Dict[str, Any]] = None

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._kill(proc)
                yield {"type": "error", "message": f"Claude Code: no response within {int(self.timeout)}s (timeout)"}
                return
            try:
                raw = lines.get(timeout=remaining)
            except queue.Empty:
                continue
            if raw is None:
                break
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                logger.debug(f"ClaudeCode: non-JSON line: {line[:200]}")
                continue

            etype = evt.get("type")
            if etype == "stream_event":
                inner = evt.get("event") or {}
                if inner.get("type") == "content_block_delta":
                    delta = inner.get("delta") or {}
                    if delta.get("type") == "text_delta" and delta.get("text"):
                        streamed_text = True
                        yield {"type": "text", "text": delta["text"]}
            elif etype == "system" and evt.get("subtype") == "api_retry":
                status = evt.get("error_status")
                if status in (401, 403):
                    self._kill(proc)
                    yield {"type": "error", "message": self._map_error(f"{status} {evt.get('error', 'authentication_failed')}")}
                    return
                logger.warning(f"ClaudeCode: API retry {evt.get('attempt')}: {status} {evt.get('error')}")
            elif etype == "result":
                result = evt
                break

        if result is None:
            try:
                rc = proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                rc = None
            err = b"".join(stderr_chunks).decode("utf-8", errors="replace").strip()
            msg = err[-500:] if err else "no output"
            if rc not in (0, None):
                yield {"type": "error", "message": self._map_error(f"CLI exited with code {rc}: {msg}")}
            else:
                yield {"type": "error", "message": self._map_error(f"CLI ended without a result: {msg}")}
            return

        if result.get("is_error") or (result.get("subtype") or "success") != "success":
            msg = result.get("result") or "; ".join(str(e) for e in (result.get("errors") or [])) \
                or str(result.get("subtype") or "unknown error")
            yield {"type": "error", "message": self._map_error(str(msg))}
            return

        if not streamed_text and result.get("result"):
            yield {"type": "text", "text": result["result"]}

        done: Dict[str, Any] = {"type": "done", "finish_reason": "stop"}
        usage = result.get("usage") or {}
        if usage:
            done["usage"] = {
                "input_tokens": usage.get("input_tokens", 0) or 0,
                "output_tokens": usage.get("output_tokens", 0) or 0,
                "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0) or 0,
                "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0) or 0,
            }
        yield done

    def get_error_translations(self) -> Dict[str, Dict[str, str]]:
        return {
            "auth_error": {
                "en": "Claude Code: not signed in. Paste a `claude setup-token` token via the 🔑 button.",
                "it": "Claude Code: non autenticato. Incolla un token `claude setup-token` con il pulsante 🔑.",
                "es": "Claude Code: sin autenticar. Pega un token de `claude setup-token` con 🔑.",
                "fr": "Claude Code : non authentifié. Collez un jeton `claude setup-token` via 🔑.",
            },
        }
