"""
POC 6 — Add a custom param to a Layer B module + wire one of its internal
render nodes to read from it.

This is the "clean way" to add a new iPad knob: declare it on the Layer B
module (so the module stays self-contained), then have controller_surface
bind to that module's custom param.

Edit `MODULE_PATH`, `NEW_PARAM`, and `WIRE_TO` below, then hand `BODY` to
`mcp__touchdesigner-stdio__execute_python_script`.

After this runs, add a matching entry in `scaffold.py`'s PARAMS + BINDINGS
and re-run scaffold.py to expose the knob on the iPad.

Idempotent — adds the param only if missing; updates the expression on the
wire target every time.
"""

MODULE_PATH = '/project1/container1'

# New custom param on the Layer B module. Type currently float-only.
# 'name' must start with uppercase, rest lowercase + digits.
NEW_PARAM = {
    'name':    'Pulserate',
    'label':   'Pulse Rate',
    'default': 1.0,
    'lo':      0.25,
    'hi':      8.0,
}

# Internal render node + par that should read from the new Layer B param.
# Use 'parent().par.<Name>' so the module stays portable.
WIRE_TO = {
    'node_path': '/project1/container1/lfo1',
    'par_name':  'frequency',
}


BODY = f'''
MODULE_PATH = {MODULE_PATH!r}
NEW_PARAM   = {NEW_PARAM!r}
WIRE_TO     = {WIRE_TO!r}

mod = op(MODULE_PATH)
if mod is None:
    raise RuntimeError(f"module not found: {{MODULE_PATH!r}}")

existing_names = {{p.name for p in mod.customPars}}
pages = {{pg.name: pg for pg in mod.customPages}}
page = pages.get('Custom') or mod.appendCustomPage('Custom')

if NEW_PARAM['name'] not in existing_names:
    p = page.appendFloat(NEW_PARAM['name'], label=NEW_PARAM['label'])[0]
    p.default = NEW_PARAM['default']
    p.min = NEW_PARAM['lo'];     p.max = NEW_PARAM['hi']
    p.normMin = NEW_PARAM['lo']; p.normMax = NEW_PARAM['hi']
    p.clampMin = True; p.clampMax = True
    p.val = NEW_PARAM['default']

target_node = op(WIRE_TO['node_path'])
if target_node is None:
    raise RuntimeError(f"wire target node not found: {{WIRE_TO['node_path']!r}}")
target_par = getattr(target_node.par, WIRE_TO['par_name'], None)
if target_par is None:
    raise RuntimeError(f"wire target par not found: {{WIRE_TO['node_path']}}.{{WIRE_TO['par_name']}}")

EXPRESSION = type(target_par.mode).EXPRESSION
target_par.expr = f"parent().par.{{NEW_PARAM['name']}}"
target_par.mode = EXPRESSION

{{
    "module":     MODULE_PATH,
    "added_param": NEW_PARAM['name'] not in existing_names,
    "param_now_exists": NEW_PARAM['name'] in {{p.name for p in mod.customPars}},
    "wire_target": f"{{WIRE_TO['node_path']}}.{{WIRE_TO['par_name']}}",
    "wire_expr":   target_par.expr,
}}
'''


if __name__ == '__main__':
    print(BODY)
