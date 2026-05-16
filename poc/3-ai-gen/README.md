# POC 3 — AI generator (catalog → ControllerSpec)

Proves Claude can turn a parameter catalog into a schema-valid, path-resolving `ControllerSpec`. Three artifacts retire the risk:

| File | Role |
|---|---|
| [`../../schemas/controller-spec.schema.json`](../../schemas/controller-spec.schema.json) | The AI's output contract. Captures knob/slider/toggle/button + macro, with referential-integrity rules. |
| [`../../ai/prompts/controller-from-catalog.md`](../../ai/prompts/controller-from-catalog.md) | System prompt + one few-shot. Hard rules (≤12 controls, path-must-exist-in-catalog, JSON-only output), design heuristics, examples. |
| [`spec.example.json`](./spec.example.json) | A worked output for the current `scene_id="a"` catalog. Schema-valid, all 8 bindings resolve. |

## Two-stage validation

The spec is checked twice:

1. **Schema validity** — structural shape only. Run `ajv`:
   ```bash
   npx --yes -p ajv-cli@5 ajv validate \
     -s schemas/controller-spec.schema.json \
     -d poc/3-ai-gen/spec.example.json \
     --spec=draft2020 --strict=false
   ```
2. **Referential integrity** — every `path` in the spec must appear in the catalog the AI was given. Schema can't enforce this. Run:
   ```bash
   node poc/3-ai-gen/cross-check-paths.mjs \
     poc/2-catalog/catalog.example.json \
     poc/3-ai-gen/spec.example.json
   ```

Both must pass before a spec hits the renderer. The production generator (deferred) will run both as a gate and retry the LLM call up to 2× if either fails.

## What's intentionally deferred

The production generator — a Node CLI that actually calls the Claude API with `prompts/controller-from-catalog.md` substituted with the live catalog. It's a thin shell once you have:

- The prompt (locked)
- The schema (locked)
- The validators (above)
- An `ANTHROPIC_API_KEY`

About 50 lines of Node. We'll write it before the demo, or trigger it from inside TD on scene swap. Either way, the interesting engineering risk is the prompt+schema — that's what POC 3 retires.

## Pass criteria

✅ `spec.example.json` validates against `controller-spec.schema.json`.
✅ Every binding path in `spec.example.json` matches a parameter path in `catalog.example.json` (8/8).
✅ Spec exercises every widget type the renderer will need (knob, toggle, macro). Slider and button are in the schema for POC 4 to render even though this example doesn't use them.
