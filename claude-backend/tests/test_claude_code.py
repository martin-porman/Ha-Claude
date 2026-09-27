"""Tests for the claude_code provider (Claude subscription via the Claude Code CLI).

A fake `claude` executable (CLAUDE_CODE_BIN) replays stream-json captured from
a real Claude Code 2.1.283 run and dumps argv/env/stdin for inspection.
"""
import importlib.util
import json
import os
import stat
import sys
import textwrap
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import providers.claude_code as cc  # noqa: E402

VALID_TOKEN = "sk-ant-oat01-" + "A" * 60

_SESSION = "45c6b739-9abc-44ce-84a5-3d367656c116"

# Trimmed lines from a real `claude -p --output-format stream-json --verbose
# --include-partial-messages --model haiku` run.
_OK_LINES = [
    {"type": "system", "subtype": "init", "session_id": _SESSION, "tools": [], "mcp_servers": [],
     "model": "claude-haiku-4-5-20251001", "apiKeySource": "none", "claude_code_version": "2.1.283"},
    {"type": "system", "subtype": "status", "status": "requesting", "session_id": _SESSION},
    {"type": "stream_event", "event": {"type": "message_start", "message": {
        "model": "claude-haiku-4-5-20251001", "type": "message", "role": "assistant", "content": [],
        "usage": {"input_tokens": 410, "output_tokens": 1}}}, "session_id": _SESSION},
    {"type": "stream_event", "event": {"type": "content_block_start", "index": 0,
                                       "content_block": {"type": "thinking", "thinking": ""}}},
    {"type": "stream_event", "event": {"type": "content_block_delta", "index": 0,
                                       "delta": {"type": "thinking_delta", "thinking": "hmm"}}},
    {"type": "stream_event", "event": {"type": "content_block_stop", "index": 0}},
    {"type": "stream_event", "event": {"type": "content_block_start", "index": 1,
                                       "content_block": {"type": "text", "text": ""}}},
    {"type": "stream_event", "event": {"type": "content_block_delta", "index": 1,
                                       "delta": {"type": "text_delta", "text": "Hi"}}},
    {"type": "stream_event", "event": {"type": "content_block_delta", "index": 1,
                                       "delta": {"type": "text_delta", "text": "!"}}},
    {"type": "assistant", "message": {"model": "claude-haiku-4-5-20251001", "role": "assistant",
                                      "content": [{"type": "text", "text": "Hi!"}]}},
    {"type": "stream_event", "event": {"type": "content_block_stop", "index": 1}},
    {"type": "stream_event", "event": {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
                                       "usage": {"input_tokens": 410, "output_tokens": 60}}},
    {"type": "stream_event", "event": {"type": "message_stop"}},
    {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "rateLimitType": "five_hour"}},
    {"type": "result", "subtype": "success", "is_error": False, "result": "Hi!", "num_turns": 1,
     "stop_reason": "end_turn", "total_cost_usd": 0.00071, "api_error_status": None,
     "usage": {"input_tokens": 410, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
               "output_tokens": 60}},
]

# Real output for an invalid CLAUDE_CODE_OAUTH_TOKEN.
_AUTH_LINES = [
    {"type": "system", "subtype": "init", "session_id": _SESSION, "apiKeySource": "none"},
    {"type": "system", "subtype": "status", "status": "requesting", "session_id": _SESSION},
    {"type": "system", "subtype": "api_retry", "attempt": 1, "max_retries": 10, "retry_delay_ms": 615,
     "error_status": 401, "error": "authentication_failed", "session_id": _SESSION},
]

# Real output with no credentials at all.
_NOT_LOGGED_IN_LINES = [
    {"type": "system", "subtype": "init", "session_id": _SESSION, "apiKeySource": "none"},
    {"type": "assistant", "message": {"model": "<synthetic>", "role": "assistant",
                                      "content": [{"type": "text", "text": "Not logged in · Please run /login"}]}},
    {"type": "result", "subtype": "success", "is_error": True, "result": "Not logged in · Please run /login",
     "api_error_status": None, "usage": {"input_tokens": 0, "output_tokens": 0}},
]

