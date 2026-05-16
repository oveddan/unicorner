"""
POC 6 — Inspect the current TD scene and flag Layer B contract gaps.

Three reports:

1. controller_surface state — what custom params it has, what they bind to.
2. Expression bindings inside a Layer B module — which internal nodes
   expression-reference each declared custom param. Empty list = the param
   is declared but unused (stub-ware), and exposing it on the iPad won't
   move the visual.
3. Render-driving params — internal nodes whose params have non-trivial
   expressions or non-default values. Candidates to wrap into Layer B
   custom params + iPad knobs.

Run by handing `BODY` to `mcp__touchdesigner-stdio__execute_python_script`.
Edit `LAYER_B_MODULE_PATHS` at the top if your scene's modules live elsewhere.
"""

SURFACE_PATH         = '/project1/controller_surface'
LAYER_B_MODULE_PATHS = ['/project1/container1', '/project1/module_a']


BODY = f'''
import json

SURFACE_PATH         = {SURFACE_PATH!r}
LAYER_B_MODULE_PATHS = {LAYER_B_MODULE_PATHS!r}


def expressions_in(comp_path):
    """All expression-bound params on the comp's direct children."""
    out = []
    comp = op(comp_path)
    if comp is None:
        return out
    for child in comp.children:
        for p in child.pars():
            try:
                mode_str = str(p.mode)
            except Exception:
                continue
            if "EXPRESSION" in mode_str and p.expr:
                out.append({{
                    "node": child.path,
                    "par":  p.name,
                    "expr": p.expr,
                }})
    return out


def controller_surface_report():
    surface = op(SURFACE_PATH)
    if surface is None:
        return {{"present": False}}
    params = []
    for p in surface.customPars:
        params.append({{
            "name":  p.name,
            "label": p.label,
            "style": p.style,
            "normMin": getattr(p, 'normMin', None),
            "normMax": getattr(p, 'normMax', None),
            "default": p.default,
        }})
    return {{"present": True, "path": surface.path, "params": params}}


def layer_b_report(module_path):
    mod = op(module_path)
    if mod is None:
        return {{"present": False, "path": module_path}}
    children = list(mod.children)
    internal_exprs = expressions_in(module_path)
    declared = []
    for p in mod.customPars:
        # An "active" param is one referenced in at least one internal expression
        # OR it's bound to the controller_surface from the outside (expression
        # mode with a controller_surface reference). We only check the former
        # here — Layer B internal consumption.
        referenced_by = [
            e for e in internal_exprs
            if (f"parent().par.{{p.name}}" in (e['expr'] or '')) or (f".par.{{p.name}}" in (e['expr'] or ''))
        ]
        declared.append({{
            "name": p.name,
            "style": p.style,
            "internal_consumers": [(e['node'], e['par']) for e in referenced_by],
            "is_stub": len(referenced_by) == 0,
        }})
    return {{
        "present": True,
        "path": module_path,
        "child_count": len(children),
        "declared_params": declared,
        "internal_expressions": internal_exprs,
    }}


report = {{
    "controller_surface": controller_surface_report(),
    "layer_b": [layer_b_report(p) for p in LAYER_B_MODULE_PATHS],
}}

print(json.dumps(report, indent=2, default=str))
"OK"
'''


if __name__ == '__main__':
    print(BODY)
