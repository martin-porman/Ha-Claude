"""Prompt-building tests for the web-login providers and Ollama (audit #19).

No network: the transport functions are monkeypatched to capture the payload.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from providers import chatgpt_web, claude_web, gemini_web, ollama, perplexity_web

SYS_A = "SYSTEM-ALPHA instructions"
SYS_B = "SYSTEM-BETA memory block"
CONTINUE_MARK = "Continue. Use the tool results above"


def _tool_round_messages():
    return [
        {"role": "system", "content": SYS_A},
        {"role": "system", "content": SYS_B},
        {"role": "user", "content": "Q1"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "get_entities", "arguments": "{}"},
            }],
        },
        {"role": "tool", "tool_call_id": "call_1", "name": "get_entities", "content": "RESULT-XYZ"},
    ]


def _tool_round_messages_with_text():
    return _tool_round_messages() + [{"role": "assistant", "content": "..."}]


VARIANTS = [_tool_round_messages, _tool_round_messages_with_text]


class _NoLimit:
    def can_request(self):
        return True, 0

    def record_request(self):
        pass


def _drain(gen):
    return list(gen)


# ── claude_web ──────────────────────────────────────────────────────────────

def _claude_capture(monkeypatch):
    captured = {"system": [], "prompts": []}
    monkeypatch.setattr(claude_web, "_stored_session", {"session_key": "k", "org_uuid": "org"})
    monkeypatch.setattr(claude_web, "HTTPX_AVAILABLE", True)

    def fake_create(session_key, org_uuid, model, system_prompt=""):
        captured["system"].append(system_prompt)
        return "conv-new"

    monkeypatch.setattr(claude_web, "_create_conversation", fake_create)
    p = claude_web.ClaudeWebProvider()
    p.rate_limiter = _NoLimit()

    def fake_stream(url, session_key, body):
        captured["prompts"].append(body["prompt"])
        yield {"type": "done"}

    monkeypatch.setattr(p, "_stream_response", fake_stream)
    return p, captured


@pytest.mark.parametrize("build", VARIANTS)
def test_claude_web_fresh_conversation_keeps_tool_results_and_systems(monkeypatch, build):
    p, cap = _claude_capture(monkeypatch)
    _drain(p.stream_chat(build(), {"intent": "auto"}))
    prompt = cap["prompts"][-1]
    assert "RESULT-XYZ" in prompt
    assert SYS_A in cap["system"][-1] and SYS_B in cap["system"][-1]
    assert prompt.rstrip().endswith("original request.")
    assert not prompt.rstrip().endswith("Human: Q1")


@pytest.mark.parametrize("build", VARIANTS)
def test_claude_web_reused_conversation_sends_tool_results(monkeypatch, build):
    p, cap = _claude_capture(monkeypatch)
    claude_web._conv_cache_set("sess-1", "conv-cached", "auto", p._resolve_model(), "org")
    try:
        _drain(p.stream_chat(build(), {"intent": "auto", "session_id": "sess-1"}))
    finally:
        claude_web._conv_cache_invalidate("sess-1")
    prompt = cap["prompts"][-1]
    assert cap["system"] == []  # reused, no new conversation
    assert "RESULT-XYZ" in prompt
    assert prompt.strip() != "Q1"
    assert CONTINUE_MARK in prompt


def test_claude_web_split_messages_joins_all_systems():
    system, rest = claude_web.ClaudeWebProvider._split_messages(None, _tool_round_messages())
    assert SYS_A in system and SYS_B in system
    assert all(m["role"] != "system" for m in rest)


# ── gemini_web ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("build", VARIANTS)
def test_gemini_web_prompt(monkeypatch, build):
    captured = []
    monkeypatch.setattr(gemini_web, "_stored_session", {"psid": "x", "psidts": "y"})
    monkeypatch.setattr(gemini_web, "HTTPX_AVAILABLE", True, raising=False)
    monkeypatch.setattr(gemini_web, "GEMINI_WEBAPI_AVAILABLE", True)

    def fake_send(s, prompt, model=""):
        captured.append(prompt)
        return "answer"

    monkeypatch.setattr(gemini_web, "_send_message_sdk", fake_send)
    p = gemini_web.GeminiWebProvider()
    p.rate_limiter = _NoLimit()
    _drain(p.stream_chat(build(), {"intent": "auto"}))
    prompt = captured[-1]
    assert "RESULT-XYZ" in prompt
    assert SYS_A in prompt and SYS_B in prompt
    assert prompt.rstrip().endswith("original request.")
    assert not prompt.rstrip().endswith("Human: Q1")


def test_gemini_web_split_messages_joins_all_systems():
    system, _ = gemini_web.GeminiWebProvider._split_messages(None, _tool_round_messages())
    assert SYS_A in system and SYS_B in system


# ── chatgpt_web ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("build", VARIANTS)
def test_chatgpt_web_prompt(monkeypatch, build):
    captured = []
    monkeypatch.setattr(chatgpt_web, "CFFI_AVAILABLE", True)
    monkeypatch.setattr(chatgpt_web, "_stored_session", {"access_token": "t"})
    p = chatgpt_web.ChatGPTWebProvider()
    p.rate_limiter = _NoLimit()

    def fake_stream(access_token, body, session_token=None, cf_clearance=None, device_id=None):
        captured.append(body)
        yield {"type": "done"}

    monkeypatch.setattr(p, "_stream_response", fake_stream)
    _drain(p.stream_chat(build(), {"intent": "auto"}))
    msgs = captured[-1]["messages"]
    texts = [m["content"]["parts"][0] for m in msgs]
    joined = "\n".join(texts)
    assert "RESULT-XYZ" in joined
    assert SYS_A in joined and SYS_B in joined
    # The final turn is not the original user question re-sent
    assert not (msgs[-1]["author"]["role"] == "user" and texts[-1].strip().endswith("Q1"))
    # After the tool round the request ends on a user turn (continuation)
    assert msgs[-1]["author"]["role"] == "user"


# ── perplexity_web ──────────────────────────────────────────────────────────

class _CaptureClient:
    captured = []

    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def stream(self, method, url, json=None, **kw):
        _CaptureClient.captured.append(json)
        raise RuntimeError("no network in tests")


@pytest.mark.parametrize("build", VARIANTS)
def test_perplexity_web_prompt(monkeypatch, build):
    _CaptureClient.captured = []
    monkeypatch.setattr(perplexity_web, "_stored_session", {"csrf_token": "c", "session_token": "s"})
    monkeypatch.setattr(perplexity_web.httpx, "Client", _CaptureClient)
    p = perplexity_web.PerplexityWebProvider()
    p.rate_limiter = _NoLimit()
    _drain(p.stream_chat(build(), {"intent": "auto"}))
    q = _CaptureClient.captured[-1]["query_str"]
    assert _CaptureClient.captured[-1]["params"]["is_incognito"] is False
    assert "RESULT-XYZ" in q
    assert SYS_A in q and SYS_B in q
    assert "Q1" in q  # history kept
    assert q.rstrip().endswith("original request.")


# ── ollama ──────────────────────────────────────────────────────────────────

def _ollama_capture(monkeypatch):
    captured = []
    p = ollama.OllamaProvider(model="llama3")

    def fake_stream(base_url, body, timeout, acc, tools=None):
        captured.append(body)
        yield {"type": "done"}

    monkeypatch.setattr(p, "_ollama_stream", fake_stream)
    return p, captured


@pytest.mark.parametrize("build", VARIANTS)
def test_ollama_keeps_systems_tool_calls_and_results(monkeypatch, build):
    p, cap = _ollama_capture(monkeypatch)
    _drain(p._do_stream(build(), {"intent": "auto"}))
    msgs = cap[-1]["messages"]
    assert msgs[0]["role"] == "system"
    assert SYS_A in msgs[0]["content"] and SYS_B in msgs[0]["content"]
    assert sum(1 for m in msgs if m["role"] == "system") == 1
    assert any(m["role"] == "assistant" and m.get("tool_calls") for m in msgs)
    assert any(m["role"] == "tool" and "RESULT-XYZ" in m["content"] for m in msgs)
    assert msgs[-1]["role"] != "user"  # user question not re-sent as the final turn


def test_ollama_does_not_truncate_current_user_message(monkeypatch):
    p, cap = _ollama_capture(monkeypatch)
    long_text = "A" * 4990 + "TAIL-MARK"
    _drain(p._do_stream([{"role": "user", "content": long_text}], None))
    last = cap[-1]["messages"][-1]
    assert last["role"] == "user"
    assert last["content"] == long_text


def test_ollama_window_does_not_start_on_orphan_tool(monkeypatch):
    p, cap = _ollama_capture(monkeypatch)
    msgs = [{"role": "user", "content": f"old {i}"} for i in range(4)]
    msgs += [
        {"role": "user", "content": "Q1"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "a", "type": "function", "function": {"name": "t1", "arguments": "{}"}},
            {"id": "b", "type": "function", "function": {"name": "t2", "arguments": "{}"}},
            {"id": "c", "type": "function", "function": {"name": "t3", "arguments": "{}"}},
            {"id": "e", "type": "function", "function": {"name": "t5", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "a", "name": "t1", "content": "R1"},
        {"role": "tool", "tool_call_id": "b", "name": "t2", "content": "R2"},
        {"role": "tool", "tool_call_id": "c", "name": "t3", "content": "RESULT-XYZ"},
        {"role": "tool", "tool_call_id": "e", "name": "t5", "content": "R5"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "d", "type": "function", "function": {"name": "t4", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "d", "name": "t4", "content": "R4"},
    ]
    _drain(p._do_stream(msgs, None))
    out = cap[-1]["messages"]
    assert out[1]["role"] != "tool"
    assert any(m["role"] == "tool" and "RESULT-XYZ" in m["content"] for m in out)
    # Every tool result in the window follows its assistant tool call
    for i, m in enumerate(out):
        if m["role"] == "tool":
            j = i - 1
            while out[j]["role"] == "tool":
                j -= 1
            assert out[j]["role"] == "assistant" and out[j].get("tool_calls")
