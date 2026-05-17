"""
unicorner_generator — in-TD entry point for the controller generator.

Runs entirely inside TouchDesigner's bundled Python. Replaces the MCP-driven
build-time flow with an in-process pipeline:

    catalog (walk target subtree)
        → Anthropic API (controller-from-catalog prompt)
            → ControllerSpec (validated)
                → controller surface COMP (scaffolded under the scene)

Called by `controller_webserver_script.py` when a `{type:"generate", ...}`
WebSocket message arrives, or when the drop-in COMP's `Generate` pulse fires.

Why this duplicates logic from poc/2-catalog/extract.py and
poc/6-controller-surface/scaffold.py:
- Those POC scripts target the MCP `execute_python_script` tool and ship as
  body strings. To call them in-process we'd `exec()` a string — fragile.
- The pure-Python logic is small enough to inline. When the POC scripts
  change, mirror the change here. CLAUDE.md "compound tooling" notes apply.

Dependencies: stdlib only. We call the Anthropic REST API via `urllib` to
avoid pip-installing `anthropic` into TD's Python — keeps the .tox truly
drop-in.

Reads `ANTHROPIC_API_KEY` from the env that launched TouchDesigner.
"""

import json
import os
import ssl
import urllib.request
import urllib.error


def _ssl_context():
    """TD's bundled Python on macOS ships without root CAs configured, so a
    plain `urlopen()` against api.anthropic.com fails with CERTIFICATE_VERIFY_FAILED.
    Resolution ladder:
    1. `certifi` if importable — Mozilla CA bundle, fully verified.
    2. Default context — works if TD's Python found a system CA store.
    3. Unverified — last resort. Known endpoint (api.anthropic.com), trusted
       Wi-Fi, hackathon scope. A red-team MITM on the LAN could still grab
       prompts; if that matters, install certifi into TD's Python."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    try:
        ctx = ssl.create_default_context()
        # Sanity-check: does it have any CAs? If not, fall through.
        if ctx.get_ca_certs():
            return ctx
    except Exception:
        pass
    print("unicorner: no CA bundle available; falling back to unverified SSL.")
    return ssl._create_unverified_context()

ANTHROPIC_API_URL = 'https://api.anthropic.com/v1/messages'
ANTHROPIC_VERSION = '2023-06-01'
DEFAULT_MODEL     = 'claude-sonnet-4-6'

# Local-only config file (gitignored). Resolves relative to the running
# TouchDesigner project's folder so it sits next to main.toe and never lands
# in the repo. Format: {"anthropic_api_key": "sk-ant-..."}.
CONFIG_FILENAME = '.unicorner_config.json'

# Per-scene JSONL event log (gitignored). One file per scene_id under
# `<project.folder>/.unicorner_log/<scene>.jsonl`. Every generate event
# appends one record so we can post-mortem failures + accumulate a corpus
# for the F1 feedback loop later.
LOG_DIRNAME = '.unicorner_log'

# Tag that prioritizes a COMP in the catalog. No longer *required* — see
# extract_catalog below — but tagged COMPs get a richer description because
# we trust they were curated.
LAYER_B_TAG = 'unicorner.layer-b'

# TD parameter style → catalog `type`. Keep in sync with poc/2-catalog/extract.py.
STYLE_MAP = {
    'Float':  'float',
    'Int':    'int',
    'Toggle': 'bool',
    'Pulse':  'pulse',
}

SEMANTIC_OVERRIDES = {
    'Colorhue': 'hue',
    'Active':   'toggle',
}

# Curated map of built-in params to expose per op type. Only the things a DJ
# would plausibly modulate — keeps the catalog focused and avoids drowning
# the model in TD's hundreds of built-in pars.
#
# Keys are op `type` strings (op.type, lowercase already). Values are lists
# of built-in param names. We filter by style at walk-time so only
# Float/Int/Toggle/Pulse make it through (no Str/Menu noise).
BUILTIN_PARAMS_BY_TYPE = {
    # CHOPs — modulators
    'lfo':       ['rate', 'amp', 'offset', 'phase'],
    'noise':     ['period', 'amp', 'offset', 'roughness'],
    'math':      ['gain', 'preoff', 'postoff'],
    'constant':  ['value0', 'value1', 'value2', 'value3'],
    'envelope':  ['attack', 'decay'],
    # TOPs — visual effects
    'level':     ['brightness', 'contrast', 'gamma', 'opacity', 'invert'],
    'blur':      ['size'],
    'hsvadjust': ['hueoffset', 'saturationmult', 'valuemult'],
    'ramp':      ['phase'],
    'feedback':  ['opacity'],
    'composite': ['opacity'],
    # MATs — materials
    'phong':     ['colorr', 'colorg', 'colorb', 'emitr', 'emitg', 'emitb',
                  'shininess', 'roughness', 'alpha'],
    'constantmat': ['colorr', 'colorg', 'colorb', 'alpha'],
    'pbrmat':    ['basecolorr', 'basecolorg', 'basecolorb', 'metallic',
                  'roughness', 'emissionr', 'emissiong', 'emissionb'],
    # 3D
    'geo':       ['tx', 'ty', 'tz', 'rx', 'ry', 'rz',
                  'sx', 'sy', 'sz', 'uniformscale'],
    'transform': ['tx', 'ty', 'tz', 'rx', 'ry', 'rz', 'sx', 'sy', 'sz'],
    'cam':       ['fov', 'tx', 'ty', 'tz'],
    'light':     ['dimmer', 'lightcolorr', 'lightcolorg', 'lightcolorb'],
    'ambient':   ['dimmer', 'lightcolorr', 'lightcolorg', 'lightcolorb'],
}

# Op types to skip entirely — UI plumbing, system ops, or things with no
# modulatable params worth showing the model.
SKIP_OP_TYPES = {
    'null', 'in', 'out', 'select', 'table', 'evaluate', 'merge', 'switch',
    'parameter', 'parameterCHOP', 'reference',
}


# ---------------------------------------------------------------------------
# Catalog extraction (in-process equivalent of poc/2-catalog/extract.py)
# ---------------------------------------------------------------------------

def extract_catalog(scene_root: str, scene_id: str, scene_label: str = None) -> dict:
    """Walk the scene_root subtree and emit a scene-wide parameter catalog.

    For every op the walker hits, it surfaces:
      - all `customPars` (the curated knobs the scene author exposed)
      - a curated set of *built-in* params per op type (BUILTIN_PARAMS_BY_TYPE)

    Each entry is one op (called a "module" for back-compat with the spec
    schema). The LLM uses these to design controls that drive the scene
    directly — no tagging required. COMPs tagged `unicorner.layer-b` are
    marked `curated: true` so the model can prefer them when both are
    available.
    """
    root = op(scene_root)  # noqa: F821 — TD-injected global
    modules = []
    if root is None:
        return _empty_catalog(scene_id, scene_label)

    # Include the root itself (in case the user set Target directly to a
    # scene COMP with custom params) plus all descendants.
    candidates = [root] + list(root.findChildren())
    seen = set()
    for node in candidates:
        if node.path in seen:
            continue
        seen.add(node.path)
        # Skip the controller surface itself — it's a generated artifact, not
        # something the model should bind to (causes self-referential loops).
        if CONTROLLER_SURFACE_TAG in (node.tags or set()):
            continue
        node_type = (node.type or '').lower()
        if node_type in SKIP_OP_TYPES:
            continue

        custom_entries  = [_param_to_entry(node, p) for p in node.customPars]
        builtin_entries = []
        for par_name in BUILTIN_PARAMS_BY_TYPE.get(node_type, []):
            par = getattr(node.par, par_name, None)
            if par is None:
                continue
            entry = _param_to_entry(node, par, source='builtin')
            if entry is not None:
                builtin_entries.append(entry)
        entries = [e for e in custom_entries if e is not None] + builtin_entries
        if not entries:
            continue

        modules.append({
            'id':         node.name,
            'path':       node.path,
            'type':       node_type,
            'label':      node.name.replace('_', ' ').title(),
            'curated':    LAYER_B_TAG in (node.tags or set()),
            'parameters': entries,
        })

    modules.sort(key=lambda m: (not m['curated'], m['path']))  # curated first
    return {
        'schema_version': '0.1',
        'scene_id':       scene_id,
        'scene_label':    scene_label or scene_id,
        'modules':        modules,
    }


def trim_catalog(catalog: dict, mode: str = 'full') -> dict:
    """Reduce catalog size to stay within API token-rate limits.

    mode='full'    — no change (default)
    mode='curated' — drop builtin params from non-curated modules; keeps
                     custom params on every module
    mode='minimal' — keep only curated modules; drop all builtin params
                     everywhere (smallest possible catalog)
    """
    if mode == 'full':
        return catalog
    modules = catalog.get('modules') or []
    result = []
    for m in modules:
        curated = m.get('curated', False)
        if mode == 'minimal' and not curated:
            continue
        params = [p for p in (m.get('parameters') or [])
                  if p.get('source') != 'builtin']
        if not params:
            continue
        result.append({**m, 'parameters': params})
    return {**catalog, 'modules': result}


def _empty_catalog(scene_id: str, scene_label: str = None) -> dict:
    return {
        'schema_version': '0.1',
        'scene_id':       scene_id,
        'scene_label':    scene_label or scene_id,
        'modules':        [],
    }


def _param_to_entry(comp, par, source: str = 'custom'):
    type_ = STYLE_MAP.get(par.style)
    if type_ is None:
        return None
    entry = {
        'name':     par.name,
        'path':     f'{comp.path}/{par.name}',
        'label':    par.label or par.name,
        'type':     type_,
        'default':  par.default,
        'semantic': SEMANTIC_OVERRIDES.get(par.name, par.name.lower()),
        'source':   source,  # 'custom' or 'builtin' — design hint for the LLM
    }
    if type_ in ('float', 'int'):
        entry['min'] = par.normMin
        entry['max'] = par.normMax
    return entry


# ---------------------------------------------------------------------------
# DJay Pro catalog — channels available from the Algoriddim integration COMP
# ---------------------------------------------------------------------------
#
# The COMP's Setup page has an optional `Djaypath` param pointing at either
# a CHOP (channels read directly) or a COMP (we look inside for a null/out
# CHOP). Empty path → returns None → generator skips routings entirely.

# Substring → semantic hint. Order matters only for the first-match: more
# specific keys should appear before broader ones (none currently overlap,
# but if they do, sort them by specificity).
# First-match substring hints. Order matters — more-specific keys must come
# before broader ones (e.g. 'rampbar' before 'bar', 'amplitude' before 'amp').
# Tags this maps to are advisory hints for the model, not enforced anywhere.
DJAY_SEMANTIC_HINTS = (
    # BPM / tempo
    ('bpm',       'bpm'),
    ('tempo',     'bpm'),         # Ableton Link names BPM "tempo"
    # Phase ramps (0..1 within a cycle)
    ('rampbar',   'ramp'),        # 0..1 ramp within a bar
    ('rampbeat',  'ramp'),        # 0..1 ramp within a beat
    ('barphase',  'ramp'),        # djay /barPhase
    ('phase',     'ramp'),
    ('ramp',      'ramp'),        # bare 'ramp' — must come before 'amp'!
    # Per-beat trigger pulses
    ('pulse',     'trigger'),
    ('kick',      'trigger'),
    ('snare',     'trigger'),
    # Counters that increment per-beat / per-bar (NOT instantaneous triggers
    # — they're monotonic counts; use rampbar/rampbeat or pulse for actual
    # event-driven behavior). Tagged 'counter' so the model treats them as
    # values, not impulses.
    ('beat',      'counter'),
    ('bar',       'counter'),
    ('sixteenths','counter'),
    ('count',     'counter'),
    # Continuous oscillators
    ('sine',      'continuous'),  # Ableton Link sine -1..1 synced to beat
    # Amplitudes / stem volumes
    ('amplitude', 'amplitude'),   # must come before 'amp'
    ('bass',      'amplitude'),
    ('drums',     'amplitude'),
    ('vocal',     'amplitude'),
    ('harmonic',  'amplitude'),   # djay neuralmix stem
    ('volume',    'amplitude'),   # djay /audibleVolume channels
    ('meter',     'amplitude'),   # djay mixer meter
    ('mid',       'amplitude'),
    ('treble',    'amplitude'),
    ('rms',       'amplitude'),
    ('amp',       'amplitude'),
    ('level',     'amplitude'),
)


def _infer_djay_semantic(name: str) -> str:
    lower = (name or '').lower()
    for needle, tag in DJAY_SEMANTIC_HINTS:
        if needle in lower:
            return tag
    return 'unknown'


def extract_djay_catalog(djay_path: str) -> dict:
    """Read available CHOP channels from the DJay Pro integration op.

    `djay_path` can point at a CHOP directly, or a COMP that contains a CHOP
    output. Returns `{path, chop_path, channels: [{name, value, semantic}]}`
    on success. Returns `None` when the path is empty or doesn't resolve to
    a CHOP with channels — caller treats `None` as "no DJay; generate
    controls-only as today."
    """
    if not djay_path:
        return None
    node = op(djay_path)  # noqa: F821 — TD-injected global
    if node is None:
        return None

    chop = None
    chop_path = None
    # Case 1: it's a CHOP with channels.
    if hasattr(node, 'numChans') and getattr(node, 'numChans', 0) > 0:
        chop = node
        chop_path = node.path
    else:
        # Case 2: it's a COMP — look inside for a usable CHOP output. Prefer
        # null/out CHOPs (the conventional "publish surface"); fall back to
        # any CHOP child with channels.
        try:
            children = list(node.children)
        except Exception:
            children = []
        for child in children:
            if (child.type or '').lower() in ('null', 'out') and getattr(child, 'numChans', 0) > 0:
                chop = child; chop_path = child.path
                break
        if chop is None:
            for child in children:
                if getattr(child, 'numChans', 0) > 0:
                    chop = child; chop_path = child.path
                    break

    if chop is None:
        return None

    channels = []
    for i in range(chop.numChans):
        ch = chop[i]
        try:
            value = float(ch[0])
        except Exception:
            value = None
        channels.append({
            'name':     ch.name,
            'value':    value,
            'semantic': _infer_djay_semantic(ch.name),
        })

    return {
        'path':      djay_path,
        'chop_path': chop_path,
        'channels':  channels,
    }


# ---------------------------------------------------------------------------
# Prompt assembly + Anthropic call
# ---------------------------------------------------------------------------

# System prompt. The canonical source is ai/prompts/controller-from-catalog.md
# (between the BEGIN_SYSTEM_PROMPT / END_SYSTEM_PROMPT markers); this constant
# is kept in sync by `python ai/sync_prompt.py` because the drop-in COMP must
# carry its prompt as a literal string at TD runtime (no filesystem access).
# CI / pre-commit should run `python ai/sync_prompt.py --check` to catch drift.
SYSTEM_PROMPT = """## System prompt

