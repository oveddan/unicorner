"""
POC 2 — Extract a parameter catalog from a live TD project, via MCP.

Walks /project1 looking for COMPs tagged `unicorner.layer-b` and reads their
custom parameters. Emits a dict that conforms to
`schemas/parameter-catalog.schema.json`.

Run by handing the body of `EXTRACTOR_BODY` to the
`mcp__touchdesigner-stdio__execute_python_script` tool. The last expression is
the catalog dict — MCP returns it verbatim.

Why the unusual shape:
- Defs at the top level work, but if the trailing expression of the script is
  too far from a simple dict literal, MCP sometimes drops the return value.
  Keeping the final line `catalog` (a bare reference) avoids that.
- `findChildren(tags=[TAG])` returns all descendants with the tag — we don't
  pass `type=COMP` because the TD-module constants (`COMP`, `baseCOMP`, etc.)
  aren't always available in the MCP exec scope.
"""

EXTRACTOR_BODY = r'''
SCENE_ROOT = '/project1'
SCENE_ID   = 'a'
TAG        = 'unicorner.layer-b'

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
    'scene_label':    'Test scene A',
    'modules':        modules,
}
catalog
'''

if __name__ == '__main__':
    print(EXTRACTOR_BODY)