_FAKE_CLAUDE = textwrap.dedent('''\
    #!{python}
    import json, os, sys, time
    mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
    if sys.argv[1:] == ["--version"]:
        print("2.1.283 (Claude Code)"); sys.exit(0)
    if sys.argv[1:4] == ["auth", "status", "--json"]:
        tok = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
        print(json.dumps({{"loggedIn": bool(tok), "authMethod": "oauth_token" if tok else "none"}}))
        sys.exit(0 if tok else 1)
    stdin = sys.stdin.read()
    args = sys.argv[1:]
    sp_file = None
    if "--system-prompt-file" in args:
        sp_file = args[args.index("--system-prompt-file") + 1]
    dump = {{"argv": args, "env": dict(os.environ), "stdin": stdin, "cwd": os.getcwd(),
             "sp_file_content": open(sp_file).read() if sp_file else None}}
    with open(os.environ["FAKE_CLAUDE_DUMP"], "w") as f:
        json.dump(dump, f)
    if mode == "crash":
        sys.stderr.write("fatal: something broke\\n"); sys.exit(3)
    if mode == "hang":
        time.sleep(60); sys.exit(0)
    for line in json.load(open(os.environ["FAKE_CLAUDE_LINES"])):
        print(json.dumps(line), flush=True)
    if mode == "auth":
        time.sleep(60)  # the real CLI keeps retrying; provider must abort early
    sys.exit(1 if mode in ("auth", "notlogged") else 0)
''')


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    script = tmp_path / "claude"
    script.write_text(_FAKE_CLAUDE.format(python=sys.executable))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    dump = tmp_path / "dump.json"
    lines_file = tmp_path / "lines.json"

    monkeypatch.setenv("CLAUDE_CODE_BIN", str(script))
    monkeypatch.setenv("FAKE_CLAUDE_DUMP", str(dump))
    monkeypatch.setenv("FAKE_CLAUDE_LINES", str(lines_file))
    monkeypatch.setenv("CLAUDE_CODE_WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", VALID_TOKEN)
    monkeypatch.setattr(cc, "_TOKEN_FILE", str(tmp_path / "claude_code_token.json"))

    class Fake:
        def set(self, lines, mode="ok"):
            lines_file.write_text(json.dumps(lines))
            monkeypatch.setenv("FAKE_CLAUDE_MODE", mode)

        def dump(self):
            return json.loads(dump.read_text())

    f = Fake()
    f.set(_OK_LINES)
    return f


def _run(model="sonnet", messages=None, intent_info=None, timeout=None):
    p = cc.ClaudeCodeProvider(model=model)
    if timeout is not None:
        p.timeout = timeout
    msgs = messages or [{"role": "system", "content": "SYS"}, {"role": "user", "content": "say hi"}]
    return list(p.stream_chat(msgs, intent_info))


def test_streams_text_deltas_and_usage(fake_claude):
    events = _run()
    assert [e["text"] for e in events if e["type"] == "text"] == ["Hi", "!"]
    done = events[-1]
    assert done["type"] == "done" and done["finish_reason"] == "stop"
    assert done["usage"]["input_tokens"] == 410
    assert done["usage"]["output_tokens"] == 60
    assert not [e for e in events if e["type"] == "error"]


def test_command_line_and_stdin(fake_claude):
    _run()
    d = fake_claude.dump()
    argv = d["argv"]
    assert argv[0] == "-p"
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--setting-sources") + 1] == ""
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    for flag in ("--no-session-persistence", "--strict-mcp-config", "--include-partial-messages",
                 "--verbose", "--disable-slash-commands"):
        assert flag in argv
    assert "--bare" not in argv
    sp = argv[argv.index("--system-prompt") + 1]
    assert "SYS" in sp and "<tool_call>" in sp
    assert d["stdin"] == "say hi"
    assert d["cwd"].endswith("work")


