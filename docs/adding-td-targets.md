# Adding new TD targets for webapp controls

Every clickable / draggable thing in the controller (slider, knob, button, toggle) writes to **one TouchDesigner operator parameter**. Adding a new control means: (1) create the target op in TD, (2) point the React control at its path, (3) verify the round-trip. This doc is the recipe.

## Mental model in one line

`/project1/<op>/<param>` is the address. The webapp sends `{type:'set', path, value}` to TD, TD's [`ws_callbacks.py`](../poc/0-ws-roundtrip/ws_callbacks.py) splits the path at the last `/`, and assigns `op(node_path).par[param_name] = value`. **The TD callback never needs editing** for new sliders, knobs, toggles, or click-to-toggle buttons — it's generic. You only add new ops.

## Naming convention

Boring beats clever. Pick the first one that fits:

| Pattern | When | Example |
|---|---|---|
| `<widget_id>_target` | One dedicated op per widget | `btn1_target`, `slider3_target` |
| `<role>_signal` | The op represents a semantic signal, not just "the thing the widget writes to" | `gate_a_signal`, `tempo_signal` |
| `poc<N>_target` | Reserved for proof-of-concept work; don't reuse in production scenes | `poc0_target` |

Rules of thumb:
- **One target op per control.** Easier to debug — when the value is wrong you know exactly which widget is responsible. The cost (a few extra Constant CHOPs) is negligible.
- **Lowercase, snake_case, no spaces.** TD allows other characters; using them later breaks downstream operator references.
- **Keep targets in `/project1`** (or a child COMP if you outgrow flat namespacing). Don't litter root.

## Step-by-step: adding a target for a new control

Concrete worked example — adding a toggle button "Button 2" that lights up a TOP.

### 1. Create the target in TD

In `/project1`:

| Widget type | Recommended op | Why |
|---|---|---|
| Slider / knob (float) | **Constant CHOP** | Numeric channel, easy to wire downstream (Math, Switch, Level…) |
| Click-to-toggle button (bool) | **Constant CHOP** | Booleans coerce to `1.0` / `0.0` on `value0`; same as numeric |
| True toggle / on-off Par | Op with a Toggle custom Par | If you need a real Toggle Par (e.g. `bypass` on a Math CHOP), you can target that directly |
| Pulse button (momentary) | **Not yet supported** end-to-end | The v1 wire contract has no `pulse` type. See [adding-controls.md](adding-controls.md) for the extension plan. |

For Button 2:
- Add a **Constant CHOP** to `/project1`.
- Rename to `btn2_target`.
- Confirm it has `value0` (default does).

### 2. Confirm the path resolves

In TD's textport:

```python
op('/project1/btn2_target').par.value0.eval()
```

Should print `0.0`, not throw. If it errors, the name or path is wrong.

### 3. Point the React control at it

Two cases depending on which kind of control:

**Hardcoded UI button** (current Button 1 / 2 / 3 / 4): edit [App.tsx](../controller/src/App.tsx) directly, set the `path` prop:

```tsx
<ToggleButton label="Button 2" path="/project1/btn2_target/value0" />
```

**Spec-driven control** (knobs, sliders, toggles loaded from `public/specs/a.json`): add an entry to the spec:

```json
{
  "id": "btn2",
  "type": "toggle",
  "label": "Button 2",
  "bind": {
    "path": "/project1/btn2_target/value0",
    "param_type": "bool"
  }
}
```

The renderer in [App.tsx](../controller/src/App.tsx) picks the right widget by `type`.

### 4. Verify end-to-end

1. Save (Vite hot-reloads).
2. Interact with the control once.
3. Watch TD textport. You should see (with the debug probes from POC 0 in place):
   ```
   poc0: RX text len=...
   poc0: WRITE /project1/btn2_target/value0 = True
   ```
4. Open `btn2_target` in TD — `value0` should reflect the new state.

If textport is silent, walk the [troubleshooting ladder in CLAUDE.md](../CLAUDE.md).

## Gotchas (cross-reference)

These bite every time:

- **Path uses the channel name, not the Par name.** A Constant CHOP's first channel is named whatever you typed (e.g. `position`), but the writable Par is always `value0`. Path must end in `value0`. If it ends in `position` you'll get `poc0: no param 'position'` in textport.
- **Silent clamp to [0, 1].** Numeric Pars have `normMin/normMax` (UI) and `min/max` (validation). If `clampMin/clampMax` are on and your control sends values outside `[0, 1]`, TD silently snaps. Either widen both ranges or disable clamping. See [CLAUDE.md](../CLAUDE.md#custom-param-writes-silently-snap-to-0-1).
- **Expression / Export / Bind overrides your write.** If the Par field has a green/blue/purple icon, something else is recomputing it every cook. Right-click → remove the binding. Symptom: `WRITE` fires in textport but the value never visibly changes.
- **Wrong CHOP in the viewer.** If you see writes in textport but no visual change, you may be looking at a different op than the one being written to. Use `op('<path>').par.value0.eval()` in textport to read the actual value.
- **Reusing a target op across controls.** Two widgets writing to the same path will fight. Always one widget → one target.

## What's intentionally still manual

- **You create the TD op by hand.** v1 doesn't auto-create targets when you add a widget. The MCP-based scaffolding (POC 1) is a v2/v3 concern. For now, every new widget = a few TD clicks to make the target.
- **You set Par ranges by hand.** If your slider goes 0–4, you have to widen the target Par's `min/max` to `[0, 4]` in TD or disable clamping.

## Quick reference

| You added… | TD side | React side |
|---|---|---|
| A new slider/knob | New Constant CHOP `<id>_target` | Spec entry with `type: "knob"`, `bind.path: ".../value0"` |
| A new toggle button | New Constant CHOP `<id>_target` | Hardcoded `<ToggleButton label="..." path=".../value0" />` or spec `type: "toggle"` |
| A new pulse button | **Not yet** — extend contract first | — |
| A new menu/string control | New op with the string-valued Par | **Not yet** — `SetMessage.value` is number/bool only in v1 |
