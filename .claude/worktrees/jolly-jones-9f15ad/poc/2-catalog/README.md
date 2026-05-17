# POC 2 — Parameter catalog extraction via MCP

Walks the TD project and emits a JSON dict describing every "Layer B" module's parameters in a shape the AI generator (POC 3) can consume.

The output is the **Layer B → Layer C contract** — the same shape Calin's real Layer B modules will eventually produce.

## Two scripts

| File | What it does |
|---|---|
| [`scaffold-module.py`](./scaffold-module.py) | Builds a sample Layer B module (`/project1/module_a`) with 5 typed custom params on a `Controls` page, tagged `unicorner.layer-b`. Idempotent. |
| [`extract.py`](./extract.py) | Reads every COMP tagged `unicorner.layer-b` under `/project1`, normalizes its custom params, and emits a dict matching [`schemas/parameter-catalog.schema.json`](../../schemas/parameter-catalog.schema.json). |

## Run it

Both scripts are template strings designed to be passed to `mcp__touchdesigner-stdio__execute_python_script`. In a Claude Code session:

> Run POC 2: scaffold the sample module from `poc/2-catalog/scaffold-module.py` into TD, then extract the catalog with `poc/2-catalog/extract.py` and save the result to `poc/2-catalog/catalog.example.json`.

Or do it by hand with the MCP tool, pasting the `SCAFFOLD_BODY` and `EXTRACTOR_BODY` constants in turn.

## Pass criteria

1. After running the scaffold, `mcp__touchdesigner-stdio__get_td_node_parameters /project1/module_a` shows the 5 custom params on a `Controls` page.
2. The extractor returns a dict that validates against `schemas/parameter-catalog.schema.json`:
   ```bash
   npx --yes -p ajv-cli@5 ajv validate \
     -s schemas/parameter-catalog.schema.json \
     -d poc/2-catalog/catalog.example.json \
     --spec=draft2020 --strict=false
   ```
   Should print `poc/2-catalog/catalog.example.json valid`.
3. Re-running both scripts produces a byte-identical `catalog.example.json` (modulo whitespace).

## What "Layer B-shaped" means

A real Layer B module (Calin's eventual deliverable) is a `containerCOMP` that:

- **Subscribes** to Algoriddim signal CHOPs (kick, bass level, BPM, …) — not covered here, POC scope is Layer C.
- **Exposes** a handful of named, typed, ranged custom parameters on a public page — this is what the extractor reads.
- **Renders** something (a TOP output) — POC scope skips this; our `module_a` is empty inside.
- **Is tagged** `unicorner.layer-b` so the extractor can find it without hard-coded paths.

The prototype catalog schema is intentionally tiny:

| Field | Meaning |
|---|---|
| `name` | TD param attribute name. Used in the WebSocket-message `path`. |
| `path` | Absolute TD path the WebSocket DAT callback writes to. |
| `label` | Human-readable name for the UI. |
| `type` | One of `float`, `int`, `bool`, `pulse`. |
| `min` / `max` | Soft range, for slider/knob widget bounds. |
| `default` | Initial value. |
| `semantic` | Free-text tag for the AI to reason about the param's musical/visual role. |

## What POC 3 will do with this

Feed the catalog as JSON into a Claude prompt along with a target widget vocabulary (knob, button, toggle). Claude returns a `ControllerSpec` — an opinionated layout that groups params into controls, applies curves, and optionally bundles multiple params into a single macro. POC 3's spec must reference only paths present in the catalog the AI was given.