def test_env_strips_api_key_and_passes_token(fake_claude, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-should-not-leak")
    monkeypatch.setenv("CLAUDE_API_KEY", "x")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://evil")
    _run()
    env = fake_claude.dump()["env"]
    assert "ANTHROPIC_API_KEY" not in env
    assert "CLAUDE_API_KEY" not in env
    assert "ANTHROPIC_BASE_URL" not in env
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == VALID_TOKEN
    assert env["DISABLE_AUTOUPDATER"] == "1"
    assert env["CLAUDE_CONFIG_DIR"].endswith("cfg")


def test_stored_token_used_when_no_env(fake_claude, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN")
    other = "sk-ant-oat01-" + "B" * 60
    cc.store_token(other)
    assert cc.get_token() == (other, "stored")
    _run()
    assert fake_claude.dump()["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == other


def test_cli_credentials_only(fake_claude, monkeypatch, tmp_path):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN")
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / ".credentials.json").write_text("{}")
    assert cc.get_token() == ("", "cli")
    events = _run()
    assert events[-1]["type"] == "done"
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in fake_claude.dump()["env"]


def test_no_credentials_errors_without_running(fake_claude, monkeypatch, tmp_path):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN")
    events = _run()
    assert events == [{"type": "error", "message": cc.AUTH_HELP}]
    assert not (tmp_path / "dump.json").exists()


def test_auth_retry_401_aborts_early_with_help(fake_claude):
    fake_claude.set(_AUTH_LINES, mode="auth")
    t0 = time.monotonic()
    events = _run()
    assert time.monotonic() - t0 < 20
    assert events[-1]["type"] == "error"
    assert "claude setup-token" in events[-1]["message"]
    assert not [e for e in events if e["type"] == "text"]


def test_not_logged_in_result_maps_to_help(fake_claude):
    fake_claude.set(_NOT_LOGGED_IN_LINES, mode="notlogged")
    events = _run()
    assert len(events) == 1 and events[0]["type"] == "error"
    assert "claude setup-token" in events[0]["message"]
    # the synthetic assistant message must not leak as chat text
    assert "Not logged in" not in "".join(e.get("text", "") for e in events)


def test_nonzero_exit_without_result(fake_claude):
    fake_claude.set([], mode="crash")
    events = _run()
    assert events[-1]["type"] == "error"
    assert "code 3" in events[-1]["message"]
    assert "something broke" in events[-1]["message"]


def test_timeout_kills_process(fake_claude):
    fake_claude.set([], mode="hang")
    t0 = time.monotonic()
    events = _run(timeout=1)
    assert time.monotonic() - t0 < 15
    assert events[-1]["type"] == "error" and "timeout" in events[-1]["message"]


def test_generator_close_kills_process(fake_claude, monkeypatch):
    # Emit the first text delta, then hang like a long generation.
    fake_claude.set(_OK_LINES[:8], mode="auth")
    started = {}
    real_popen = cc.subprocess.Popen

    def spy(*a, **kw):
        started["proc"] = real_popen(*a, **kw)
        return started["proc"]

    monkeypatch.setattr(cc.subprocess, "Popen", spy)
    gen = cc.ClaudeCodeProvider(model="sonnet").stream_chat([{"role": "user", "content": "hi"}])
    assert next(gen) == {"type": "text", "text": "Hi"}
    assert started["proc"].poll() is None
    gen.close()  # client disconnect / abort
    assert started["proc"].poll() is not None


@pytest.mark.parametrize("model,expected", [
    ("claude_code/opus", "opus"),
    ("claude-code/haiku", "haiku"),
    ("", "sonnet"),
    ("claude-sonnet-5", "claude-sonnet-5"),
])
def test_model_prefix_stripping(fake_claude, model, expected):
    _run(model=model)
    argv = fake_claude.dump()["argv"]
    assert argv[argv.index("--model") + 1] == expected


def test_rejects_flag_like_model(fake_claude, tmp_path):
    events = _run(model="--dangerously-skip-permissions")
    assert events[0]["type"] == "error"
    assert not (tmp_path / "dump.json").exists()


def test_history_flattening_and_leading_slash(fake_claude):
    msgs = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "turn on the light"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "1", "function": {"name": "call_service", "arguments": "{}"}}]},
        {"role": "tool", "name": "call_service", "tool_call_id": "1", "content": "ok"},
        {"role": "user", "content": [{"type": "text", "text": "/cost now"}]},
    ]
    _run(messages=msgs)
    stdin = fake_claude.dump()["stdin"]
    assert "[CONVERSATION HISTORY]" in stdin
    assert "Human: turn on the light" in stdin
    assert "[TOOL RESULT: call_service]" in stdin
    assert stdin.rstrip().endswith("Human: /cost now")
    assert not stdin.startswith("/")

    _run(messages=[{"role": "user", "content": "/login"}])
    assert fake_claude.dump()["stdin"] == "Human: /login"


