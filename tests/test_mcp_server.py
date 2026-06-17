"""Smoke tests for mcp_server.py: tool registry, helper renderers, and dispatch."""
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tool"))
import mcp_server  # noqa: E402


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# _text / _err renderers
# ---------------------------------------------------------------------------

def test_text_wraps_plain_dict():
    result = mcp_server._text({"foo": "bar"})
    assert len(result) == 1
    body = json.loads(result[0].text)
    assert body["ok"] is True
    assert body["foo"] == "bar"


def test_text_passes_through_ok_dict():
    result = mcp_server._text({"ok": True, "custom": 42})
    body = json.loads(result[0].text)
    assert body["ok"] is True and body["custom"] == 42


def test_err_produces_error_envelope():
    result = mcp_server._err("some_code", "something went wrong")
    body = json.loads(result[0].text)
    assert body["ok"] is False
    assert body["error"]["code"] == "some_code"
    assert "something went wrong" in body["error"]["message"]


# ---------------------------------------------------------------------------
# TOOLS registry
# ---------------------------------------------------------------------------

def test_tools_list_completeness():
    names = {t.name for t in mcp_server.TOOLS}
    required = {
        "bug_hunter.status", "bug_hunter.run_step", "bug_hunter.list_artifacts",
        "bug_hunter.read_artifact", "bug_hunter.write_artifact", "bug_hunter.orchestrate",
    }
    assert required <= names, f"missing tools: {required - names}"


def test_tools_have_valid_schemas():
    for tool in mcp_server.TOOLS:
        schema = tool.inputSchema
        assert schema.get("type") == "object", f"{tool.name}: inputSchema.type must be 'object'"
        assert "additionalProperties" in schema, f"{tool.name}: missing additionalProperties"


# ---------------------------------------------------------------------------
# call_tool dispatch
# ---------------------------------------------------------------------------

def test_call_tool_status():
    with patch("mcp_server.pl") as mock_pl:
        mock_pl.get_state.return_value = {"ok": True, "steps": []}
        result = _run(mcp_server.call_tool("bug_hunter.status", {}))
    body = json.loads(result[0].text)
    assert body["ok"] is True
    mock_pl.get_state.assert_called_once()


def test_call_tool_list_artifacts():
    # Patch only the specific function; leave pl.envelope_success real so _text() works.
    with patch("mcp_server.pl.list_artifacts",
               return_value=[{"name": "shortlist", "exists": True}]):
        result = _run(mcp_server.call_tool("bug_hunter.list_artifacts", {}))
    body = json.loads(result[0].text)
    assert body["ok"] is True and "artifacts" in body


def test_call_tool_read_artifact_unknown():
    # _err() calls pl.envelope_error — patch only get_artifact, leave helpers real.
    with patch("mcp_server.pl.get_artifact", return_value=None):
        result = _run(mcp_server.call_tool("bug_hunter.read_artifact", {"name": "no-such"}))
    body = json.loads(result[0].text)
    assert body["ok"] is False and body["error"]["code"] == "unknown_artifact"


def test_call_tool_write_artifact():
    with patch("mcp_server.pl.write_file",
               return_value={"ok": True, "name": "shortlist"}) as mock_wf:
        result = _run(mcp_server.call_tool(
            "bug_hunter.write_artifact", {"name": "shortlist", "content": "foo/bar.java\n"}))
    body = json.loads(result[0].text)
    assert body["ok"] is True
    mock_wf.assert_called_once_with("shortlist", "foo/bar.java\n")


def test_call_tool_unknown_name():
    result = _run(mcp_server.call_tool("bug_hunter.does_not_exist", {}))
    body = json.loads(result[0].text)
    assert body["ok"] is False and "Unknown tool" in body["error"]["message"]


def test_call_tool_exception_is_caught():
    # _err() uses pl.envelope_error — patch only get_state, leave helpers real.
    with patch("mcp_server.pl.get_state", side_effect=RuntimeError("boom")):
        result = _run(mcp_server.call_tool("bug_hunter.status", {}))
    body = json.loads(result[0].text)
    assert body["ok"] is False and "RuntimeError" in body["error"]["message"]
