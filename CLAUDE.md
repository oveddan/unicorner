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

Two parallel flows now exist:

**Build-time (Claude Code + MCP, port 9981).** Open `td/main.toe`. Run POC scaffolds via the `mcp__touchdesigner-stdio__execute_python_script` tool — feed it the body of e.g. [poc/1-mcp-scaffold/scaffold.py](poc/1-mcp-scaffold/scaffold.py) or [poc/7-drop-in/scaffold.py](poc/7-drop-in/scaffold.py). Powered by the `mcp_webserver_base` COMP.

**Runtime (drop-in `.tox`, port 9980).** The `unicorner_controller` COMP hosts the iPad WebSocket and runs the in-TD generator. It calls the Anthropic Messages API directly via stdlib `urllib` — no Claude Code, no Node MCP server, no `pip install` needed inside TD. The DJ / scene-author iterates on the controller from the iPad's ⚙ designer drawer (chat-style prompt + per-scene history). See [poc/7-drop-in/README.md](poc/7-drop-in/README.md) for the drop-in flow.

**Prerequisite for the runtime flow** — an Anthropic API key. The default place to put it is **the `Apikey` parameter on the `unicorner_controller` COMP** (Setup page). After dragging the .tox onto a project, that field is the only thing you must set before the iPad can generate a controller.

Heads-up: TD String params save into the `.toe` as plaintext. For projects you commit or share, leave `Apikey` blank and use one of the escape hatches instead:

- **`ANTHROPIC_API_KEY` env var** in the shell that launches TouchDesigner.
  - macOS: if TD was launched from Finder, shell init isn't inherited — relaunch via `open -a TouchDesigner` from a terminal that has the env, or use `launchctl setenv ANTHROPIC_API_KEY …`.
  - Windows: persist with `setx ANTHROPIC_API_KEY "sk-ant-…"` (new shells / new TD launches inherit it), or set per-session with `$env:ANTHROPIC_API_KEY = "sk-ant-…"` in the PowerShell that launches TD.
- **`td/.unicorner_config.json`** (gitignored). Copy [td/.unicorner_config.example.json](td/.unicorner_config.example.json) → `td/.unicorner_config.json` and fill in the key. Survives Finder launches.

Resolution order: COMP param → env var → config file. First non-empty value wins.

The browser-side POCs (`poc/N-*/poc*.html`) are static files openable in Chrome or Safari, including from an iPad on the same network. The full React renderer lives in [controller/](controller/) — `npm run dev` from there for HMR, or `npm run build` and let the COMP serve `controller/dist/` directly on port 9980 (the COMP's `Distpath` param defaults to `./unicorner_controller_dist` next to the .toe; the released zip uses that same layout).

**Releases.** Every push to `main` triggers [.github/workflows/release.yml](.github/workflows/release.yml), which builds the controller and attaches a zip (`unicorner_controller.tox` + `unicorner_controller_dist/`) plus the bare `.tox` to a fresh GitHub Release. CI can't run TouchDesigner, so the committed `td/unicorner_controller.tox` is the source of truth: **after any change to the COMP's structure or its embedded Python (scaffold.py, the modules under `td/modules/`), open `td/main.toe`, re-run `poc/7-drop-in/scaffold.py` via MCP, and commit the regenerated `td/unicorner_controller.tox`** — otherwise the next release ships a stale COMP.

**PR reviews.** Opening a PR triggers [.github/workflows/claude-review.yml](.github/workflows/claude-review.yml), which runs the official `code-review` plugin and posts severity-tagged inline comments. Fires on `opened` only — pushes to an open PR do not re-review.

## Designer flow — tuning the AI without TouchDesigner

The system prompt that drives the generator + the routing rules ("connections from DJay Pro to scene params") are tunable from Claude Code by a designer who doesn't need TD running. The canonical source is [ai/prompts/controller-from-catalog.md](ai/prompts/controller-from-catalog.md); a thin Python tester ([ai/test_prompt.py](ai/test_prompt.py)) hits Anthropic against fixture catalogs in [ai/fixtures/](ai/fixtures/) so you can iterate, and [ai/sync_prompt.py](ai/sync_prompt.py) mirrors the prompt into `td/modules/unicorner_generator.py` SYSTEM_PROMPT (the TD-side copy that ships in the .tox).

Workflow: edit the .md → `python ai/test_prompt.py "your prompt"` → iterate → `python ai/sync_prompt.py` → commit both files. CI/pre-commit should run `python ai/sync_prompt.py --check` to enforce no drift. See [ai/README.md](ai/README.md) for the full guide, including the three routing types (`direct`, `lfo_sync`, `bar_reset`) and how connections are validated.

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

## Reusable scripts: build, improve, document

**The pattern: don't inline-exec the same TD operation twice.** The first time we do a non-trivial operation through `mcp__touchdesigner-stdio__execute_python_script`, save it as a parameterized script under `poc/N-*/` and feed *its body* to MCP next time. Inline exec is fine for one-off introspection; the moment we'd consider re-running an operation, it belongs in a script.

The convention (see [poc/1-mcp-scaffold/scaffold.py](poc/1-mcp-scaffold/scaffold.py), [poc/2-catalog/extract.py](poc/2-catalog/extract.py), [poc/6-controller-surface/scaffold.py](poc/6-controller-surface/scaffold.py)):

```python
"""
What this does, when to run it, what it depends on.
"""

# ----- Config (edit per scene) -----
PARAMS   = [...]
BINDINGS = [...]

# ----- Body fed to TD via MCP -----
BODY = r'''
... TD-side Python that consumes the config above ...
'''

if __name__ == '__main__':
    print(BODY)  # render for piping / inspection
```

**When we learn something doing this work** — a new gotcha, a new pattern, an undocumented constraint — improve in two places:

1. The script's comments and defaults, so the next person running it sees the lesson in context.
2. The "TD gotchas learned the hard way" section above, so cross-script lessons accumulate centrally.

Scripts compound. A one-off inline exec is throwaway; a saved script is an asset that gets sharper each time we run it. Bias toward saving and improving rather than redoing.

## Repository layout

| Path | Purpose |
|---|---|
| `PLAN.md` | Full hackathon project plan |
| `README.md` | Pitch / outward-facing project description |
| `td/` | TouchDesigner project (`main.toe` lives here, gitignored is its auto-backups) |
| `vendor/` | Gitignored. Populated by `scripts/setup-td-mcp.sh`. |
| `scripts/` | Setup + helper scripts |
| `poc/` | Numbered proof-of-concepts (riskiest tech first) and reusable scripts (e.g. [poc/6-controller-surface/](poc/6-controller-surface/)). See sub-plan. |
| `docs/` | Project docs, including [touchdesigner-mcp-setup.md](docs/touchdesigner-mcp-setup.md) (deep-dive for the dev workflow) |

## Pointers for Claude

- The Layer C sub-plan is the source of truth for what we're building: `~/.claude/plans/ok-this-is-dan-lovely-turtle.md`.
- POC discipline: don't combine POCs. Each one should be a binary yes/no on one piece of tech. If a POC needs to be expanded, that's a new POC.
- Architecture decisions made and *not* to relitigate without cause: external React controller, TD WebSocket DAT as the runtime transport, MCP only for build-time scaffolding, write-only v1. See the plan file for context.
