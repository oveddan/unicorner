# `ai/` — designing the controller-from-catalog brain

This folder is for **designers** (no coding required beyond editing markdown and running two Python scripts) who want to shape how the AI controller generator and DJ-routing system behave. You don't need TouchDesigner running to iterate.

## What lives here

| Path | What it is |
|---|---|
| [`prompts/controller-from-catalog.md`](prompts/controller-from-catalog.md) | **The system prompt.** Source of truth for what the model sees. Edit this. |
| [`fixtures/scene-catalog.json`](fixtures/scene-catalog.json) | A synthetic TD scene catalog the prompt tester runs against. |
| [`fixtures/dj-catalog.json`](fixtures/dj-catalog.json) | A synthetic DJay Pro CHOP catalog (BPM, beat, bar, stem amplitudes). |
| [`test_prompt.py`](test_prompt.py) | CLI that calls Claude with the live prompt + fixtures. Iterate without TD. |
| [`sync_prompt.py`](sync_prompt.py) | Copies the .md prompt into `td/modules/unicorner_generator.py` SYSTEM_PROMPT. Required before changes ship to TD. |

## One-time setup

1. Clone the repo, open it in Claude Code (or any editor):
   ```sh
   git clone git@github.com:oveddan/unicorner.git
   cd unicorner
   ```
2. Export your Anthropic API key:
   ```sh
   export ANTHROPIC_API_KEY=sk-ant-…
   ```
3. Make sure Python 3.9+ is available (`python3 --version`). No `pip install` needed — the tester is stdlib-only.

## Designer loop

```sh
# 1. Edit the prompt
$EDITOR ai/prompts/controller-from-catalog.md

# 2. See what the model produces (uses fixtures by default)
python ai/test_prompt.py "make this scene react to music with a bass blend knob"

# 3. Read the response. Tweak the .md. Repeat.

# 4. When happy, mirror the .md into the .py constant TD ships with
python ai/sync_prompt.py

# 5. Commit both files
git add ai/prompts/controller-from-catalog.md td/modules/unicorner_generator.py
git commit -m "tune routing heuristics: prefer 4-bar phrases on lfo_sync"

# 6. (Optional, only matters for TD-side users) — reload the COMP:
#    open td/main.toe, re-run poc/7-drop-in/scaffold.py via the MCP tool,
#    commit the regenerated td/unicorner_controller.tox.
```

`python ai/sync_prompt.py --check` reports drift without writing — wire this into CI to enforce that no PR ships an unsynced .py constant.

## Testing variations

```sh
# Test the controls-only path (no DJay catalog, like before Djaypath is set)
python ai/test_prompt.py --no-dj "give me intensity, hue, and a Push macro"

# Use your own catalogs (export them from TD via poc/2-catalog/extract.py)
python ai/test_prompt.py --catalog my-scene.json --dj-catalog my-dj.json "..."

# Inspect the assembled user turn without calling the API (no key required)
python ai/test_prompt.py --show-prompt "your prompt"

# Test what TD actually has shipped (the .py SYSTEM_PROMPT) vs. your edits
python ai/test_prompt.py --use-py-prompt "your prompt"

# Simulate the iPad's "scan scene" summary being passed along
python ai/test_prompt.py --scene-summary "Slow cinematic intro, kick-heavy track" "make it react to music"
```

---

## How connections (`routings`) work

The generator emits an optional `routings` array alongside the regular `controls`. Each routing **wires a DJay Pro signal directly into a scene parameter**, so the scene reacts to music autonomously while the DJ still controls a few knobs by hand.

The plumbing on the TD side: routings create expressions, LFO CHOPs, and CHOP Execute DATs under the scene root, all tagged `unicorner.routing` so the next regenerate cleans them up.

### When are routings available?

Only when the COMP's `Djaypath` parameter (Unicorner page) points at a DJay Pro CHOP or COMP that exposes channels. If `Djaypath` is empty, the model is told there's no DJay catalog and is forbidden from emitting routings — backwards-compatible with the controls-only flow.

### The three connection types

#### 1. `direct` — DJay channel → scene param

Reads a CHOP channel value (typically 0–1) every frame and remaps it into a scene parameter's range. Optionally scaled by a "blend" knob the DJ can move.

```json
{
  "id": "bass_to_emit",
  "type": "direct",
  "label": "Bass → Emit",
  "djay_channel": "bass",
  "target_path": "/project1/scene/phong1",
  "target_param": "emitb",
  "min": 0.0, "max": 1.0, "curve": "exp",
  "blend_control_id": "bass_blend"
}
```

Result in TD: `phong1.emitb.expr = tdu.remap(op('/project1/djayPro/out1')['bass'] * op('.../surface').par.Bassblend, 0, 1, 0.0, 1.0)`. Use for amplitude-like channels driving visible scalars (brightness, emit, scale, opacity).

#### 2. `lfo_sync` — BPM-synced LFO drives one or more params

Creates a new LFO CHOP under the scene, sets its `rate` from `bpm_channel / 60 / beats_per_cycle`, and expression-binds each target param to the LFO output. Optionally scaled by a rate-multiplier knob (×0.25–×4 for half/double-time).

