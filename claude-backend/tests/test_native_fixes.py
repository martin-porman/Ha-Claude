"""Native Claude Code fixes: provider-locked fallback (#4) and startup settings overlay (#8)."""

import json
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import model_fallback  # noqa: E402
import services.settings_service as settings_service  # noqa: E402


class _Ref:
    def __init__(self, provider, model):
        self.provider = provider
        self.model = model


@pytest.fixture
def agent_chain(monkeypatch):
    """Agent fallbacks and global defaults that mix providers."""
    agent = types.SimpleNamespace(model_config=types.SimpleNamespace(fallbacks=[
        _Ref("openai", "gpt-4o"), _Ref("claude_code", "haiku"),
    ]))
    defaults = types.SimpleNamespace(model=types.SimpleNamespace(
        fallbacks=[_Ref("anthropic", "claude-sonnet-5"), _Ref("claude_code", "opus")],
        primary=_Ref("groq", "llama"),
    ))
    mgr = types.SimpleNamespace(resolve_agent=lambda _id: agent, get_defaults=lambda: defaults)
    stub = types.ModuleType("agent_config")
    stub.get_agent_manager = lambda: mgr
    monkeypatch.setitem(sys.modules, "agent_config", stub)


def _keys(cands):
    return [c.key() for c in cands]


def test_native_claude_code_falls_back_only_to_claude_code(monkeypatch, agent_chain):
    monkeypatch.setenv("CLAUDE_CODE_NATIVE_TOOLS", "true")
    cands = model_fallback.resolve_candidates("claude_code", "sonnet", agent_id="a")
    assert _keys(cands) == ["claude_code/sonnet", "claude_code/haiku", "claude_code/opus"]


def test_native_claude_code_override_drops_other_providers(monkeypatch, agent_chain):
    monkeypatch.setenv("CLAUDE_CODE_NATIVE_TOOLS", "true")
    cands = model_fallback.resolve_candidates(
        "claude_code", "sonnet", fallbacks_override=["openai/gpt-4o", "opus", "claude_code/haiku"])
    assert _keys(cands) == ["claude_code/sonnet", "claude_code/opus", "claude_code/haiku"]


def test_non_native_claude_code_keeps_cross_provider_fallbacks(monkeypatch, agent_chain):
    monkeypatch.setenv("CLAUDE_CODE_NATIVE_TOOLS", "false")
    cands = model_fallback.resolve_candidates("claude_code", "sonnet", agent_id="a")
    assert "openai/gpt-4o" in _keys(cands)
    assert "groq/llama" in _keys(cands)


def test_other_provider_unaffected_by_native_flag(monkeypatch, agent_chain):
    monkeypatch.setenv("CLAUDE_CODE_NATIVE_TOOLS", "true")
    cands = model_fallback.resolve_candidates("openai", "gpt-4o-mini", agent_id="a")
    assert "anthropic/claude-sonnet-5" in _keys(cands)
    assert "claude_code/haiku" in _keys(cands)


def test_native_streaming_fallback_never_runs_other_provider(monkeypatch, agent_chain):
    monkeypatch.setenv("CLAUDE_CODE_NATIVE_TOOLS", "true")
    calls = []

    def run(p, m):
        calls.append(f"{p}/{m}")
        raise RuntimeError("boom")

    model_fallback.clear_all_cooldowns()
    try:
        result = model_fallback.run_with_model_fallback_streaming(
            provider="claude_code", model="sonnet", run=run, agent_id="a")
    finally:
        model_fallback.clear_all_cooldowns()
    assert not result.success
    assert calls and all(c.startswith("claude_code/") for c in calls)


def _write_settings(tmp_path, monkeypatch, data):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(settings_service, "SETTINGS_FILE", str(path))


def test_startup_overlay_only_contains_saved_keys(tmp_path, monkeypatch):
    _write_settings(tmp_path, monkeypatch, {"language": "it", "dark_mode": True, "unknown_key": 1})
    overlay = settings_service._startup_settings_overlay()
    # dark_mode has no global mapping; unknown keys are ignored.
    assert overlay == {"language": "it"}
    for key in ("enable_mcp", "enable_memory", "max_conversations"):
        assert key not in overlay


def test_startup_overlay_keeps_saved_values(tmp_path, monkeypatch):
    _write_settings(tmp_path, monkeypatch, {"enable_mcp": False, "max_conversations": 42})
    assert settings_service._startup_settings_overlay() == {"enable_mcp": False, "max_conversations": 42}


def test_startup_overlay_missing_file_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_service, "SETTINGS_FILE", str(tmp_path / "absent.json"))
    assert settings_service._startup_settings_overlay() == {}
