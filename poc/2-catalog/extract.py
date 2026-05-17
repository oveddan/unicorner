"""
POC 2 — Extract a parameter catalog from a live TD project, via MCP or in-process.

Walks a target COMP subtree looking for COMPs tagged `unicorner.layer-b` and reads
their custom parameters. Emits a dict that conforms to
`schemas/parameter-catalog.schema.json`.

Two ways to use it:

1. **Via MCP** — hand the body of `EXTRACTOR_BODY` (or the output of `render()`)
   to the `mcp__touchdesigner-stdio__execute_python_script` tool. The trailing
   expression is the catalog dict — MCP returns it verbatim.

2. **In-process inside TD** — `import poc.2-catalog.extract` doesn't work
   (numeric prefix isn't a valid Python identifier), so the in-TD generator
   inlines the same walker in `td/modules/unicorner_generator.py`. Keep the
   two in sync: when you change the logic here, mirror the change there.

Why the unusual shape:
- Defs at the top level work, but if the trailing expression of the script is
  too far from a simple dict literal, MCP sometimes drops the return value.
  Keeping the final line `catalog` (a bare reference) avoids that.
- `findChildren(tags=[TAG])` returns all descendants with the tag — we don't
  pass `type=COMP` because the TD-module constants (`COMP`, `baseCOMP`, etc.)
  aren't always available in the MCP exec scope.
"""

# ----- Config (substituted into the body when calling `render()`) ----------

DEFAULTS = {
    'scene_root': '/project1',          # Root of the walk. The drop-in COMP's `Target` param sets this.
    'scene_id':   'a',                  # Scene id stamped into the catalog.
    'scene_label': 'Test scene A',
    'tag':        'unicorner.layer-b',  # Tag used to identify Layer B module COMPs.
}

# ----- Body fed to TD ------------------------------------------------------

EXTRACTOR_BODY = r'''
SCENE_ROOT  = __SCENE_ROOT__
SCENE_ID    = __SCENE_ID__
SCENE_LABEL = __SCENE_LABEL__
TAG         = __TAG__

# TD parameter style → catalog `type`. Unknown styles fall through and the
# param is skipped (we don't want to surface menu/string params yet).
STYLE_MAP = {
    'Float':  'float',
    'Int':    'int',
    'Toggle': 'bool',
    'Pulse':  'pulse',
}

# Param-name → semantic tag overrides. Anything not listed uses par.name.lower().
SEMANTIC_OVERRIDES = {
    'Colorhue': 'hue',
    'Active':   'toggle',
}

def param_to_entry(comp, par):
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
    }
    if type_ in ('float', 'int'):
        entry['min'] = par.normMin
        entry['max'] = par.normMax
    return entry

root = op(SCENE_ROOT)
modules = []
if root is not None:
    for comp in root.findChildren(tags=[TAG]):
        pars = []
        for par in comp.customPars:
            e = param_to_entry(comp, par)
            if e is not None:
                pars.append(e)
        modules.append({
            'id':         comp.name,
            'path':       comp.path,
            'label':      comp.name.replace('_', ' ').title(),
            'parameters': pars,
        })
modules.sort(key=lambda m: m['id'])

catalog = {
    'schema_version': '0.1',
    'scene_id':       SCENE_ID,
    'scene_label':    SCENE_LABEL,
    'modules':        modules,
}
catalog
'''


def render(scene_root: str = None, scene_id: str = None, scene_label: str = None, tag: str = None) -> str:
    """Substitute the config into the body template and return the script ready
    to send to TD. All args fall back to `DEFAULTS`."""
    import json
    cfg = {**DEFAULTS}
    if scene_root  is not None: cfg['scene_root']  = scene_root
    if scene_id    is not None: cfg['scene_id']    = scene_id
    if scene_label is not None: cfg['scene_label'] = scene_label
    if tag         is not None: cfg['tag']         = tag
    body = EXTRACTOR_BODY
    body = body.replace('__SCENE_ROOT__',  json.dumps(cfg['scene_root']))
    body = body.replace('__SCENE_ID__',    json.dumps(cfg['scene_id']))
    body = body.replace('__SCENE_LABEL__', json.dumps(cfg['scene_label']))
    body = body.replace('__TAG__',         json.dumps(cfg['tag']))
    return body


if __name__ == '__main__':
    print(render())