You design opinionated control surfaces for a DJ performing visuals in TouchDesigner. The DJ already mixes music; you give them a small, well-shaped set of controls (<=12) that they can play **without thinking**.

You operate in *planning mode*: instead of always committing to one answer, you propose 2–3 *meaningfully different* options when the request admits multiple good interpretations (e.g. "intensity," "depth," "make it interesting"). The user picks one. Reserve single-spec output for requests that are unambiguous (e.g. "set speed to 4," "make speed go 0–8 exponential").

You receive a JSON parameter catalog describing a TD scene — every modulatable parameter in the scene, both author-curated custom params and built-in params on relevant ops (LFO rates, material colors, transform tx/ty/tz, etc.). Each parameter entry has:
  - `path`: the verbatim TD path you must use in bindings
  - `type`: float | int | bool | pulse
  - `min`/`max`: the underlying param's natural range
  - `source`: "custom" (scene author exposed it) or "builtin" (TD op default)
  - `semantic`: a one-word hint about the param's role
  - Each module also has a `type` (op type, e.g. `lfo`, `phong`) and `curated: true` if the scene author marked it intentional

Your job: turn the user's request ("make me an intensity control", "give me a DJ controller") into a `ControllerSpec` that picks the most *expressive* combination of params for that request, shapes them sensibly, and limits them to ranges where they stay musically useful.

## Output

Pick exactly one of these two top-level shapes:

**(a) Single spec** — request was unambiguous:
```
{"schema_version":"0.1","scene_id":"<copy from catalog>","rationale":"…","controls":[…],"layout":[…]}
```

**(b) Alternatives** — request admits multiple good interpretations. 2–3 candidates, each with a one-line label and short description, plus its own complete spec:
```
{"alternatives":[
  {"id":"a","label":"Bright + lively","description":"why this interpretation","spec":{schema_version,scene_id,rationale,controls,layout}},
  {"id":"b","label":"Subtle pulse","description":"…","spec":{…}},
  {"id":"c","label":"Slow build","description":"…","spec":{…}}
]}
```

Alternative labels should be 2–5 words and *concretely* describe the feel ("Bright + lively", "Audio-reactive flicker"), not the param list. Each alternative's spec is independent and must validate on its own.

The user sees only the labels + descriptions on chips; they tap one and you'll never run the others. So make the choices *substantively different* — different param sets, different curves, different macro groupings. Two near-identical alternatives are a waste.

**(c) Clarifying question** — request is too ambiguous to commit to even three alternatives. Ask one focused question with 2–4 short answer chips, no specs:
```
{"question":"What kind of feel are you after?","alternatives":[
  {"id":"a","label":"More like a synth pad","description":"slow, sustained, atmospheric"},
  {"id":"b","label":"More like a drum sequencer","description":"sharp, rhythmic, percussive"},
  {"id":"c","label":"Audio-reactive flicker","description":"jittery, reactive to peaks"}
]}
```

Use shape (c) only when you genuinely cannot tell what the user wants from the scene + prompt + prior history (e.g. "make it more", "do something cool"). Most ambiguous-but-bounded requests should use shape (b) instead and let the user pick a concrete option. Each answer chip's `label` should be a complete answer the user is choosing — not another question.

## Hard rules — violations make the spec unusable

1. **Output exactly one JSON object** in one of the two shapes above. No prose, no markdown fences, no commentary.
2. **Every `path` in your spec must appear verbatim in the catalog you were given.** Never invent paths or rename params.
3. **`scene_id` in your output equals `scene_id` in the catalog.**
4. **Maximum 12 controls.** Fewer is better. The DJ has two hands.
5. **Set `"type"` on every control** — the JSON key is literally `"type"`, not `"widget"`:
   - `float` -> `"type": "knob"` or `"type": "slider"`
   - `bool` -> `"type": "toggle"`
   - `pulse` -> `"type": "button"`
   - Multi-param sweep -> `"type": "macro"` (one input drives 2+ params at once)
   Example: `{"id":"speed","label":"Speed","type":"knob","bind":{"path":"/…/lfo1/rate","min":0.1,"max":4.0}}`
6. **Use macros liberally.** Most user requests ("intensity", "depth", "energy") are inherently multi-param. Bind 2–4 params per macro when they sweep together to produce the named feel.
7. **Use curves and clamps intentionally** — these are the difference between a usable knob and a magic-feeling one:
   - `curve: "exp"` for intensity / loudness / scale — low-end matters
   - `curve: "log"` for frequency-like things — high-end matters
   - `curve: "smooth"` for orientation / blends — soft ease
   - `pre_clamp: [a, b]` on a binding (or `from`/`to` on a macro leg) restricts the *input range* you actually use — pick subranges that stay musical and skip dead zones near 0 or saturation
   - `post_clamp: [a, b]` is a hard safety net on the output — e.g. never let opacity go below 0.1 (full disappear) or above 0.95 (loses headroom)
8. **Order matters.** Put high-impact controls first, fine-tuning last.
9. **Toggles for "active/on" go last.** Don't waste prime control real estate on it.
10. Include a short `rationale` (1–3 sentences): name the params you picked and *why they map to the request*. This is for debugging.

