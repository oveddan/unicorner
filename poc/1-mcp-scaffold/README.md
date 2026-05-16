# POC 1 — MCP scaffolds the POC 0 scene

[`scaffold.py`](./scaffold.py) recreates POC 0's setup programmatically. Idempotent — safe to re-run.

## Prerequisites

1. TouchDesigner is open with a `/project1` root.
2. The upstream `mcp_webserver_base.tox` is loaded so the MCP server is live on `127.0.0.1:9981` (see [docs/touchdesigner-mcp-setup.md](../../docs/touchdesigner-mcp-setup.md)).
3. Verify with `mcp__touchdesigner-stdio__get_td_info` — should return server info, not a connection error.

## Run it

In a Claude Code session connected to this repo:

> Run POC 1: read `poc/1-mcp-scaffold/scaffold.py` and execute it via the TD MCP `execute_python_script` tool.

Claude reads the file and invokes `mcp__touchdesigner-stdio__execute_python_script` with the file's contents. The script's `print()` output (the layout summary) comes back in the tool response.

## Pass criteria

After the script runs:

1. `mcp__touchdesigner-stdio__get_td_nodes` on `/project1` shows three new ops: `poc0_target`, `poc0_ws_callbacks`, `poc0_ws`.
2. Open [`../0-ws-roundtrip/poc0.html`](../0-ws-roundtrip/poc0.html) in a browser, connect to `ws://127.0.0.1:9980`, move the slider — `poc0_target.value0` updates.
3. Re-running the script destroys + recreates cleanly with no `KeyError` from duplicate names.

If all three pass, the scaffolding loop is proven and we can move to POC 2 (catalog extraction).

## What this unlocks

The scaffold is the same skeleton scene-swap will use later: destroy + create + configure a coherent set of ops in one call. POC 7 extends this script to take a `variant` argument that builds different module compositions.

## Keep in sync

`CALLBACK_CODE` in `scaffold.py` mirrors [`../0-ws-roundtrip/ws_callbacks.py`](../0-ws-roundtrip/ws_callbacks.py). The Python file is the human-readable reference for the manual POC 0 path; the embedded string is the MCP-driven source of truth. If you change one, mirror the change to the other.