def test_large_system_prompt_uses_file(fake_claude):
    big = "X" * (cc._SYSTEM_PROMPT_ARGV_LIMIT + 10)
    _run(messages=[{"role": "system", "content": big}, {"role": "user", "content": "hi"}])
    d = fake_claude.dump()
    assert "--system-prompt" not in d["argv"]
    sp_path = d["argv"][d["argv"].index("--system-prompt-file") + 1]
    assert big in d["sp_file_content"]
    assert not os.path.exists(sp_path)  # temp file cleaned up


def test_store_and_clear_token_file(tmp_path, monkeypatch):
    path = tmp_path / "sub" / "claude_code_token.json"
    monkeypatch.setattr(cc, "_TOKEN_FILE", str(path))
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    res = cc.store_token("  " + VALID_TOKEN + "\n")
    assert res == {"ok": True, "configured": True, "source": "stored"}
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert json.loads(path.read_text())["token"] == VALID_TOKEN
    cc.clear_token()
    assert not path.exists()


@pytest.mark.parametrize("bad", ["", "sk-ant-sid01-" + "A" * 60, "sk-ant-oat01-short",
                                 "sk-ant-oat01-" + "A" * 30 + " " + "A" * 30])
def test_store_token_rejects_invalid(tmp_path, monkeypatch, bad):
    monkeypatch.setattr(cc, "_TOKEN_FILE", str(tmp_path / "t.json"))
    with pytest.raises(ValueError):
        cc.store_token(bad)
    assert not (tmp_path / "t.json").exists()


def test_env_token_wins_over_stored(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "_TOKEN_FILE", str(tmp_path / "t.json"))
    cc.store_token(VALID_TOKEN)
    env_tok = "sk-ant-oat01-" + "E" * 60
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", env_tok)
    assert cc.get_token() == (env_tok, "env")


def test_missing_binary(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CODE_BIN", str(tmp_path / "nope"))
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", VALID_TOKEN)
    events = list(cc.ClaudeCodeProvider().stream_chat([{"role": "user", "content": "hi"}]))
    assert events[0]["type"] == "error" and "not found" in events[0]["message"]
    assert cc.get_status()["installed"] is False


def _load_oauth_routes():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "routes", "oauth_routes.py")
    spec = importlib.util.spec_from_file_location("_oauth_routes_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_routes_store_status_clear(fake_claude, tmp_path, monkeypatch):
    from flask import Flask
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN")
    app = Flask(__name__)
    app.register_blueprint(_load_oauth_routes().oauth_bp)
    c = app.test_client()

    r = c.get("/api/oauth/claude_code/status").get_json()
    assert r["installed"] is True and r["logged_in"] is False and r["source"] == ""
    assert r["version"].startswith("2.1.283")

    assert c.post("/api/oauth/claude_code/store", json={"token": "nope"}).status_code == 400
    r = c.post("/api/oauth/claude_code/store", json={"token": VALID_TOKEN})
    assert r.status_code == 200 and r.get_json()["ok"] is True

    resp = c.get("/api/oauth/claude_code/status")
    assert VALID_TOKEN not in resp.get_data(as_text=True)
    r = resp.get_json()
    assert r["logged_in"] is True and r["source"] == "stored" and r["auth_method"] == "oauth_token"

    assert c.post("/api/oauth/claude_code/clear").get_json() == {"ok": True}
    assert not (tmp_path / "claude_code_token.json").exists()
