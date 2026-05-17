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
# Prompt assembly + Anthropic call
# ---------------------------------------------------------------------------

# System prompt + few-shot. Mirrors ai/prompts/controller-from-catalog.md so
# the generator works without filesystem access (TD's CWD is unpredictable).
# When you edit the .md file, copy the system-prompt body into SYSTEM_PROMPT.
SYSTEM_PROMPT = """You design opinionated control surfaces for a DJ performing visuals in TouchDesigner. The DJ already mixes music; you give them a small, well-shaped set of controls (<=12) that they can play **without thinking**.

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
5. **Match widget to param type:**
   - `float` -> `knob` or `slider`
   - `bool` -> `toggle`
   - `pulse` -> `button`
   - Multi-param sweep -> `macro` (one input drives 2+ params at once)
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
- Labels are short. "Intensity" not "Visual Intensity Multiplier"."""


def build_messages(catalog: dict,
                   user_prompt: str,
                   history: list = None,
                   current_spec: dict = None,
                   scene_summary: str = None) -> list:
    """Pure function — no TD calls. Builds the messages list that will be
    POSTed to Anthropic. Split out from generate_spec() so the callbacks
    layer can extract the catalog on the main thread, then build messages
    here, then ship the result to a worker thread for the HTTP call.

    `scene_summary`, if provided, is a plain-language description of the
    scene (typically authored by the DJ via the "Re-scan scene" button and
    optionally hand-edited). It's injected before the catalog as shared
    context so the model writes a spec against the DJ's mental picture of
    the scene rather than just the raw param dump.
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
        '"rationale":"…","controls":[…],"layout":[…]}',
        "",
        "If the request admits multiple good interpretations (2–3 meaningfully different options):",
        '  {"alternatives":[{"id":"a","label":"<short>","description":"<one line>","spec":{schema_version,scene_id,rationale,controls,layout}}, ...]}',
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


def parse_response(response_text: str, catalog: dict):
    """Pure function — no TD calls. Parses the model output into one of two
    shapes:

      - single-spec mode: returns `{'mode': 'spec', 'spec': <ControllerSpec>}`
      - alternatives mode: returns `{'mode': 'alternatives', 'alternatives': [...]}`

    Each alternative is `{'id', 'label', 'description', 'spec': <ControllerSpec>}`.
    Each spec is normalized + validated; an invalid alternative is dropped
    (with a debug print) rather than failing the whole batch.
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
    return {'mode': 'spec', 'spec': spec}


_LAST_RESPONSE: str = ''  # last raw model response


def generate_spec(scene_root: str,
                  scene_id: str,
                  user_prompt: str,
                  history: list = None,
                  current_spec: dict = None,
                  scene_label: str = None,
                  scene_summary: str = None,
                  model: str = DEFAULT_MODEL,
                  comp = None) -> dict:
    """Synchronous end-to-end: catalog → API → validated spec.

    Kept for one-shot scripted use (e.g. testing from MCP). The runtime
    path through `controller_webserver_script._handle_generate` does this
    in pieces across a worker thread to avoid freezing TD's cook.
    """
    catalog  = extract_catalog(scene_root, scene_id, scene_label)
    messages = build_messages(catalog, user_prompt, history, current_spec, scene_summary)
    response_text = _call_anthropic(messages, model=model, api_key=resolve_api_key(comp))
    parsed = parse_response(response_text, catalog)
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


# ---------------------------------------------------------------------------
# Spec → scaffold (apply the ControllerSpec to a controller_surface COMP)
# ---------------------------------------------------------------------------

CONTROLLER_SURFACE_TAG = 'unicorner.controller-surface'
SURFACE_NAME           = 'controller_surface'
SURFACE_PAGE           = 'Surface'


def apply_spec(spec: dict, scene_parent: str) -> str:
    """Build/rebuild `<scene_parent>/controller_surface` from the spec. Returns
    the surface path so the caller can broadcast it."""
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

    for ctrl in spec['controls']:
        _apply_control(ctrl, page, surface, surface_path, EXPRESSION)

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
        model: str = DEFAULT_MODEL,
        comp = None) -> dict:
    """End-to-end: generate a new ControllerSpec and apply it to
    <scene_parent>/controller_surface. Returns the spec dict (so caller can
    send it back to the iPad).

    `comp` is the drop-in unicorner_controller COMP. Passed through so the
    API-key resolver can read its `Apikey` custom param as a fallback."""
    spec = generate_spec(
        scene_root=scene_root,
        scene_id=scene_id,
        user_prompt=user_prompt,
        history=history,
        current_spec=current_spec,
        scene_label=scene_label,
        model=model,
        comp=comp,
    )
    apply_spec(spec, scene_parent)
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