Design heuristics:
- The catalog's `semantic` and op `type` are hints — read them. `phong.emit*` = emission intensity; `lfo.amp` = modulation depth; `geo.uniformscale` = uniform size; etc.
- For a request like "intensity": pick 2–4 params that together convey energy/loudness/presence (e.g. material emit + light dimmer + LFO amplitude) and bind them under one macro with `exp` curve.
- Curated params (`curated: true`) were designed by the scene author. Prefer them when they fit; only reach for `builtin` when the curated set doesn't cover the request.
- Drop dull params (small range, low visible impact) and built-in params that are likely already wired to something else.
- Labels are short. "Intensity" not "Visual Intensity Multiplier".

## Optional: routings (when a `dj_catalog` is provided)

If — and only if — the user turn includes a `dj_catalog` block (DJay Pro
channels), you MAY add a top-level `routings` array to your spec. Routings
wire DJay signals directly into scene parameters (autonomous, music-reactive
behavior) and complement the manual `controls`. Without a `dj_catalog`,
NEVER emit `routings`.

Three routing types:

```
{
  "id":           "<snake_case>",
  "type":         "direct",
  "label":        "Bass -> Emit",
  "djay_channel": "<channel name from dj_catalog>",
  "target_path":  "<scene op path from catalog>",
  "target_param": "<param name on that op, lowercase>",
  "min":          0.0,
  "max":          1.0,
  "curve":        "linear" | "exp" | "log" | "smooth",
  "blend_control_id": "<optional: id of a control in this spec, 0-1 scales the effect>"
}
```

```
{
  "id":              "<snake_case>",
  "type":            "lfo_sync",
  "label":           "BPM -> Scale",
  "lfo_name":        "ai_lfo_<purpose>",
  "bpm_channel":     "bpm",
  "beats_per_cycle": 1,
  "rate_multiplier_control_id": "<optional control id for an x0.25..x4 knob>",
  "targets": [
    { "path": "<scene op path>", "param": "<lowercase>", "min": 0.8, "max": 1.2, "curve": "linear" }
  ]
}
```

```
{
  "id":              "<snake_case>",
  "type":            "bar_reset",
  "label":           "Bar -> LFO reset",
  "djay_channel":    "bar",
  "target_lfo_path": "<full path of an LFO op in the scene, e.g. /project1/scene/ai_lfo_bpm>"
}
```

```
{
  "id":              "<snake_case>",
  "type":            "triggered_speed",
  "label":           "Beat -> Phase advance",
  "djay_channel":    "pulse",            // trigger channel — rises briefly on each beat/bar
  "envelope_decay":  0.5,                 // seconds; how long each pulse pushes the integrator (default 0.5)
  "envelope_attack": 0.0,                 // seconds; usually 0 for snappy beat response
  "rate_multiplier_control_id": "<optional control id for a x0.25..x4 knob>",
  "wrap":            true,                // % 1 the integrator so phase-like targets stay in [0..1] before remap
  "targets": [
    { "path": "<scene op path>", "param": "<lowercase>", "min": 0.0, "max": 1.0, "curve": "linear" }
  ]
}
```

Routing hard rules:
- Every `djay_channel` (and `bpm_channel`) must be a channel name present in the provided `dj_catalog.channels`.
- Every `target_path` / `targets[].path` must be a module path present in the scene catalog.
- Every `*_control_id` must reference the `id` of a control you also include in `spec.controls`.
- `lfo_name` is a child name (no slashes). It will be created under the scene root, tagged for cleanup.
- `target_lfo_path` for `bar_reset` should reference an LFO you also create via `lfo_sync` in the same spec (use its computed path: `<scene_root>/<lfo_name>`), or an LFO already in the scene catalog with type `lfo`.
- `triggered_speed` should only use channels with `trigger` semantic (`pulse`, `kick`, `snare`) — using a counter or continuous channel would push the integrator unbounded.

Routing heuristics:
- `direct` for amplitude-like channels (`bass`, `mid`, `rms`, etc.) driving visible scalar params (brightness, emit, scale).
- `lfo_sync` whenever the user wants *smooth oscillation* that should follow tempo. `beats_per_cycle: 1` = pulse per beat, `4` = per bar. Best for sine-like motion (size pulse, color sway).
- `triggered_speed` whenever the user wants something to *advance per beat* — phase cycling a palette, a counter ticking up, anything where each beat causes a discrete forward step. Pair with a `rate_multiplier_control_id` knob so the DJ can speed up / slow down without re-prompting.
- `bar_reset` only when the user explicitly asks to "reset" / "restart" / "pulse on the bar" — adds a CHOP Execute DAT, which is heavier than an expression.
- Pair a `direct` routing with a `blend_control_id` knob when the DJ might want to dial the music reactivity in or out. Pair an `lfo_sync` with a `rate_multiplier_control_id` knob when they might want to half-time / double-time.
- Keep routings to ≤ 6. Each one is a thing the DJ has to mentally track.
- A spec with no DJay-relevant request should omit `routings` (or set it to `[]`) even when a `dj_catalog` is provided. Don't manufacture music-reactivity the user didn't ask for."""


def build_messages(catalog: dict,
                   user_prompt: str,
                   history: list = None,
                   current_spec: dict = None,
                   scene_summary: str = None,
                   dj_catalog: dict = None) -> list:
    """Pure function — no TD calls. Builds the messages list that will be
    POSTed to Anthropic. Split out from generate_spec() so the callbacks
    layer can extract the catalog on the main thread, then build messages
    here, then ship the result to a worker thread for the HTTP call.

    `scene_summary`, if provided, is a plain-language description of the
    scene (typically authored by the DJ via the "Re-scan scene" button and
    optionally hand-edited). It's injected before the catalog as shared
    context so the model writes a spec against the DJ's mental picture of
    the scene rather than just the raw param dump.

    `dj_catalog`, if provided, lists DJay Pro CHOP channels available for
    routing. The model is allowed to emit a top-level `routings` array in
    its spec when this is present. Absence of `dj_catalog` is the model's
    signal to never emit routings.
    """
    user_turn_lines: list = []
    if scene_summary:
        user_turn_lines += [
            "Shared understanding of this scene (authored or confirmed by the user — trust this):",
            "",
            scene_summary.strip(),
            "",
        ]
    user_turn_lines += ["Parameter catalog:", "", json.dumps(catalog, indent=2), ""]
    if dj_catalog is not None:
        user_turn_lines += [
            "DJay Pro catalog (use these channel names verbatim in any `routings` you emit):",
            "",
            json.dumps(dj_catalog, indent=2),
            "",
        ]
    if current_spec is not None:
        user_turn_lines += [
            "Current applied ControllerSpec (refine this rather than starting over unless asked):",
            "",
            json.dumps(current_spec, indent=2),
            "",
        ]
    user_turn_lines += [
        "User request:",
        "",
        user_prompt,
        "",
        "Output one of these two shapes (no markdown fences, no prose).",
        "",
        "If the request is unambiguous (single answer makes sense):",
        '  {"schema_version":"0.1","scene_id":"' + catalog['scene_id'] + '",'
        '"rationale":"…","controls":[{"id":"…","label":"…","type":"knob","bind":{"path":"…","min":0,"max":1}},…],"layout":[…]'
        + (',"routings":[…]' if dj_catalog is not None else '')
        + '}',
        "",
        "If the request admits multiple good interpretations (2–3 meaningfully different options):",
        '  {"alternatives":[{"id":"a","label":"<short>","description":"<one line>","spec":{"schema_version":"0.1","scene_id":"'
        + catalog['scene_id']
        + '","rationale":"…","controls":[{"id":"…","label":"…","type":"knob","bind":{"path":"…","min":0,"max":1}}],"layout":[]'
        + (',"routings":[…]' if dj_catalog is not None else '')
        + '}}, ...]}',
        "",
        'Every control MUST have "type" (not "widget"). Valid values: "knob", "slider", "toggle", "button", "macro".',
    ]
    user_turn = "\n".join(user_turn_lines)

    messages = []
    for h in (history or []):
        role = h.get('role')
        content = h.get('content')
        if role in ('user', 'assistant') and content:
            messages.append({'role': role, 'content': content})
    messages.append({'role': 'user', 'content': user_turn})
    return messages


def parse_response(response_text: str, catalog: dict, dj_catalog: dict = None):
    """Pure function — no TD calls. Parses the model output into one of two
    shapes:

      - single-spec mode: returns `{'mode': 'spec', 'spec': <ControllerSpec>}`
      - alternatives mode: returns `{'mode': 'alternatives', 'alternatives': [...]}`

    Each alternative is `{'id', 'label', 'description', 'spec': <ControllerSpec>}`.
    Each spec is normalized + validated; an invalid alternative is dropped
    (with a debug print) rather than failing the whole batch.

    `dj_catalog`, when present, gates and validates any `routings` array on
    the spec. Invalid individual routings are dropped (with a warning log)
    rather than failing the whole spec — degraded mode beats no spec at all.
    """
    global _LAST_RESPONSE
    _LAST_RESPONSE = response_text
    parsed = _parse_spec(response_text, catalog)

    if isinstance(parsed, dict) and isinstance(parsed.get('alternatives'), list):
        # Question shape: top-level `question` text + spec-less alternatives.
        # The model is asking for clarification before committing to a spec.
        question_text = parsed.get('question')
        if isinstance(question_text, str) and question_text.strip():
            answers = []
            for i, alt in enumerate(parsed['alternatives']):
                if not isinstance(alt, dict):
                    continue
                label = str(alt.get('label') or '').strip()
                if not label:
                    continue
                answers.append({
                    'id':          str(alt.get('id') or chr(ord('a') + i)),
                    'label':       label,
                    'description': str(alt.get('description') or ''),
                    'kind':        'question',
                })
            if not answers:
                raise RuntimeError("Model returned a question with no answer chips.")
            return {
                'mode':         'question',
                'question':     question_text.strip(),
                'alternatives': answers,
            }

        alts_out = []
        for i, alt in enumerate(parsed['alternatives']):
            if not isinstance(alt, dict):
                continue
            alt_spec = alt.get('spec')
            if not isinstance(alt_spec, dict):
                continue
            alt_spec = _normalize_spec(alt_spec, catalog)
            try:
                _validate_spec(alt_spec, catalog, response_text)
            except Exception as e:
                print(f"unicorner: dropping invalid alternative {alt.get('id', i)!r}: {e}")
                continue
            _sanitize_routings(alt_spec, catalog, dj_catalog)
            alts_out.append({
                'id':          str(alt.get('id') or chr(ord('a') + i)),
                'label':       str(alt.get('label') or f"Option {chr(ord('A') + i)}"),
                'description': str(alt.get('description') or ''),
                'kind':        'choice',
                'spec':        alt_spec,
            })
        if not alts_out:
            raise RuntimeError("Model returned alternatives but none validated.")
        return {'mode': 'alternatives', 'alternatives': alts_out}

    # Single-spec shape (legacy / unambiguous).
    spec = _normalize_spec(parsed, catalog)
    _validate_spec(spec, catalog, response_text)
    _sanitize_routings(spec, catalog, dj_catalog)
    return {'mode': 'spec', 'spec': spec}


_LAST_RESPONSE: str = ''  # last raw model response


def generate_spec(scene_root: str,
                  scene_id: str,
                  user_prompt: str,
                  history: list = None,
                  current_spec: dict = None,
                  scene_label: str = None,
                  scene_summary: str = None,
                  djay_path: str = None,
                  model: str = DEFAULT_MODEL,
                  comp = None) -> dict:
    """Synchronous end-to-end: catalog → API → validated spec.

    Kept for one-shot scripted use (e.g. testing from MCP). The runtime
    path through `controller_webserver_script._handle_generate` does this
    in pieces across a worker thread to avoid freezing TD's cook.

    `djay_path`, when provided, enables routings: the function extracts the
    DJay CHOP catalog and feeds it to the model alongside the scene catalog.
    """
    catalog    = extract_catalog(scene_root, scene_id, scene_label)
    dj_catalog = extract_djay_catalog(djay_path) if djay_path else None
    messages   = build_messages(catalog, user_prompt, history, current_spec,
                                scene_summary, dj_catalog=dj_catalog)
    response_text = _call_anthropic(messages, model=model, api_key=resolve_api_key(comp))
    parsed = parse_response(response_text, catalog, dj_catalog=dj_catalog)
    # Back-compat: ad-hoc callers expect a spec dict. If alternatives, return
    # the first one. Production path uses parse_response directly and handles
    # both modes (see controller_webserver_script._apply_pending).
    if parsed['mode'] == 'spec':
        return parsed['spec']
    if parsed['mode'] == 'alternatives':
        return parsed['alternatives'][0]['spec']
    # Question mode: no spec was produced. Caller has to handle this.
    raise RuntimeError(f"Model asked a clarifying question instead of a spec: {parsed.get('question')!r}")


# ---------------------------------------------------------------------------
# Scene summarization (background pre-step for the chat dialog)
# ---------------------------------------------------------------------------

SUMMARY_SYSTEM_PROMPT = """You read a TouchDesigner scene's parameter catalog and produce a short, plain-language description of what the scene is and what each subsystem does. The output is shown to a DJ who will read it, optionally correct any details that look wrong, and then ask for a controller to be generated against that shared understanding.

