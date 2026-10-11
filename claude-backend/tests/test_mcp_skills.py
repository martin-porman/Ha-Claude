"""Tests for Amira MCP tool routing and the skill store index fetch."""

import json
import urllib.error

import pytest

import amira_mcp
import skills


class _StubServer:
    def __init__(self, tools):
        self.tools = {t: {"inputSchema": {}} for t in tools}
        self.calls = []

    def call_tool(self, tool_name, arguments):
        self.calls.append(tool_name)
        return json.dumps({"ok": tool_name})

    def is_connected(self):
        return True


def _manager(**servers):
    m = amira_mcp.MCPManager()
    m.servers.update(servers)
    return m


def test_server_name_with_underscore_routes():
    srv = _StubServer(["read_file"])
    m = _manager(home_files=srv)
    assert json.loads(m.call_tool("mcp_home_files_read_file", {})) == {"ok": "read_file"}
    assert srv.calls == ["read_file"]


def test_longest_server_prefix_wins():
    foo, foo_bar = _StubServer(["bar_baz"]), _StubServer(["baz"])
    m = _manager(foo=foo, foo_bar=foo_bar)
    m.call_tool("mcp_foo_bar_baz", {})
    assert foo_bar.calls == ["baz"] and foo.calls == []
    m.call_tool("mcp_foo_qux", {})
    assert foo.calls == ["qux"]


def test_unknown_server_and_bad_prefix():
    m = _manager(home_files=_StubServer(["x"]))
    assert "not found" in json.loads(m.call_tool("mcp_other_x", {}))["error"]
    assert "Invalid tool name" in json.loads(m.call_tool("home_files_x", {}))["error"]


@pytest.fixture(autouse=True)
def _reset_store_cache():
    skills._store_cache.update(ts=0.0, data=None, error=None)
    yield
    skills._store_cache.update(ts=0.0, data=None, error=None)


def test_store_index_parsed(monkeypatch):
    body = json.dumps({"skills": [{"name": "a"}, {"name": "b"}]}).encode()
    seen = []
    monkeypatch.setattr(skills, "_http_get", lambda url: seen.append(url) or body)
    result = skills.fetch_store_index(installed_names={"a"})
    assert seen == [skills.SKILLS_INDEX_URL]
    assert [(s["name"], s["installed"]) for s in result] == [("a", True), ("b", False)]


def test_store_404_gives_clear_message_and_is_cached(monkeypatch):
    calls = []

    def _404(url):
        calls.append(url)
        raise urllib.error.HTTPError(url, 404, "Not Found", None, None)

    monkeypatch.setattr(skills, "_http_get", _404)
    with pytest.raises(skills.SkillStoreUnavailable) as exc:
        skills.fetch_store_index()
    assert "HTTP 404" in str(exc.value) and skills.SKILLS_INDEX_URL in str(exc.value)
    with pytest.raises(skills.SkillStoreUnavailable):
        skills.fetch_store_index()
    assert len(calls) == 1


def test_store_requests_http_error_status(monkeypatch):
    class _Resp:
        status_code = 503

    class _Err(Exception):
        response = _Resp()

    def _fail(url):
        raise _Err("boom")

    monkeypatch.setattr(skills, "_http_get", _fail)
    with pytest.raises(skills.SkillStoreUnavailable, match="HTTP 503"):
        skills.fetch_store_index()


def test_store_bad_json(monkeypatch):
    monkeypatch.setattr(skills, "_http_get", lambda url: b"<html>")
    with pytest.raises(skills.SkillStoreUnavailable, match="Skill store unavailable"):
        skills.fetch_store_index()
