# TouchDesigner MCP — Local Setup

This doc captures the steps to run the [`8beeeaaat/touchdesigner-mcp`](https://github.com/8beeeaaat/touchdesigner-mcp) server locally and wire it into Claude Code / Claude Desktop. Relevant for Unicorner v4 (the MCP-driven agent that reconfigures TouchDesigner itself), and useful at the v1 stage if any of us want Claude to poke at a TD project interactively.

Upstream reference: <https://github.com/8beeeaaat/touchdesigner-mcp/blob/main/docs/development.md>

## What this gives you

The MCP server exposes 13 tools that let an LLM read and mutate a running TouchDesigner project. Highlights:

| Tool | What it does |
|---|---|
| `create_td_node` | Create any operator type (TOPs/CHOPs/SOPs/DATs/MATs and **COMPs** — Container, Base, Geometry…) under a given parent path |
| `delete_td_node` | Delete a node |
| `update_td_node_parameters` | Set parameter values on a node |
| `get_td_nodes` / `get_td_node_parameters` / `get_td_node_errors` | Inspect the project tree, parameters, errors |
| `exec_node_method` | Call a method on a node (e.g. `.create(...)` on a COMP) |
| `execute_python_script` | Run arbitrary Python inside TD's WebServer DAT — use this for anything multi-step (create + parent + wire + parameterize in one call) |
| `get_td_classes` / `get_td_class_details` / `get_td_module_help` | Introspect available operator types and Python API |
| `get_td_info` / `describe_td_tools` | Server / tool metadata |

The `nodeType` for `create_td_node` is whatever TD expects (e.g. `containerCOMP`, `baseCOMP`, `geo`, `textTOP`). Use `get_td_classes` if you're unsure.

## Two runtime pieces

1. **Node.js MCP server** — `dist/cli.js`, spawned by the MCP client (Claude Code / Desktop) over stdio. Source under `src/`.
2. **Python code inside TouchDesigner** — `td/modules/` loaded by TD's WebServer DAT. The Node server talks to this Python via HTTP on `127.0.0.1:9981` by default.

So a working setup needs both: the Node server bin built locally, **and** TouchDesigner running with the project's WebServer DAT loaded (the upstream repo ships `td/mcp_webserver_base.tox` for that).

## Prerequisites

- macOS with Homebrew
- Node 24.x (the repo pins `24.14.1` in `.node-version`)
- **Java** — needed only at build time (the build step generates a Python Flask skeleton from `src/api/index.yml` via `openapi-generator-cli`, which runs a JAR). Docker is the upstream-recommended alternative; we used openjdk because it's lighter on macOS.
- TouchDesigner installed (for actually running the server-side Python)

## One-time install

```bash
# 1. openjdk for the build (keg-only — not symlinked onto PATH)
brew install openjdk

# 2. clone + install
cd ~/Source     # or wherever
git clone https://github.com/8beeeaaat/touchdesigner-mcp.git
cd touchdesigner-mcp
npm install

# 3. build (gen:webserver needs Java on PATH; openjdk is keg-only so prepend it)
export PATH="/opt/homebrew/opt/openjdk/bin:$PATH"
npm run build
```

`npm run build` runs three codegen steps before `tsc`:
- `gen:webserver` → Python Flask skeleton in `td/modules/td_server/` (the part that runs inside TD)
- `gen:handlers` → `td/modules/mcp/controllers/generated_handlers.py`
- `gen:mcp` → TS client + Zod schemas in `src/gen/` and `src/tdClient/`

If you'll rebuild often, add `export PATH="/opt/homebrew/opt/openjdk/bin:$PATH"` to `~/.zshrc` per brew's caveat.

## Wire it into Claude Code

```bash
claude mcp add -s user touchdesigner-stdio -- \
  npx -y /ABSOLUTE/PATH/TO/touchdesigner-mcp/dist/cli.js --stdio --port=9981
```

Verify:
```bash
claude mcp list | grep touch
# touchdesigner-stdio: ... - ✓ Connected
```

`--port=9981` is the port the Node server will use to reach the Python side inside TouchDesigner. Match the value the TD WebServer DAT is listening on.

## Wire it into Claude Desktop (optional)

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` and add an `mcpServers` block (merge with existing keys — don't overwrite):

```json
{
  "mcpServers": {
    "touchdesigner-stdio": {
      "command": "npx",
      "args": [
        "-y",
        "/ABSOLUTE/PATH/TO/touchdesigner-mcp/dist/cli.js",
        "--stdio",
        "--port=9981"
      ]
    }
  }
}
```

Restart Claude Desktop to pick it up.

## TouchDesigner side

The Node MCP server is useless on its own — it's an HTTP client to a Python server running **inside** TouchDesigner. To complete the loop:

1. Open the upstream repo's `td/mcp_webserver_base.tox` in a TD project (or import the modules into your own project — see `td/import_modules.py`).
2. Make sure the WebServer DAT is active on the same port you passed to `--port=` (default `9981`).
3. From an MCP-aware client, call `get_td_info` to confirm the round-trip works.

## When you change code, restart both

- **TypeScript change** (anything in `src/`): rerun `npm run build`, then reconnect the MCP client (`/mcp` in Claude Code, or restart Claude Desktop) so it respawns `dist/cli.js`.
- **Python change** (anything in `td/modules/`): restart TouchDesigner. TD caches imported modules; full restart is the simplest reliable way to clear them. `td/import_modules.py` does a softer reload if you need to iterate quickly.
- **API contract change** (`src/api/index.yml`): both sides — codegen regenerates both Python and TS sides.

## Troubleshooting

- **Build fails at `gen:webserver`** → Java isn't on PATH. `which java` should resolve to `/opt/homebrew/opt/openjdk/bin/java` (or similar). If it's `/usr/bin/java` and complains "Unable to locate a Java Runtime", that's the macOS stub — install openjdk and prepend brew's path.
- **`✓ Connected` in `claude mcp list` but tools fail** → the Node side is healthy; the Python side inside TD isn't. Check TD is running, the WebServer DAT is on, and the port matches `--port=`.
- **Stale behavior after editing code** → see "restart both" above. Reconnecting the MCP client without rebuilding still runs the old `dist/cli.js`.

## For Unicorner specifically

- **v1–v3** don't need this — those layers talk to TD via OSC, not via the MCP server.
- **v4** (the agent that reconfigures TD itself) is the natural consumer. The `create_td_node` + `update_td_node_parameters` + `execute_python_script` trio is enough to build new visual modules in Layer B from scratch.
- Useful even pre-v4: ad-hoc Claude Code sessions can introspect a teammate's TD project (`get_td_nodes` on `/project1`) when debugging Layer B parameter catalogs.