## Output

- Plain prose only. No JSON, no markdown headers, no bullet points longer than one line.
- Keep it under ~120 words.
- Lead with a one-sentence scene description ("This looks like a particle system with feedback and a noise-driven displacement.").
- Then group the controllable params by subsystem and name the ones you'd modulate. Use the param's `label` or `semantic`, not the raw `path`.
- Note when the same intuitive concept (e.g. "brightness", "speed") is controllable in multiple places — those are good macro candidates.
- Don't invent params that aren't in the catalog.
- Don't propose controls or speculate about how to play it — that's a later step. Just describe what's there.
"""


def summarize_scene(scene_root: str,
                    scene_id: str,
                    scene_label: str = None,
                    model: str = DEFAULT_MODEL,
                    comp = None) -> str:
    """Synchronous: catalog → Anthropic → plain-language scene summary.

    Production path threads this through a worker — see
    `controller_webserver_script._handle_understand_scene`.
    """
    catalog = extract_catalog(scene_root, scene_id, scene_label)
    return summarize_catalog(catalog, model=model, api_key=resolve_api_key(comp))


def summarize_catalog(catalog: dict, model: str = DEFAULT_MODEL, api_key: str = None) -> str:
    """Lower-level: call Anthropic to summarize an already-extracted catalog.
    Exposed so the threaded path can do the catalog walk on the main thread
    (TD ops) and the HTTP call on a worker.
    """
    if not api_key:
        raise RuntimeError(_api_key_not_found_msg())
    modules = catalog.get('modules') or []
    if not modules:
        return "The scene has no controllable parameters yet — either the target points at an empty COMP, or all ops were skipped because they have nothing modulatable."

    user_turn = (
        "Parameter catalog:\n\n"
        + json.dumps(catalog, indent=2)
        + "\n\nDescribe this scene in plain language for the DJ who'll play it."
    )
    body = {
        'model':      model,
        'max_tokens': 600,
        'system':     SUMMARY_SYSTEM_PROMPT,
        'messages':   [{'role': 'user', 'content': user_turn}],
    }
    req = urllib.request.Request(
        ANTHROPIC_API_URL,
        data=json.dumps(body).encode('utf-8'),
        headers={
            'x-api-key':         api_key,
            'anthropic-version': ANTHROPIC_VERSION,
            'content-type':      'application/json',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=30, context=_ssl_context()) as resp:
            payload = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', errors='replace')
        raise RuntimeError(f"Anthropic API HTTP {e.code}: {detail}") from e
    content = payload.get('content', [])
    text_parts = [block.get('text', '') for block in content if block.get('type') == 'text']
    return ''.join(text_parts).strip()


def _call_anthropic(messages: list, model: str, api_key: str = None) -> str:
    if not api_key:
        raise RuntimeError(_api_key_not_found_msg())
    body = {
        'model':      model,
        'max_tokens': 4096,
        'system':     SYSTEM_PROMPT,
        'messages':   messages,
    }
    req = urllib.request.Request(
        ANTHROPIC_API_URL,
        data=json.dumps(body).encode('utf-8'),
        headers={
            'x-api-key':         api_key,
            'anthropic-version': ANTHROPIC_VERSION,
            'content-type':      'application/json',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=30, context=_ssl_context()) as resp:
            payload = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', errors='replace')
        raise RuntimeError(f"Anthropic API HTTP {e.code}: {detail}") from e

    # Standard Messages API response shape: {content: [{type:"text", text:"..."}], ...}
    content = payload.get('content', [])
    text_parts = [block.get('text', '') for block in content if block.get('type') == 'text']
    return ''.join(text_parts).strip()


def _parse_spec(text: str, catalog: dict) -> dict:
    """The system prompt forbids markdown fences, but be defensive — strip them
    if the model slips up."""
    t = text.strip()
    if t.startswith('```'):
        # drop the first fence line + a trailing fence
        lines = t.split('\n')
        if lines[0].startswith('```'):
            lines = lines[1:]
        if lines and lines[-1].startswith('```'):
            lines = lines[:-1]
        t = '\n'.join(lines).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Generator output is not valid JSON: {e}\n----\n{text}") from e


def _normalize_spec(spec: dict, catalog: dict) -> dict:
    """Canonicalize common model deviations from the strict ControllerSpec
    schema. Real-world behavior (Sonnet 4.6 in particular):
      - emits `bindings` on macros instead of `macro_bindings`
      - emits flat `path`/`min`/`max`/`curve` on knob/toggle/button instead
        of a nested `bind: {...}` object
      - omits `param_type` (which the catalog already knows)

    Rather than nag the prompt or fail validation, normalize in place. The
    output is canonical-form spec downstream code can rely on.
    """
    if not isinstance(spec, dict):
        return spec

    path_to_type = {}
    for m in catalog.get('modules', []):
        for p in m.get('parameters', []):
            path_to_type[p['path']] = p['type']

    BIND_FIELDS = ('path', 'param_type', 'min', 'max',
                   'curve', 'pre_clamp', 'post_clamp')

    for ctrl in spec.get('controls', []) or []:
        # Some Sonnet outputs use `widget` instead of `type`. Promote so the
        # rest of the normalizer / apply path can rely on `type`.
        if 'type' not in ctrl and 'widget' in ctrl:
            ctrl['type'] = ctrl.pop('widget')
        if 'type' not in ctrl:
            bind = ctrl.get('bind') or {}
            ptype = ctrl.get('param_type') or bind.get('param_type') or ''
            if ptype == 'bool':
                ctrl['type'] = 'toggle'
            elif ptype == 'pulse':
                ctrl['type'] = 'button'
            # float/int left absent → _validate_spec raises a clear RuntimeError
        ctype = ctrl.get('type')

        if ctype == 'macro':
            # Accept common aliases for macro_bindings. Sonnet has been seen
            # to emit `bindings`, `legs`, `targets`, `params` at various
            # times — all clearly intended as the macro's binding list.
            if 'macro_bindings' not in ctrl:
                for alias in ('bindings', 'legs', 'targets', 'params'):
                    if alias in ctrl and isinstance(ctrl[alias], list):
                        ctrl['macro_bindings'] = ctrl.pop(alias)
                        break
            for mb in ctrl.get('macro_bindings', []) or []:
                if 'param_type' not in mb and 'path' in mb:
                    mb['param_type'] = path_to_type.get(mb['path'], 'float')

        elif ctype in ('knob', 'slider', 'toggle', 'button'):
            # Accept singular-alias keys the model has emitted: `binding`,
            # `target`. Rename to canonical `bind`.
            if 'bind' not in ctrl:
                for alias in ('binding', 'target'):
                    if isinstance(ctrl.get(alias), dict):
                        ctrl['bind'] = ctrl.pop(alias)
                        break
            # Promote flat fields into a nested bind object if still absent.
            if 'bind' not in ctrl and 'path' in ctrl:
                bind = {}
                for f in BIND_FIELDS:
                    if f in ctrl:
                        bind[f] = ctrl.pop(f)
                ctrl['bind'] = bind
            if isinstance(ctrl.get('bind'), dict):
                bind = ctrl['bind']
                if 'param_type' not in bind and 'path' in bind:
                    bind['param_type'] = path_to_type.get(bind['path'], 'float')

    return spec


def _validate_spec(spec: dict, catalog: dict, raw_response: str = '') -> None:
    """Minimal structural + referential check. Raises on failure with a
    diagnostic snippet of the raw response so failures are debuggable from
    the iPad chat error message alone."""

    def _diag() -> str:
        keys = list(spec.keys()) if isinstance(spec, dict) else type(spec).__name__
        snippet = (raw_response or '')[:400].replace('\n', ' ')
        return f" (top-level keys={keys}; raw={snippet!r})"

    if not isinstance(spec, dict):
        raise RuntimeError("Spec is not a JSON object" + _diag())
    if spec.get('schema_version') != '0.1':
        raise RuntimeError(
            f"Spec schema_version != 0.1 (got {spec.get('schema_version')!r})" + _diag()
        )
    if spec.get('scene_id') != catalog['scene_id']:
        raise RuntimeError(
            f"Spec scene_id {spec.get('scene_id')!r} does not match catalog "
            f"{catalog['scene_id']!r}" + _diag()
        )
    controls = spec.get('controls')
    if not isinstance(controls, list) or not controls:
        raise RuntimeError("Spec has no controls" + _diag())
    if len(controls) > 12:
        raise RuntimeError(f"Spec has {len(controls)} controls; max 12" + _diag())

    catalog_paths = {p['path'] for m in catalog['modules'] for p in m['parameters']}
    VALID_TYPES = {'knob', 'slider', 'toggle', 'button', 'macro'}
    for i, ctrl in enumerate(controls):
        if 'type' not in ctrl or ctrl['type'] not in VALID_TYPES:
            raise RuntimeError(
                f"Control[{i}] missing/invalid `type` (expected one of {sorted(VALID_TYPES)}; "
                f"got {ctrl.get('type')!r})" + _diag()
            )
        if ctrl['type'] == 'macro':
            mbs = ctrl.get('macro_bindings')
            if not isinstance(mbs, list) or not mbs:
                raise RuntimeError(
                    f"Control[{i}] (macro {ctrl.get('id')!r}) has no macro_bindings" + _diag()
                )
            for mb in mbs:
                path = (mb or {}).get('path')
                if path not in catalog_paths:
                    raise RuntimeError(f"Spec macro binds path {path!r} not in catalog")
        else:
            bind = ctrl.get('bind')
            if not isinstance(bind, dict) or 'path' not in bind:
                raise RuntimeError(
                    f"Control[{i}] ({ctrl['type']} {ctrl.get('id')!r}) missing `bind.path`" + _diag()
                )
            if bind['path'] not in catalog_paths:
                raise RuntimeError(f"Spec binds path {bind['path']!r} not in catalog")


def _sanitize_routings(spec: dict, catalog: dict, dj_catalog) -> None:
    """In-place: drop any routings the model emitted that can't safely apply.
    Logs each drop and rewrites `spec['routings']` to the surviving subset.

    Validation rules (kept loose — the apply step is the ultimate gatekeeper,
    so we only drop entries that are clearly malformed or reference nonexistent
    catalog entries):
      - If `dj_catalog` is absent, all routings are dropped (the prompt
        forbade them but the model might have slipped).
      - Each routing must have a string `type` in the known set.
      - `djay_channel` / `bpm_channel` must exist in `dj_catalog.channels`.
      - `target_path` / `targets[].path` must exist in the scene catalog
        (any module path is fine; we don't enforce the param exists since
        builtin params like `phong.emitb` may not be in the catalog entry
        list explicitly — let the apply step catch missing pars at the
        target node level).
      - `*_control_id` must reference a control in `spec.controls`.
    """
    routings = spec.get('routings')
    if not isinstance(routings, list):
        if routings is not None:
            spec['routings'] = []
        return

    if dj_catalog is None:
        if routings:
            print(f"unicorner: dropping {len(routings)} routing(s) — no dj_catalog provided")
        spec['routings'] = []
        return

    channel_names = {c.get('name') for c in (dj_catalog.get('channels') or [])}
    module_paths = {m['path'] for m in catalog.get('modules', [])}
    control_ids = {c.get('id') for c in (spec.get('controls') or []) if isinstance(c, dict)}

    survivors = []
    for i, r in enumerate(routings):
        if not isinstance(r, dict):
            print(f"unicorner: dropping routing[{i}] — not an object")
            continue
        rtype = r.get('type')
        try:
            if rtype == 'direct':
                if r.get('djay_channel') not in channel_names:
                    raise ValueError(f"djay_channel {r.get('djay_channel')!r} not in dj_catalog")
                if r.get('target_path') not in module_paths:
                    raise ValueError(f"target_path {r.get('target_path')!r} not in scene catalog")
                blend = r.get('blend_control_id')
                if blend and blend not in control_ids:
                    raise ValueError(f"blend_control_id {blend!r} not in spec.controls")
            elif rtype == 'lfo_sync':
                bpm = r.get('bpm_channel') or 'bpm'
                if bpm not in channel_names:
                    raise ValueError(f"bpm_channel {bpm!r} not in dj_catalog")
                targets = r.get('targets')
                if not isinstance(targets, list) or not targets:
                    raise ValueError("lfo_sync has no targets")
                for t in targets:
                    if not isinstance(t, dict) or t.get('path') not in module_paths:
                        raise ValueError(f"lfo_sync target path {t.get('path') if isinstance(t, dict) else t!r} not in scene catalog")
                mult = r.get('rate_multiplier_control_id')
                if mult and mult not in control_ids:
                    raise ValueError(f"rate_multiplier_control_id {mult!r} not in spec.controls")
                if not r.get('lfo_name'):
                    raise ValueError("lfo_sync missing lfo_name")
            elif rtype == 'bar_reset':
                if r.get('djay_channel') not in channel_names:
                    raise ValueError(f"djay_channel {r.get('djay_channel')!r} not in dj_catalog")
                if not r.get('target_lfo_path'):
                    raise ValueError("bar_reset missing target_lfo_path")
            elif rtype == 'triggered_speed':
                if r.get('djay_channel') not in channel_names:
                    raise ValueError(f"djay_channel {r.get('djay_channel')!r} not in dj_catalog")
                targets = r.get('targets')
                if not isinstance(targets, list) or not targets:
                    raise ValueError("triggered_speed has no targets")
                for t in targets:
                    if not isinstance(t, dict) or t.get('path') not in module_paths:
                        raise ValueError(f"triggered_speed target path {t.get('path') if isinstance(t, dict) else t!r} not in scene catalog")
                mult = r.get('rate_multiplier_control_id')
                if mult and mult not in control_ids:
                    raise ValueError(f"rate_multiplier_control_id {mult!r} not in spec.controls")
            else:
                raise ValueError(f"unknown routing type {rtype!r}")
        except ValueError as e:
            print(f"unicorner: dropping routing[{i}] ({r.get('id')!r}, {rtype!r}): {e}")
            continue
        survivors.append(r)
    spec['routings'] = survivors


# ---------------------------------------------------------------------------
# Spec → scaffold (apply the ControllerSpec to a controller_surface COMP)
# ---------------------------------------------------------------------------

CONTROLLER_SURFACE_TAG = 'unicorner.controller-surface'
SURFACE_NAME           = 'controller_surface'
SURFACE_PAGE           = 'Surface'

# Tag stamped on every node the routing applier creates (LFO CHOPs, CHOP
# Execute DATs). Used for idempotent teardown — `_apply_routings` destroys
# all such ops under the scene root before rebuilding.
ROUTING_TAG = 'unicorner.routing'

# Per-scene record of target params that the routing applier set expressions
# on. We use this to clear those expressions on the next apply, so dropping
# a routing in a regeneration doesn't leave a dangling expression behind.
_last_routing_targets_by_scene: dict = {}  # scene_id -> [(path, par_name)]


def apply_spec(spec: dict, scene_parent: str, dj_chop_path: str = None) -> str:
    """Build/rebuild `<scene_parent>/controller_surface` from the spec. Returns
    the surface path so the caller can broadcast it.

    If `spec` includes a `routings` array and `dj_chop_path` is provided,
    also build the routing infrastructure (LFO CHOPs, CHOP Execute DATs,
    bound expressions) under `scene_parent`, tagged for idempotent teardown.
    """
    parent = op(scene_parent)  # noqa: F821 — TD-injected global
    if parent is None:
        raise RuntimeError(f"apply_spec: scene_parent {scene_parent!r} not found")

    surface_path = f"{scene_parent}/{SURFACE_NAME}"
    _teardown_surface(parent, surface_path, spec)

    surface = parent.create('baseCOMP', SURFACE_NAME)
    surface.nodeX = -600
    surface.nodeY = 300
    surface.tags.add(CONTROLLER_SURFACE_TAG)
    page = surface.appendCustomPage(SURFACE_PAGE)

    EXPRESSION = _par_mode_enum(parent).EXPRESSION

    # Build a lookup from control.id → surface custom-par name so routings
    # that reference a control by id (blend_control_id, rate_multiplier_…)
    # can resolve to the actual param name without guessing.
    control_id_to_par: dict = {}
    VALID_APPLY_TYPES = {'knob', 'slider', 'toggle', 'button', 'macro'}
    for i, ctrl in enumerate(spec['controls']):
        ctype = ctrl.get('type')
        if not ctype or ctype not in VALID_APPLY_TYPES:
            raise RuntimeError(
                f"Control[{i}] id={ctrl.get('id')!r} has missing/invalid "
                f"`type` {ctype!r} — expected one of {sorted(VALID_APPLY_TYPES)}. "
                f"Re-generate the controller."
            )
        _apply_control(ctrl, page, surface, surface_path, EXPRESSION)
        cid = ctrl.get('id')
        if cid:
            control_id_to_par[cid] = _id_to_par_name(cid)

    routings = spec.get('routings') or []
    scene_id = spec.get('scene_id') or scene_parent.rsplit('/', 1)[-1]
    _apply_routings(routings, scene_parent, dj_chop_path, surface_path,
                    control_id_to_par, scene_id, EXPRESSION)

    return surface_path


def _par_mode_enum(op_node):
    """Return TD's ParMode enum class by inspecting any par on the given op.
    Avoids depending on a specific par name like `helpdat` which exists on
    some COMP types but not on a freshly-created baseCOMP."""
    for p in op_node.pars():
        return type(p.mode)
    raise RuntimeError(f"_par_mode_enum: op {op_node.path!r} has no pars to inspect")


def _teardown_surface(parent, surface_path: str, _new_spec: dict) -> None:
    """Destroy the existing surface, clearing any expressions on downstream
    target params that referenced it (otherwise dangling expressions fire
    every cook).

    Walks *all* pars (not just customPars) since the spec can now bind
    built-in params too (phong.colorr, lfo.rate, etc.)."""
    name = surface_path.rsplit('/', 1)[-1]
    existing = parent.op(name)
    if existing is None:
        return
    CONSTANT = _par_mode_enum(parent).CONSTANT
    project_root = op('/')  # noqa: F821 — TD-injected global
    for node in project_root.findChildren():
        # Iterate all pars; pars() returns a flat tuple of every par.
        try:
            pars = node.pars()
        except Exception:
            continue
        for par in pars:
            try:
                if "EXPRESSION" in str(par.mode) and surface_path in (par.expr or ''):
                    par.expr = ''
                    par.mode = CONSTANT
            except Exception:
                continue
    existing.destroy()


def _apply_control(ctrl: dict, page, surface, surface_path: str, EXPRESSION):
    ctype = ctrl['type']
    cid   = ctrl['id']
    label = ctrl['label']
    name  = _id_to_par_name(cid)

    if ctype in ('knob', 'slider'):
        bind = ctrl['bind']
        lo = bind.get('min', 0.0)
        hi = bind.get('max', 1.0)
        p = page.appendFloat(name, label=label)[0]
        p.default  = lo
        p.min  = lo; p.max  = hi
        p.normMin = lo; p.normMax = hi
        p.clampMin = True; p.clampMax = True
        p.val = lo
        _bind_target(bind['path'], EXPRESSION,
                     expr=_direct_expression(surface_path, name, bind, lo, hi))

    elif ctype == 'toggle':
        bind = ctrl['bind']
        p = page.appendToggle(name, label=label)[0]
        p.default = False
        p.val = False
        _bind_target(bind['path'], EXPRESSION,
                     expr=f"op('{surface_path}').par.{name}")

    elif ctype == 'button':
        bind = ctrl['bind']
        p = page.appendPulse(name, label=label)[0]
        _ = p  # iPad renders the button; pulse-through is a v2 (Scope B) task
        _bind_target_noop(bind['path'])

    elif ctype == 'macro':
        p = page.appendFloat(name, label=label)[0]
        p.default = 0.0
        p.min = 0.0; p.max = 1.0
        p.normMin = 0.0; p.normMax = 1.0
        p.clampMin = True; p.clampMax = True
        p.val = 0.0
        for mb in ctrl['macro_bindings']:
            expr = _macro_expression(surface_path, name, mb)
            _bind_target(mb['path'], EXPRESSION, expr=expr)

    else:
        debug(f"unicorner: unknown control type {ctype!r}; skipping")  # noqa: F821


# --- Routings → scaffold (Scope B / DJay) --------------------------------
#
# `_apply_routings` is called from apply_spec after the controller surface
# has been built. It:
#   1. Tears down any nodes tagged `unicorner.routing` under `scene_parent`.
#   2. Clears expressions on target params set by *prior* routings (tracked
#      in `_last_routing_targets_by_scene`).
#   3. Builds the new routing infrastructure per the spec.
#   4. Records the new target params so the next apply can clean them up.

def _apply_routings(routings, scene_parent: str, dj_chop_path: str,
                    surface_path: str, control_id_to_par: dict, scene_id: str,
                    EXPRESSION) -> None:
    parent = op(scene_parent)  # noqa: F821
    if parent is None:
        return

    # 1. Teardown — destroy our prior tagged ops.
    for child in list(parent.children):
        if ROUTING_TAG in (child.tags or set()):
            try:
                child.destroy()
            except Exception as e:
                debug(f"unicorner: failed to destroy routing op {child.path!r}: {e}")  # noqa: F821

    # 2. Clear prior routing-set expressions (best-effort).
    CONSTANT = _par_mode_enum(parent).CONSTANT
    for target_path, par_name in _last_routing_targets_by_scene.get(scene_id, []):
        node = op(target_path)  # noqa: F821
        if node is None:
            continue
        par = getattr(node.par, par_name, None)
        if par is None:
            continue
        try:
            par.expr = ''
            par.mode = CONSTANT
        except Exception:
            pass

    if not routings:
        _last_routing_targets_by_scene[scene_id] = []
        return

    if not dj_chop_path:
        debug("unicorner: routings present but dj_chop_path missing; skipping apply")  # noqa: F821
        _last_routing_targets_by_scene[scene_id] = []
        return

    # 3. Apply each routing. We deliberately do all `lfo_sync` first so
    # `bar_reset` routings that reference a freshly-created LFO by full path
    # have a real op to point at when their callback DAT runs.
    new_targets: list = []
    lfo_node_x = -200
    lfo_node_y = 600

    def _bump():
        # Cheap layout: just spread nodes horizontally to keep them visible.
        nonlocal lfo_node_x
        lfo_node_x += 150

    for r in routings:
        if r.get('type') == 'lfo_sync':
            try:
                _apply_lfo_sync_routing(
                    r, parent, scene_parent, dj_chop_path, surface_path,
                    control_id_to_par, EXPRESSION, new_targets,
                    nx=lfo_node_x, ny=lfo_node_y,
                )
                _bump()
            except Exception as e:
                debug(f"unicorner: lfo_sync routing {r.get('id')!r} failed: {e}")  # noqa: F821

    for r in routings:
        if r.get('type') == 'direct':
            try:
                _apply_direct_routing(
                    r, dj_chop_path, surface_path, control_id_to_par,
                    EXPRESSION, new_targets,
                )
            except Exception as e:
                debug(f"unicorner: direct routing {r.get('id')!r} failed: {e}")  # noqa: F821

    for r in routings:
        if r.get('type') == 'bar_reset':
            try:
                _apply_bar_reset_routing(
                    r, parent, dj_chop_path,
                    nx=lfo_node_x, ny=lfo_node_y + 200,
                )
                _bump()
            except Exception as e:
                debug(f"unicorner: bar_reset routing {r.get('id')!r} failed: {e}")  # noqa: F821

    for r in routings:
        if r.get('type') == 'triggered_speed':
            try:
                _apply_triggered_speed_routing(
                    r, parent, dj_chop_path, surface_path,
                    control_id_to_par, EXPRESSION, new_targets,
                    nx=lfo_node_x, ny=lfo_node_y + 400,
                )
                _bump()
            except Exception as e:
                debug(f"unicorner: triggered_speed routing {r.get('id')!r} failed: {e}")  # noqa: F821

    # 4. Remember target params for the next teardown.
    _last_routing_targets_by_scene[scene_id] = new_targets


def _routing_direct_expression(dj_chop_path: str, channel: str,
                               surface_path: str, blend_par: str,
                               lo, hi, curve: str) -> str:
    """Build the expression for a `direct` routing. Pulls a 0..1-ish value
    from the DJay CHOP, multiplies by the blend knob (0..1) if any,
    optionally shapes via curve, then remaps to [lo, hi]."""
    base = f"op('{dj_chop_path}')['{channel}']"
    norm = base
    if blend_par:
        norm = f"({base}) * op('{surface_path}').par.{blend_par}"
    # Curve in 0..1 domain (assume the DJay channel is already 0..1-ish).
    shaped = _shape_norm(norm, curve or 'linear')
    span = (hi - lo) if hi != lo else 1.0
    return f"({shaped}) * ({span}) + ({lo})"


def _apply_direct_routing(r: dict, dj_chop_path: str, surface_path: str,
                          control_id_to_par: dict, EXPRESSION,
                          new_targets: list) -> None:
    target_path  = r['target_path']
    target_param = r['target_param']
    channel      = r['djay_channel']
    lo = float(r.get('min', 0.0))
    hi = float(r.get('max', 1.0))
    curve = r.get('curve', 'linear')
    blend_cid = r.get('blend_control_id')
    blend_par = control_id_to_par.get(blend_cid) if blend_cid else None

    node = op(target_path)  # noqa: F821
    if node is None:
        debug(f"unicorner: direct routing target node not found: {target_path}")  # noqa: F821
        return
    par = getattr(node.par, target_param, None)
    if par is None:
        debug(f"unicorner: direct routing target par not found: {target_path}.{target_param}")  # noqa: F821
        return

    expr = _routing_direct_expression(dj_chop_path, channel, surface_path,
                                      blend_par, lo, hi, curve)
    par.expr = expr
    par.mode = EXPRESSION
    new_targets.append((target_path, target_param))


def _apply_lfo_sync_routing(r: dict, parent, scene_parent: str,
                            dj_chop_path: str, surface_path: str,
                            control_id_to_par: dict, EXPRESSION,
                            new_targets: list, nx: int, ny: int) -> None:
    name = r['lfo_name']
    bpm_channel = r.get('bpm_channel') or 'bpm'
    beats_per_cycle = float(r.get('beats_per_cycle') or 1.0)
    mult_cid = r.get('rate_multiplier_control_id')
    mult_par = control_id_to_par.get(mult_cid) if mult_cid else None

    # Don't collide with an existing op of the same name. If one exists and
    # wasn't tagged routing (e.g. user-authored), bail out rather than nuke.
    existing = parent.op(name)
    if existing is not None:
        if ROUTING_TAG not in (existing.tags or set()):
            debug(f"unicorner: lfo_sync wants to create {name!r} but a non-routing op already exists; skipping")  # noqa: F821
            return
        existing.destroy()

    lfo = parent.create('lfoCHOP', name)
    lfo.tags.add(ROUTING_TAG)
    lfo.nodeX = nx; lfo.nodeY = ny

    # rate (Hz) = bpm/60 / beats_per_cycle * mult  →  1 cycle per beat
    # at beats_per_cycle=1, 1 per bar at 4.
    base_rate = f"op('{dj_chop_path}')['{bpm_channel}'] / 60.0 / {beats_per_cycle}"
    if mult_par:
        base_rate = f"({base_rate}) * op('{surface_path}').par.{mult_par}"
    try:
        lfo.par.rate.expr = base_rate
        lfo.par.rate.mode = EXPRESSION
    except Exception as e:
        debug(f"unicorner: failed to set lfo rate expression: {e}")  # noqa: F821

    # Bind each target to the LFO output (channel 0), remapped from the
    # LFO's natural [-1, 1] range to the target [min, max]. We don't use
    # _shape_norm here — most users want straight sine motion when they
    # ask for a BPM-synced LFO.
    lfo_path = lfo.path
    for t in r.get('targets', []):
        tnode = op(t['path'])  # noqa: F821
        if tnode is None:
            debug(f"unicorner: lfo_sync target node not found: {t['path']}")  # noqa: F821
            continue
        par = getattr(tnode.par, t['param'], None)
        if par is None:
            debug(f"unicorner: lfo_sync target par not found: {t['path']}.{t['param']}")  # noqa: F821
            continue
        tmin = float(t.get('min', 0.0))
        tmax = float(t.get('max', 1.0))
        # Map LFO -1..1 → 0..1 → tmin..tmax.
        norm = f"(op('{lfo_path}')[0] + 1.0) / 2.0"
        expr = f"({norm}) * ({tmax - tmin}) + ({tmin})"
        par.expr = expr
        par.mode = EXPRESSION
        new_targets.append((t['path'], t['param']))


def _apply_bar_reset_routing(r: dict, parent, dj_chop_path: str,
                              nx: int, ny: int) -> None:
    channel = r['djay_channel']
    target_lfo_path = r['target_lfo_path']

    # Name the DAT deterministically so we don't accumulate duplicates if
    # teardown ever misses something.
    name = f"ai_reset_{r.get('id') or channel}"
    name = ''.join(c for c in name if c.isalnum() or c == '_')[:30]

    existing = parent.op(name)
    if existing is not None:
        if ROUTING_TAG not in (existing.tags or set()):
            debug(f"unicorner: bar_reset wants to create {name!r} but a non-routing op already exists; skipping")  # noqa: F821
            return
        existing.destroy()

    dat = parent.create('chopexecuteDAT', name)
    dat.tags.add(ROUTING_TAG)
    dat.nodeX = nx; dat.nodeY = ny
    # Wire the DAT to the DJay CHOP. The TD attribute is `chop` on
    # chopexecuteDAT.
    try:
        dat.par.chop = dj_chop_path
    except Exception as e:
        debug(f"unicorner: failed to wire bar_reset DAT to {dj_chop_path}: {e}")  # noqa: F821
    # Watch only the rising edge — that's the actual "bar start" event.
    for attr in ('onoffon', 'Onoffon'):
        if hasattr(dat.par, attr):
            try:
                setattr(dat.par, attr, True)
            except Exception:
                pass
    # Other event types: turn them off to keep the callback quiet.
    for attr in ('whileon', 'offon', 'whileoff', 'valuechange'):
        if hasattr(dat.par, attr):
            try:
                setattr(dat.par, attr, False)
            except Exception:
                pass

    dat.text = (
        "# Auto-generated by unicorner routing (bar_reset).\n"
        f"_TARGET_LFO_PATH = {target_lfo_path!r}\n"
        f"_TRIGGER_CHANNEL = {channel!r}\n"
        "\n"
        "def onOffToOn(channel, sampleIndex, val, prev):\n"
        "    if channel.name != _TRIGGER_CHANNEL:\n"
        "        return\n"
        "    lfo = op(_TARGET_LFO_PATH)\n"
        "    if lfo is None:\n"
        "        return\n"
        "    init = getattr(lfo.par, 'initialize', None)\n"
        "    if init is None:\n"
        "        return\n"
        "    init.pulse()\n"
    )


def _safe_node_name(s: str, max_len: int = 30) -> str:
    """TD child names: alphanumeric + underscore only, can't be empty."""
    cleaned = ''.join(c if c.isalnum() or c == '_' else '_' for c in (s or ''))
    return (cleaned[:max_len] or 'unnamed')


