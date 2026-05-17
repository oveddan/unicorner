# Adding controls to the webapp — robustness guide

How to add new widgets to the React controller so they land cleanly on TouchDesigner without per-control changes on the TD side. Read this before you grow the controller spec past the POC 0 baseline.

## The mental model in one paragraph

The webapp speaks one message to TD over WebSocket: `{type:'set', path, value}`. TD's [`ws_callbacks.py`](../poc/0-ws-roundtrip/ws_callbacks.py) splits `path` at the last `/` into an operator path and a parameter name, then assigns `op(node_path).par[param_name] = value`. **That handler is fully generic** — it doesn't care what op or what param. If your control points its `bind.path` at a real op with a real, writable param, it works. No TD-side code change required.

## What this means for adding controls

| Control type | TD changes needed? | Why |
|---|---|---|
| Knob / slider (float, int) | **No** | `par.val = number` works for any numeric Par |
| Toggle (bool) | **No** | `par.val = True/False` works for any Toggle Par |
| Macro (multiple float targets) | **No** | Just emits N `set` messages, each routed independently |
| **Button (pulse)** | **Yes** | Pulse Pars need `par.pulse()`, not `par.val = ...`. The current callback can't trigger pulses. See "Extending the contract" below. |
| Menu / string params | **Yes** | `par.val = "stringval"` works in TD, but the v1 wire contract only allows `number | boolean`. Extend `SetMessage` in [`types.ts`](../controller/src/types.ts) to allow strings. |

So for the **common case** (more knobs, sliders, toggles, macros), adding controls is purely a webapp change.

## The wire contract — don't break this

Every message the controller sends must look exactly like:

```json
{ "type": "set", "path": "/project1/<op>/<param>", "value": 0.42 }
```

- `path` is the **full Par path**, ending in the actual parameter attribute name (e.g. `value0`, `tx`, `rx`, `bypass`). **Not** the channel name shown in the CHOP viewer — that's `name0`, a different field. Reference the Parameter Dialog (`p` key on a selected op) for the attribute name.
- `value` is a JSON number or boolean. Never a string in v1.
- `type` is always `"set"`. Future message kinds (`pulse`, `batch`) get new `type` values.

## Robustness checklist for any new control

Before declaring a new control "done":

1. **The target op exists in TD.** Confirm in TD's textport: `op('/project1/<op>')` returns a node, not `None`.
2. **The param exists on that op.** `getattr(op('/project1/<op>').par, '<param>', None)` is not `None`.
3. **The param accepts the value range you'll send.** Numeric Pars have *two* range fields:
   - `normMin` / `normMax` — UI slider range
   - `min` / `max` — hard validation range, enforced when `clampMin` / `clampMax` are on
   If you send `2.5` to a Par with default `[0,1]` clamping, TD silently snaps to `1.0` and you'll spend an hour debugging. Either widen both ranges, or leave `clampMin`/`clampMax` off. See [CLAUDE.md](../CLAUDE.md#custom-param-writes-silently-snap-to-0-1).
4. **The param isn't being overridden by an Expression / Export / Bind.** A green/blue/purple icon next to the Par field means something else is recomputing it every cook, and your write gets immediately overwritten. Right-click → remove the binding.
5. **The Knob's `useEffect` actually fires.** In [`Knob.tsx`](../controller/src/widgets/Knob.tsx), `send()` only runs when `midiNorm != null`, which only happens after a MIDI Learn mapping exists. If your new widget should react to MIDI, follow the same `useMidi() → normByControl → useEffect → send` pattern.

## How to add a new control end-to-end

Concretely, to add a "scale" knob mapped to a Transform CHOP's `sx` parameter:

1. **Create the target in TD.** Add a Transform CHOP at `/project1/scale_target`. Verify `op('/project1/scale_target').par.sx` exists.
2. **Add the control to the spec.** In the `ControllerSpec` you load in the React app, append:
   ```ts
   {
     id: 'scale',
     type: 'knob',
     label: 'Scale',
     bind: {
       path: '/project1/scale_target/sx',
       param_type: 'float',
       min: 0,
       max: 4,
       curve: 'linear',
     },
   }
   ```
3. **Confirm Par range.** In TD, with the Transform CHOP selected, open the Parameter Dialog. Set `sx`'s `min`/`max` to `[0, 4]` or disable clamping. Otherwise values > 1 will silently snap.
4. **Run it.** Twist the physical knob (or drag the UI slider). Watch the textport for `poc0: WRITE /project1/scale_target/sx = <value>`. If you see no log, follow the diagnostic ladder in CLAUDE.md.

That's it. No edit to `ws_callbacks.py`.

## Adding a brand-new widget type (e.g. an XY pad)

If you want a control that doesn't fit any existing widget:

1. **Define the binding shape** in [`types.ts`](../controller/src/types.ts). For XY: probably a `MacroBinding[]` of length 2 (x → one Par, y → another).
2. **Write the React widget** in `controller/src/widgets/`. Follow [`Knob.tsx`](../controller/src/widgets/Knob.tsx) as the template:
   - Subscribe to `useMidi()` for MIDI input
   - Use `useSend()` to emit `SetMessage`s
   - Each axis becomes one `set` message
3. **Register it** in whatever switch statement renders controls by `type`.
4. **No TD-side changes** as long as the underlying Pars are numeric/bool.

## Extending the contract (only when you must)

If you need pulses, batch updates, or string params, extend both ends *together*:

| Need | Webapp change | TD change |
|---|---|---|
| Pulse buttons | Add `{type:'pulse', path}` to `SetMessage` (or split into a separate type) | In `onWebSocketReceiveText`, branch on `msg.type == 'pulse'` → resolve the Par → call `par.pulse()` |
| Batch (atomic multi-write) | Add `{type:'batch', writes:[{path,value}, ...]}` | Branch on `msg.type == 'batch'` → loop `_set_param` |
| String / menu params | Allow `value: string` in `SetMessage` | No change — `par.val = "stringval"` already works |

Pulse is the only one likely to come up early. Keep `{type:'set'}` as the only message kind until you actually need more.

## Common gotchas (cross-reference)

These have all bitten us at least once — full context in [CLAUDE.md](../CLAUDE.md):

- **Param silently snaps to [0,1]** — the clamp range trap. Affects almost every numeric Par TD ships with.
- **`par.val = value` succeeds but nothing visibly changes** — usually means you're looking at a different op than the one being written to, or the Par has an Expression/Export overriding it every cook.
- **Webapp sends frames but TD textport is silent** — the callbacks DAT contents don't match the file. Re-paste, verify with `op('/project1/poc0_ws_callbacks').text.count('onWebSocketReceiveText')`.
- **No MIDI Learn mapping = no `send()` call** — the input plumbing works fine, but `Knob.tsx` only sends when a mapping exists. Always learn before debugging.

## What's intentionally out of scope (v1)

- **Read-back from TD.** v1 is write-only. The webapp never knows whether a write landed or what the current TD value is. PLAN.md flags bidirectional sync as a later concern.
- **Auth / allowlist.** Anything that can reach `127.0.0.1:9980` can poke any param on any op. Fine on a single-machine hackathon LAN.
- **Schema validation on the TD side.** The handler trusts the message. Don't expose this beyond your dev machine.
