# POC 7 — Drop-in `unicorner_controller.tox`

The single COMP a user drops onto any TouchDesigner project to get an iPad-driven, AI-generated controller surface — no Claude Code, no MCP server, no pip installs required.

## What it bundles

```
/project1/unicorner_controller       (baseCOMP, tagged 'unicorner.controller')
│   custom pars: Target, Scene, Prompt, Generate
├── webserver1      Web Server DAT, port 9980, WebSocket enabled
├── callbacks       Text DAT — mirrors td/modules/controller_webserver_script.py
├── generator       Text DAT — mirrors td/modules/unicorner_generator.py
├── init            Text DAT — wires `unicorner_generator` into the callbacks scope
└── parexec1        Parameter Execute DAT — fires regen on Scene / Generate
```

## One-time scaffold

From the repo root, with `td/main.toe` open and the MCP bridge connected:

```bash
python poc/7-drop-in/scaffold.py | pbcopy
# paste into mcp__touchdesigner-stdio__execute_python_script
```

That builds the COMP at `/project1/unicorner_controller` and writes `td/unicorner_controller.tox` to disk. After that, **dragging the .tox onto any TD project does the same thing** — no Claude Code needed.

## Per-machine prerequisites

- An Anthropic API key, set on the COMP's **`Apikey`** parameter (Setup page) — that's the canonical place. The key saves into the `.toe` as plaintext, so for committed/shared projects leave the param blank and use `ANTHROPIC_API_KEY` env var or `td/.unicorner_config.json` (gitignored) instead. Resolution order: COMP param → env var → config file. See [CLAUDE.md](../../CLAUDE.md) "Day-to-day" for details.
- Spec generation calls the Anthropic Messages API via stdlib `urllib`; no third-party Python deps inside TD.

## Day-to-day flow for non-Claude-Code users

1. Open a TD project, drag `unicorner_controller.tox` onto the root COMP.
2. Set `Target` to the COMP whose subtree contains your Layer B modules (tagged `unicorner.layer-b`).
3. Optionally set `Scene` to a specific child COMP — surfaces live at `<Scene>/controller_surface`.
4. Open the iPad URL — `http://<laptop-ip>:9980` (renderer dev server) or wherever you've deployed the React build.
5. Tap the ⚙ icon, type a prompt, hit Send. Spec generates in ~3–5s; controls render; knob moves drive the underlying scene.

## Verification

See the plan at `~/.claude/plans/how-can-i-make-velvety-key.md` for the full verification checklist.
