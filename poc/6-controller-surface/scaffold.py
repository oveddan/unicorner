"""
POC 6 — Build/rebuild a controller-surface COMP and its parameter bindings.

The controller-surface COMP is the single contract between the iPad React app
and TouchDesigner. Its custom params are the controls the iPad renders, and
each one is expression-bound to a target param somewhere downstream —
typically a Layer B module's custom param (clean Layer B contract), occasionally
an internal render node (quick path).

**Parent path is configurable.** Each scene COMP gets its own surface as a
child, e.g. `/project1/sceneA/controller_surface`, `/project1/sceneB/controller_surface`.
The drop-in `unicorner_controller.tox` selects which child surface the iPad
talks to via its `Scene` parameter.

Edit `PARAMS` and `BINDINGS` below to match your scene, then hand the rendered
body to `mcp__touchdesigner-stdio__execute_python_script`. Idempotent — tears
down the existing COMP at `<parent>/<surface_name>` and rebuilds.

This script does NOT modify Layer B modules. If you want a new knob to drive
a Layer B custom param that doesn't exist yet, add the custom param on the
Layer B side first (and wire its internal nodes to it) — see the
`add_layer_b_param` snippet in `add_layer_b_param.py` for the pattern.
"""

# ----- Config --------------------------------------------------------------
# Knobs to expose on the iPad. Each entry: (name, label, default, lo, hi).
# 'name' must follow TD's rules: starts with uppercase, rest lowercase + digits,
# and cannot end in a digit. 'label' is what the iPad shows.

PARAMS = [
    # ('Name',       'Label',       default, lo,    hi),
    ('Phase',        'Phase',       0.0,     0.0,   1.0),
    ('Period',       'Period',      1.0,     0.1,   4.0),
    ('Pulserate',    'Pulse Rate',  1.0,     0.25,  8.0),
    ('Decay',        'Decay',       0.5,     0.05,  2.0),
]

# Toggles. Each entry: (name, label, default_bool).
TOGGLES = [
    # ('Active', 'Active', True),
]

# Bindings: each surface param drives one target via a parameter expression.
# Each entry: (surface_param_name, target_node_path, target_par_name).
# The target par switches to ParMode.EXPRESSION reading from the surface.

BINDINGS = [
    ('Phase',     '/project1/container1/constant1', 'value0'),
    ('Period',    '/project1/container1',           'Period'),
    ('Pulserate', '/project1/container1',           'Pulserate'),
    ('Decay',     '/project1/container1',           'Decay'),
]

# Default parent for the surface COMP. The drop-in .tox overrides this per-scene.
DEFAULT_PARENT = '/project1'
DEFAULT_SURFACE_NAME = 'controller_surface'

# ----- Body fed to TD ------------------------------------------------------

SCAFFOLD_BODY = r'''
PARENT_PATH   = __PARENT_PATH__
SURFACE_NAME  = __SURFACE_NAME__
TAG           = 'unicorner.controller-surface'
PAGE_NAME     = 'Surface'

PARAMS   = __PARAMS__
TOGGLES  = __TOGGLES__
BINDINGS = __BINDINGS__

SURFACE_PATH = f'{PARENT_PATH}/{SURFACE_NAME}'

parent = op(PARENT_PATH)
if parent is None:
    raise RuntimeError(f"scaffold: parent COMP not found at {PARENT_PATH!r}")

existing = parent.op(SURFACE_NAME)

# Clear stale expressions that pointed at the old surface, so destroying it
# doesn't leave dangling expression references that fire on every cook.
if existing is not None:
    CONSTANT = type(parent.par.helpdat.mode).CONSTANT  # any par; mode enum
    for surface_param_name, target_node, target_par in BINDINGS:
        node = op(target_node)
        if node is None: continue
        par = getattr(node.par, target_par, None)
        if par is None: continue
        if "EXPRESSION" in str(par.mode) and SURFACE_PATH in (par.expr or ''):
            par.expr = ''
            par.mode = CONSTANT
    existing.destroy()

surface = parent.create('baseCOMP', SURFACE_NAME)
surface.nodeX = -600
surface.nodeY = 300
surface.tags.add(TAG)
page = surface.appendCustomPage(PAGE_NAME)

for name, label, default, lo, hi in PARAMS:
    p = page.appendFloat(name, label=label)[0]
    p.default = default
    p.min = lo; p.max = hi
    p.normMin = lo; p.normMax = hi
    p.clampMin = True; p.clampMax = True
    p.val = default

for name, label, default in TOGGLES:
    p = page.appendToggle(name, label=label)[0]
    p.default = default
    p.val = default

# Find a ParMode.EXPRESSION enum value to use. Any existing par will do.
EXPRESSION = type(surface.par.helpdat.mode).EXPRESSION

for surface_param_name, target_node, target_par in BINDINGS:
    node = op(target_node)
    if node is None:
        debug(f"scaffold: target node not found: {target_node}")
        continue
    par = getattr(node.par, target_par, None)
    if par is None:
        debug(f"scaffold: target par not found: {target_node}.{target_par}")
        continue
    par.expr = f"op('{SURFACE_PATH}').par.{surface_param_name}"
    par.mode = EXPRESSION

[(p.name, p.label, p.default, p.normMin, p.normMax) for p in surface.customPars]
'''


def render(parent_path: str = None,
           surface_name: str = None,
           params: list = None,
           toggles: list = None,
           bindings: list = None) -> str:
    """Substitute Python literals into the body template and return the full
    script ready to send to TD. Use this when calling via MCP."""
    import json
    body = SCAFFOLD_BODY
    body = body.replace('__PARENT_PATH__',  json.dumps(parent_path  or DEFAULT_PARENT))
    body = body.replace('__SURFACE_NAME__', json.dumps(surface_name or DEFAULT_SURFACE_NAME))
    body = body.replace('__PARAMS__',       json.dumps(params   if params   is not None else PARAMS))
    body = body.replace('__TOGGLES__',      json.dumps(toggles  if toggles  is not None else TOGGLES))
    body = body.replace('__BINDINGS__',     json.dumps(bindings if bindings is not None else BINDINGS))
    return body


if __name__ == '__main__':
    print(render())