```json
{
  "id": "beat_lfo_scale",
  "type": "lfo_sync",
  "label": "BPM → Scale",
  "lfo_name": "ai_lfo_beat_scale",
  "bpm_channel": "bpm",
  "beats_per_cycle": 1,
  "rate_multiplier_control_id": "lfo_mult",
  "targets": [
    {"path": "/project1/scene/geo1", "param": "uniformscale", "min": 0.8, "max": 1.2}
  ]
}
```

`beats_per_cycle: 1` = one LFO cycle per beat. `4` = one per bar. Use for rhythmic motion that should follow tempo (pulsing scale, slow camera sway, color wash).

#### 3. `bar_reset` — phase-reset an LFO on every bar

Creates a CHOP Execute DAT watching the `bar` (or `beat`) channel. On the rising edge, it pulses the target LFO's `initialize` param, snapping its phase back to zero. Useful when you want the LFO peak to land exactly on the downbeat.

```json
{
  "id": "bar_reset",
  "type": "bar_reset",
  "label": "Bar → reset LFO",
  "djay_channel": "bar",
  "target_lfo_path": "/project1/scene/ai_lfo_beat_scale"
}
```

`target_lfo_path` typically points at an `lfo_sync` LFO created in the same spec. The path is `<scene_root>/<lfo_name>`.

#### 4. `triggered_speed` — per-beat advancing value

Scaffolds a CHOP chain so a param *advances by some amount on each trigger pulse* (not a smooth sine like `lfo_sync`, not a continuous map like `direct` — but a knob-controllable per-beat step). Use for phase cycling a palette, ticking a counter, building tension over time.

```
djay[djay_channel] → Select → Envelope (exp, attack/decay) → Speed → bind target
```

```json
{
  "id": "beat_palette_advance",
  "type": "triggered_speed",
  "label": "Beat → Palette",
  "djay_channel": "pulse",
  "envelope_decay": 0.5,
  "envelope_attack": 0.0,
  "rate_multiplier_control_id": "step_speed",
  "wrap": true,
  "targets": [
    {"path": "/project1/scene/ramp3", "param": "phase", "min": 0.0, "max": 1.0}
  ]
}
```

Result in TD: three child ops under the scene (`ai_sel_<id>`, `ai_env_<id>`, `ai_spd_<id>`) wired together, with the target param expression-bound to `(speed[0] * step_speed) % 1 * (max-min) + min`. The DJ's `step_speed` knob scales how far each beat advances. `wrap: true` (default) is right for phase-like targets that should loop; `wrap: false` lets the value climb unbounded (useful for an iteration counter).

### Connection rules (validated server-side)

The TD generator drops any routing that violates these. Designers can't accidentally ship broken connections.

- `djay_channel` / `bpm_channel` must match a channel name verbatim in the provided `dj_catalog.channels`.
- `target_path` / `targets[].path` must match a module path in the scene catalog.
- `*_control_id` must reference the `id` of a control in the same spec's `controls`.
- `lfo_name` is a child name (no slashes) — created under the scene root.
- `triggered_speed` should only use trigger-semantic channels (`pulse`, `kick`, `snare`). Using a continuous or counter channel would push the integrator unbounded.

### How to know what channels DJay Pro exposes

The `dj_catalog` block is built by `extract_djay_catalog()` in [`td/modules/unicorner_generator.py`](../td/modules/unicorner_generator.py). It either reads a CHOP's named channels directly or, given a COMP, looks inside for the first `null` / `out` CHOP. Channel names are mapped to a `semantic` hint (`bpm`, `trigger`, `amplitude`, `ramp`) by substring matching in `DJAY_SEMANTIC_HINTS`.

To see what your DJay setup actually exposes, set the COMP's `Djaypath` and look at the next chat turn — the channels are in the `dj_catalog` block of the user turn. Or run the catalog extractor by hand:

```sh
# from inside TouchDesigner, via the MCP execute_python_script tool:
import unicorner_generator
print(unicorner_generator.extract_djay_catalog('/project1/djayPro'))
```

If you want to tune `DJAY_SEMANTIC_HINTS` so the model gets better semantic tags for your specific DJay channel naming, edit the constant in [`td/modules/unicorner_generator.py`](../td/modules/unicorner_generator.py) and re-scaffold the COMP. (Hints are advisory — the model can still wire any channel; semantics just help it pick well.)

---

## When the prompt changes don't seem to take effect

In the designer loop, the .md is the source of truth and `test_prompt.py --use-py-prompt` shows what TD currently ships. If they disagree, run `python ai/sync_prompt.py`. Forgetting this is the #1 way "I changed the prompt but the iPad still does the old thing" happens.

If you've synced and TD still shows the old behavior:
- TD caches imported modules → restart TouchDesigner (just reloading the .toe isn't always enough; see CLAUDE.md).
- The drop-in COMP also bundles the generator code in a Text DAT — re-run `poc/7-drop-in/scaffold.py` via MCP and commit the regenerated `td/unicorner_controller.tox`.
