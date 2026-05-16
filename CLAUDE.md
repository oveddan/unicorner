# Unicorner — Claude Code working notes

Hackathon project building an AI-generated visual controller. See [README.md](README.md) for the pitch and [PLAN.md](PLAN.md) for the full architecture. The Layer C prototype sub-plan is at `~/.claude/plans/ok-this-is-dan-lovely-turtle.md`.

This file captures setup and troubleshooting context that's useful across sessions.

## First-time setup (per machine)

Three pieces have to be in place: the TouchDesigner side, the Claude Code MCP wiring, and a `.toe` to work in.

1. **Fetch the TouchDesigner MCP server artifact.** From the repo root:
   ```bash
   ./scripts/setup-td-mcp.sh
   ```
   Downloads the latest release into `vendor/touchdesigner-mcp-td/` (gitignored). Optionally pin to a tagged release: `TD_MCP_RELEASE=v1.4.7 ./scripts/setup-td-mcp.sh`.

2. **Wire Claude Code to the MCP server** (one-time, per machine):
   ```bash
   claude mcp add -s user touchdesigner -- npx -y touchdesigner-mcp-server@latest --stdio
   ```
   Verify:
   ```bash
   claude mcp list | grep touchdesigner
   ```
   Should show `✓ Connected`. The Node side will report connected even when TD isn't running — the connection it actually needs is to TD's web server, which only happens when you call a tool.

3. **Create + configure the TD project:**
   - Open TouchDesigner. Save a new project as `td/main.toe`.
   - Drag `vendor/touchdesigner-mcp-td/mcp_webserver_base.tox` onto `/project1`.
   - Open the textport (`Dialogs → Textport and DATs`). Look for `HTTP server started` with no `[ERROR]` lines.
   - Save the .toe (`Cmd+S`).

4. **Verify end-to-end** from Claude Code:
   ```
   call mcp__touchdesigner-stdio__get_td_info
   ```
   Should return server info (TD version, API version), not a connection error.

## Day-to-day

- Open `td/main.toe` to start working. The MCP bridge starts automatically when the `mcp_webserver_base` COMP loads.
- Run the POC scaffolds via the `mcp__touchdesigner-stdio__execute_python_script` tool, feeding it the contents of e.g. `poc/1-mcp-scaffold/scaffold.py`.
- The browser-side POCs (`poc/N-*/poc*.html`) are static files openable in Chrome or Safari, including from an iPad on the same network.

## Troubleshooting

### `🔌 TouchDesigner Connection Failed` / `ECONNREFUSED 127.0.0.1:9981`

TD's MCP bridge isn't reachable. In order of likelihood:
1. **TD isn't open** with a project that has `mcp_webserver_base` loaded → open `td/main.toe`.
2. **The Web Server DAT inside the COMP isn't Active** → open `/project1/mcp_webserver_base`, check the internal Web Server DAT has `Active = On`.
3. **The COMP loaded but `setup()` failed silently** → see the next section.

### `ModuleNotFoundError: No module named 'mcp'` in TD's textport

The MCP COMP's `import_modules.setup()` raised, was swallowed by the webserver script's `try/except`, and `sys.path` never got the `vendor/touchdesigner-mcp-td/modules/` directory.

In `mcp_webserver_script.py`:
```python
try:
    import import_modules
    import_modules.setup()
except Exception as e:
    print(f"[ERROR] Failed to setup modules: {str(e)}")
```

Open the textport and look **above** the traceback for the `[ERROR] Failed to setup modules:` line. That tells you the real cause. Common ones:

| Cause | Fix |
|---|---|
| Stale Python import state cached from a prior failed load | Full TD restart (not just new project). Python cache clears. |
| `vendor/` directory moved or renamed | Don't move files within `vendor/touchdesigner-mcp-td/`. `import_modules.py`, `mcp_webserver_base.tox`, and `modules/` must stay siblings. Re-run `./scripts/setup-td-mcp.sh` to restore. |
| Dev clone of `touchdesigner-mcp` used instead of release zip | Use the vendored release artifact. The dev clone can have partial build state that the release artifact doesn't. |

**TL;DR fix:** quit TouchDesigner entirely → re-run `./scripts/setup-td-mcp.sh` → open `td/main.toe`. If that doesn't clear it, paste the textport `[ERROR]` line.

### `claude mcp list` shows `✓ Connected` but every tool call fails

The Node-side MCP server is healthy; the Python-side server inside TD isn't. Same fix as above — open TD, ensure the COMP is loaded, check the textport.

### Editing the upstream MCP server code

You shouldn't need to for v1. If you do (working on the MCP server itself, not Unicorner):
- TypeScript changes (`src/`): rerun `npm run build` in the touchdesigner-mcp repo, reconnect MCP client (`/mcp` in Claude Code).
- Python changes (`td/modules/`): full TD restart. TD caches imported modules; `td/import_modules.py` has a softer reload but a restart is the simplest reliable clear.

## Repository layout

| Path | Purpose |
|---|---|
| `PLAN.md` | Full hackathon project plan |
| `README.md` | Pitch / outward-facing project description |
| `td/` | TouchDesigner project (`main.toe` lives here, gitignored is its auto-backups) |
| `vendor/` | Gitignored. Populated by `scripts/setup-td-mcp.sh`. |
| `scripts/` | Setup + helper scripts |
| `poc/` | Numbered proof-of-concepts, riskiest tech first. See sub-plan. |
| `docs/` | Project docs, including [touchdesigner-mcp-setup.md](docs/touchdesigner-mcp-setup.md) (deep-dive for the dev workflow) |

## Pointers for Claude

- The Layer C sub-plan is the source of truth for what we're building: `~/.claude/plans/ok-this-is-dan-lovely-turtle.md`.
- POC discipline: don't combine POCs. Each one should be a binary yes/no on one piece of tech. If a POC needs to be expanded, that's a new POC.
- Architecture decisions made and *not* to relitigate without cause: external React controller, TD WebSocket DAT as the runtime transport, MCP only for build-time scaffolding, write-only v1. See the plan file for context.
