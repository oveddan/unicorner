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

## TD gotchas learned the hard way

### Custom-param writes silently snap to [0, 1]

TD numeric Pars have **two** ranges:

| Field | Purpose |
|---|---|
| `normMin` / `normMax` | Slider / UI range (the soft range a knob sweeps over) |
| `min` / `max` | Hard validation range — what `clampMin` / `clampMax` enforce |

If you set only `normMin` / `normMax` and turn `clampMin` / `clampMax = True` on, every write outside the **default** `[0, 1]` silently snaps. Symptom: WS writes appear to succeed, the param "doesn't move." Fix: set both ranges, or leave clamping off. See [`poc/2-catalog/scaffold-module.py`](poc/2-catalog/scaffold-module.py) for the canonical pattern.

### `execute_python_script` sometimes drops the return value

The MCP tool returns the last expression of the script verbatim — *except* when the script is "too long" or contains `def` blocks plus several statements. In those cases the side effects all happen, but the return comes back as `null`. Workarounds:
- Use `detailLevel: "detailed"` always — short scripts then reliably round-trip.
- For longer scripts: don't depend on the return value; follow up with a small read script that just evaluates one expression.

### `project.name` gets stuck after `project.save("/new/path.toe")`

After saving to a new path, TD updates `project.folder` but `project.name` stays on the *next* backup name (e.g. `main.1.toe`) — even when no such file exists. If you then `Cmd+S`, TD writes to that name and the file you meant to update stays stale. Fix: `File → Open` the .toe explicitly to reset `project.name`.

### Backup `.N.toe` files proliferate on every save

TD writes a numbered backup each save by default. Disable: `Edit → Preferences → File → "Number of Backup Files To Keep On Save"` → set to `0`. The repo's `.gitignore` already excludes `*.[0-9].toe` and `*.[0-9][0-9].toe` either way.

### Don't work inside a worktree if the .toe is involved

Stored relative paths inside a .toe resolve against whatever directory the file currently sits in. Switching between a git worktree and the main checkout leaves multiple valid resolutions of the same path and TD gets confused. Use the main checkout for any work that touches `td/main.toe`.

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