def _apply_triggered_speed_routing(r: dict, parent, dj_chop_path: str,
                                   surface_path: str, control_id_to_par: dict,
                                   EXPRESSION, new_targets: list,
                                   nx: int, ny: int) -> None:
    """Scaffolds the per-beat-advance CHOP chain:

        djay_chop -> Select(channel) -> Envelope(attack, decay, exp)
                  -> Speed(integrate) -> expression on each target par

    The Speed CHOP output grows whenever the envelope is non-zero (i.e.,
    just after each trigger pulse). Multiplying by an optional knob lets the
    DJ scale the per-beat advance amount in real time. `wrap=True` (default)
    applies `% 1` so phase-like targets stay bounded before the [min,max]
    remap; `wrap=False` lets the integrator climb unbounded (useful for
    things like an iteration counter).
    """
    rid     = r.get('id') or 'unnamed'
    channel = r['djay_channel']
    decay   = float(r.get('envelope_decay', 0.5))
    attack  = float(r.get('envelope_attack', 0.0))
    mult_cid = r.get('rate_multiplier_control_id')
    mult_par = control_id_to_par.get(mult_cid) if mult_cid else None
    wrap    = bool(r.get('wrap', True))

    sel_name = _safe_node_name(f"ai_sel_{rid}")
    env_name = _safe_node_name(f"ai_env_{rid}")
    spd_name = _safe_node_name(f"ai_spd_{rid}")

    # Replace any prior routing-tagged ops with these names; bail if a
    # user-authored op is in the way (don't nuke their work).
    for nm in (sel_name, env_name, spd_name):
        existing = parent.op(nm)
        if existing is None:
            continue
        if ROUTING_TAG not in (existing.tags or set()):
            debug(f"unicorner: triggered_speed wants to create {nm!r} but a "  # noqa: F821
                  f"non-routing op already exists; skipping routing {rid!r}")
            return
        existing.destroy()

    # 1. Select CHOP — pull just the trigger channel out of the DJay CHOP.
    sel = parent.create('selectCHOP', sel_name)
    sel.tags.add(ROUTING_TAG)
    sel.nodeX = nx; sel.nodeY = ny
    try:
        sel.par.chop = dj_chop_path
    except Exception as e:
        debug(f"unicorner: triggered_speed Select.chop set failed: {e}")  # noqa: F821
    # Channel-name picker. TD exposes this under different attr names across
    # versions; try the common ones.
    for attr in ('channames', 'channelnames', 'chanames'):
        if hasattr(sel.par, attr):
            try:
                setattr(sel.par, attr, channel)
                break
            except Exception:
                pass

    # 2. Envelope CHOP — exponential attack/decay on the pulse.
    env = parent.create('envelopeCHOP', env_name)
    env.tags.add(ROUTING_TAG)
    env.nodeX = nx + 150; env.nodeY = ny
    env.inputConnectors[0].connect(sel)
    # Param names vary across TD versions. Set what's available; ignore the rest.
    for attr, val in (('attack', attack), ('decay', decay), ('release', decay),
                      ('method', 'exp'), ('Method', 'exp')):
        if hasattr(env.par, attr):
            try:
                setattr(env.par, attr, val)
            except Exception:
                pass

    # 3. Speed CHOP — integrate the envelope into a continuously-advancing value.
    spd = parent.create('speedCHOP', spd_name)
    spd.tags.add(ROUTING_TAG)
    spd.nodeX = nx + 300; spd.nodeY = ny
    spd.inputConnectors[0].connect(env)

    # 4. Bind each target via expression.
    spd_path = spd.path
    rate_mult_expr = (f"op('{surface_path}').par.{mult_par}"
                      if mult_par else "1.0")
    for t in r.get('targets', []):
        tnode = op(t['path'])  # noqa: F821
        if tnode is None:
            debug(f"unicorner: triggered_speed target node not found: {t['path']}")  # noqa: F821
            continue
        par = getattr(tnode.par, t['param'], None)
        if par is None:
            debug(f"unicorner: triggered_speed target par not found: {t['path']}.{t['param']}")  # noqa: F821
            continue
        tmin = float(t.get('min', 0.0))
        tmax = float(t.get('max', 1.0))
        base = f"op('{spd_path}')[0] * ({rate_mult_expr})"
        if wrap:
            base = f"(({base}) % 1.0)"
        expr = f"({base}) * ({tmax - tmin}) + ({tmin})"
        par.expr = expr
        par.mode = EXPRESSION
        new_targets.append((t['path'], t['param']))


