"""Real stdio round trip through the official mcp SDK client (skipped if mcp is absent)."""
import sys
import textwrap

import pytest

pytest.importorskip("mcp.client.stdio")

import amira_mcp


def _echo_server(tmp_path):
    try:
        from mcp.server.mcpserver import MCPServer  # noqa: F401  (mcp 2.x)
        imp = "from mcp.server.mcpserver import MCPServer as App"
    except ImportError:
        imp = "from mcp.server.fastmcp import FastMCP as App"  # mcp 1.x
    path = tmp_path / "echo_server.py"
    path.write_text(textwrap.dedent(f"""
        {imp}
        app = App("echo")

        @app.tool()
        def echo(text: str) -> str:
            \"\"\"Echo text back.\"\"\"
            return "ECHO:" + text

        app.run()
    """))
    return str(path)


def test_manager_stdio_round_trip_with_underscore_server(tmp_path):
    mgr = amira_mcp.MCPManager()
    cfg = {"command": sys.executable, "args": [_echo_server(tmp_path)]}
    assert mgr.add_server("my_echo", "stdio", cfg)
    try:
        assert "mcp_my_echo_echo" in mgr.get_all_tools()
        assert "ECHO:hi" in mgr.call_tool("mcp_my_echo_echo", {"text": "hi"})
    finally:
        for s in mgr.servers.values():
            s.disconnect()
