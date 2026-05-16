# POC 6 — controller-surface scaffolding

The runtime contract between the iPad React app and TouchDesigner is a single
COMP at `/project1/controller_surface`. Its custom params are the controls
the iPad renders (via the schema push from `poc/0-ws-roundtrip/ws_callbacks.py`),
and each one is expression-bound to a target param somewhere downstream.

These scripts are the day-to-day operations for that contract.

## Files

| File | What it does | When to run |
|---|---|---|
| [scaffold.py](scaffold.py) | (Re)builds `/project1/controller_surface` from a config and binds each surface param to a target par. Idempotent. | Whenever the controller's param set or bindings change. |
| [sync_callbacks.py](sync_callbacks.py) | Reads `poc/0-ws-roundtrip/ws_callbacks.py` from disk and writes it into TD's callbacks Text DAT. | After editing the WS callback file on disk. |
| [inspect.py](inspect.py) | Dumps `controller_surface` state + Layer B contract gaps (declared custom params with no internal consumers — "stub-ware"). | When debugging why a knob doesn't move the visual. |
| [add_layer_b_param.py](add_layer_b_param.py) | Adds a new custom param to a Layer B module *and* expression-binds one of its internal render nodes to read from it. | When you want a new iPad knob to follow the clean Layer B contract. |

## Two ways to wire a new knob

**Clean way (Layer B contract):** the knob drives a Layer B module's own custom param, and the module's internals read from that. Keeps modules self-contained — Calin can move the module to a fresh project and the surface comes with it.

1. Edit `add_layer_b_param.py` with the new param + which internal node should follow it.
2. Run it via MCP `execute_python_script`.
3. Edit `scaffold.py`'s `PARAMS` + `BINDINGS` to expose the new knob on the surface.
4. Run `scaffold.py` via MCP.
5. Reload the iPad — the new widget appears.

**Quick way (direct bind):** the knob's binding target is an internal render node directly. Skips the module's custom-param layer.

1. Edit `scaffold.py`'s `PARAMS` and add a `BINDINGS` entry whose target is the internal node.
2. Run it via MCP.
3. Reload the iPad.

Use the quick way for hackathon iteration; the clean way for anything that's expected to survive.

## A new-scene workflow

```
1.  Open the .toe in TouchDesigner.
2.  Run inspect.py via MCP — gives you the current surface state and any
    declared-but-unused Layer B params in the scene.
3.  Read the scene's modules + internal expressions to identify which params
    actually drive rendering.
4.  Decide which knobs to expose. For each:
      - clean way → add_layer_b_param.py to declare + wire on the Layer B side,
        then add an entry to scaffold.py's BINDINGS pointing at the module's
        custom param.
      - quick way → add to scaffold.py's BINDINGS pointing at the internal
        render node directly.
5.  Run scaffold.py via MCP.
6.  Run sync_callbacks.py if ws_callbacks.py has changed on disk.
7.  Reload the React app on the iPad.
```

## Diagnostic check: "is this param actually driving anything?"

If a knob on the iPad moves but the visual doesn't change, run `inspect.py`. Look for declared custom params whose `is_stub: true` — those are over-promising the Layer B contract. Either wire them up internally, or drop them from the surface.

## Notes on TD parameter naming

TD enforces a specific casing rule for custom param `name`: it must start with one uppercase letter, then lowercase letters or digits, and cannot end in a digit. Multi-word names become one word: `Pulserate`, not `PulseRate`. The `label` is free-form and can be `Pulse Rate` — that's what the iPad shows.

## Related

- [poc/0-ws-roundtrip/ws_callbacks.py](../0-ws-roundtrip/ws_callbacks.py) — the Web Server DAT's Python callbacks. `onWebSocketOpen` reads `controller_surface.customPars` and pushes the schema to the connecting client; `onWebSocketReceiveText` writes the named param.
- [controller/src/App.tsx](../../controller/src/App.tsx) — receives the schema, builds one widget per param, sends `{type:"set", path, value}` on change.
- [CLAUDE.md](../../CLAUDE.md) — TD gotchas, including the silent-clamp trap and `execute_python_script` return-value quirks.