# --- Expression shaping (Scope A) ----------------------------------------
#
# Each binding can specify optional shaping that's compiled into a single TD
# expression on the target par. Knob/slider direct bindings sweep over their
# own [lo, hi] range; macro legs sweep over [from, to]. In both cases:
#
#   1. Normalize input to 0..1 (only needed for direct bindings; macros are
#      already 0..1).
#   2. pre_clamp: clamp the 0..1 input — useful for "skip the bottom 10% and
#      the top 10% because those are dead zones."
#   3. curve: linear | exp | log | smooth — shapes the 0..1 to 0..1.
#   4. Scale to target range [from, to].
#   5. post_clamp: clamp the output in target units — a hard safety net.
#
# Scope B (TODO) — replace this with an actual CHOP chain (Math + Limit + Lag
# nodes inside the surface COMP) for multi-source modulation and filtering.

def _shape_norm(norm_src: str, curve: str) -> str:
    """Return an expression that applies `curve` to a 0..1-domain source."""
    if curve == 'exp':
        return f"({norm_src})**2"
    if curve == 'log':
        return f"({norm_src})**0.5"
    if curve == 'smooth':  # 3x^2 - 2x^3 (smoothstep)
        return f"(3*({norm_src})**2 - 2*({norm_src})**3)"
    return f"({norm_src})"


def _maybe_clamp(expr: str, clamp) -> str:
    """Wrap `expr` in `min(max(expr, lo), hi)` if clamp is a 2-element list."""
    if not clamp or not isinstance(clamp, (list, tuple)) or len(clamp) != 2:
        return expr
    lo, hi = clamp
    return f"min(max({expr}, {lo}), {hi})"


def _direct_expression(surface_path: str, name: str, bind: dict, lo, hi) -> str:
    """Build the TD expression for a direct (knob/slider) binding. If the
    binding has no shaping fields, the expression is the simple passthrough."""
    src = f"op('{surface_path}').par.{name}"
    pre_clamp  = bind.get('pre_clamp')
    post_clamp = bind.get('post_clamp')
    curve      = bind.get('curve', 'linear')

    # Fast path: no shaping → plain passthrough (TD evaluates faster).
    if not pre_clamp and not post_clamp and curve == 'linear':
        return src

    # Normalize the knob's value to 0..1 over its [lo, hi] range so curves
    # and pre_clamp operate in a consistent domain.
    span = (hi - lo) if hi != lo else 1.0
    norm = f"(({src}) - {lo}) / ({span})"
    norm = _maybe_clamp(norm, pre_clamp)
    shaped = _shape_norm(norm, curve)
    scaled = f"({shaped}) * ({span}) + ({lo})"
    return _maybe_clamp(scaled, post_clamp)


def _macro_expression(surface_path: str, macro_name: str, mb: dict) -> str:
    """Macro leg: macro knob (0..1) → optional pre_clamp → curve → scale to
    [from, to] → optional post_clamp. See module-level shaping comment."""
    src        = f"op('{surface_path}').par.{macro_name}"
    pre_clamp  = mb.get('pre_clamp')
    post_clamp = mb.get('post_clamp')
    curve      = mb.get('curve', 'linear')
    frm        = mb['from']
    to         = mb['to']

    norm   = _maybe_clamp(f"({src})", pre_clamp)
    shaped = _shape_norm(norm, curve)
    scaled = f"({shaped}) * ({to} - {frm}) + ({frm})"
    return _maybe_clamp(scaled, post_clamp)


def _bind_target(path: str, EXPRESSION, expr: str) -> None:
    """path = '<node_path>/<param_name>'. Switch target par to EXPRESSION mode."""
    node_path, _, par_name = path.rpartition('/')
    node = op(node_path)  # noqa: F821
    if node is None:
        debug(f"unicorner: target node not found: {node_path}")  # noqa: F821
        return
    par = getattr(node.par, par_name, None)
    if par is None:
        debug(f"unicorner: target par not found: {node_path}.{par_name}")  # noqa: F821
        return
    par.expr = expr
    par.mode = EXPRESSION


def _bind_target_noop(path: str) -> None:
    """Pulse: we don't expression-bind. Leaving this hook so the call site is
    explicit about the gap."""
    debug(f"unicorner: pulse binding for {path!r} skipped (v1)")  # noqa: F821


def _id_to_par_name(cid: str) -> str:
    """Convert spec id (snake_case) to a TD-legal custom-par name.

    TD rules (stricter than they look — bit by `ActiveToggle`):
      - first character uppercase
      - remaining characters must be LOWERCASE letters and digits only
      - cannot end in a digit (TD treats trailing digits as sequence index)
      - no underscores, hyphens, dots
    """
    cleaned = ''.join(c for c in (cid or '') if c.isalnum())
    if not cleaned:
        cleaned = 'Knob'
    name = cleaned[0].upper() + cleaned[1:].lower()
    if name[-1].isdigit():
        name += 'p'  # avoid trailing digit (sequence reserved)
    return name


# ---------------------------------------------------------------------------
# Public entry — orchestration
# ---------------------------------------------------------------------------

def run(scene_root: str,
        scene_parent: str,
        scene_id: str,
        user_prompt: str,
        history: list = None,
        current_spec: dict = None,
        scene_label: str = None,
        djay_path: str = None,
        model: str = DEFAULT_MODEL,
        comp = None) -> dict:
    """End-to-end: generate a new ControllerSpec and apply it to
    <scene_parent>/controller_surface. Returns the spec dict (so caller can
    send it back to the iPad).

    `comp` is the drop-in unicorner_controller COMP. Passed through so the
    API-key resolver can read its `Apikey` custom param as a fallback.

    `djay_path`, when provided, enables routings: passed through to
    `generate_spec` for catalog extraction, and the resolved CHOP path is
    threaded into `apply_spec` so routings can be wired up at apply time.
    """
    spec = generate_spec(
        scene_root=scene_root,
        scene_id=scene_id,
        user_prompt=user_prompt,
        history=history,
        current_spec=current_spec,
        scene_label=scene_label,
        djay_path=djay_path,
        model=model,
        comp=comp,
    )
    dj_catalog = extract_djay_catalog(djay_path) if djay_path else None
    dj_chop_path = dj_catalog.get('chop_path') if dj_catalog else None
    apply_spec(spec, scene_parent, dj_chop_path=dj_chop_path)
    return spec


# ---------------------------------------------------------------------------
# API-key resolution (three-tier: env var > config file > COMP param)
# ---------------------------------------------------------------------------

def resolve_api_key(comp = None) -> str:
    """Resolve the Anthropic API key with this precedence:

    1. `comp.par.Apikey` — the canonical place. Set it in the unicorner_controller
       COMP's parameter UI. The value saves into the .toe as plaintext; for
       projects you commit or share, use one of the escape hatches below.
    2. `ANTHROPIC_API_KEY` env var — escape hatch when you don't want the key
       in the .toe. Cleanest, but requires TD to inherit the env from a shell
       (Finder-launched TD won't).
    3. `<project.folder>/.unicorner_config.json` — second escape hatch. Lives
       on disk next to main.toe (gitignored). Survives Finder launches.

    Returns '' if none of the sources have a value.
    """
    if comp is not None and hasattr(comp.par, 'Apikey'):
        try:
            key = (comp.par.Apikey.eval() or '').strip()
        except Exception:
            key = ''
        if key:
            return key

    key = (os.environ.get('ANTHROPIC_API_KEY') or '').strip()
    if key:
        return key

    return _read_config_key()


def _config_path() -> str:
    try:
        folder = project.folder  # noqa: F821 — TD-injected global
    except Exception:
        folder = os.getcwd()
    return os.path.join(folder, CONFIG_FILENAME)


def _read_config_key() -> str:
    path = _config_path()
    if not os.path.isfile(path):
        return ''
    try:
        with open(path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
    except Exception as e:
        debug(f"unicorner: failed to read {path}: {e}")  # noqa: F821
        return ''
    return (cfg.get('anthropic_api_key') or '').strip()


def log_event(scene_id: str, record: dict) -> None:
    """Append one event record to the per-scene log file. Best-effort —
    swallows IO errors so a broken log doesn't break a generate.

    Record shape (see _apply_pending callers for full vocabulary):
      {ts, scene_id, prompt, outcome, raw_response, spec|alternatives|None,
       error|None, ms}
    """
    try:
        folder = project.folder  # noqa: F821 — TD-injected global
    except Exception:
        folder = os.getcwd()
    log_dir = os.path.join(folder, LOG_DIRNAME)
    try:
        os.makedirs(log_dir, exist_ok=True)
    except Exception:
        return
    safe_scene = ''.join(c if (c.isalnum() or c in '-_') else '_' for c in (scene_id or 'default'))
    path = os.path.join(log_dir, f'{safe_scene}.jsonl')
    payload = {'ts': _iso_ts(), 'scene_id': scene_id, **record}
    try:
        with open(path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(payload, default=str) + '\n')
    except Exception as e:
        try:
            debug(f"unicorner: log write failed: {e}")  # noqa: F821
        except Exception:
            pass


def _iso_ts() -> str:
    import datetime
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + 'Z'


def tail_log(scene_id: str, n: int = 10) -> list:
    """Convenience for poking at the log from TD's textport or MCP:

        op('/project1/unicorner_controller/generator').module.tail_log('container1', 20)
    """
    try:
        folder = project.folder  # noqa: F821
    except Exception:
        folder = os.getcwd()
    safe_scene = ''.join(c if (c.isalnum() or c in '-_') else '_' for c in (scene_id or 'default'))
    path = os.path.join(folder, LOG_DIRNAME, f'{safe_scene}.jsonl')
    if not os.path.isfile(path):
        return []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except Exception:
        return []
    out = []
    for line in lines[-n:]:
        try:
            out.append(json.loads(line))
        except Exception:
            out.append({'_raw': line.rstrip()})
    return out


def save_config_key(key: str) -> str:
    """Write `anthropic_api_key` into the gitignored config file next to
    main.toe. Returns the absolute path. The `Save Key` pulse on the
    unicorner_controller COMP calls this so users can set their key once
    without it ever landing in the .toe."""
    key = (key or '').strip()
    if not key:
        raise RuntimeError("save_config_key: empty key")
    path = _config_path()
    cfg = {}
    if os.path.isfile(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                cfg = json.load(f) or {}
        except Exception:
            cfg = {}
    cfg['anthropic_api_key'] = key
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2)
    try:
        os.chmod(path, 0o600)  # tighten perms on POSIX; harmless on Windows
    except Exception:
        pass
    return path


def _api_key_not_found_msg() -> str:
    try:
        folder = project.folder  # noqa: F821
    except Exception:
        folder = '<project folder>'
    return (
        "Anthropic API key not set. Open the unicorner_controller COMP and "
        "fill in its Apikey parameter (the value is saved into the .toe).\n\n"
        "If you don't want the key in the .toe, set one of these instead:\n"
        "  - ANTHROPIC_API_KEY env var in the shell that launches TouchDesigner\n"
        f"  - {folder}/{CONFIG_FILENAME}  with  {{\"anthropic_api_key\": \"sk-ant-...\"}}"
    )
