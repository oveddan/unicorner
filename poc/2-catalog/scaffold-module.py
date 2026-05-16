"""
POC 2 — Scaffold a sample "Layer B" module via MCP.

Adds `/project1/module_a` (a containerCOMP) with 5 typed custom parameters on a
'Controls' page, tagged `unicorner.layer-b` so the catalog extractor finds it.
Idempotent — re-running destroys and rebuilds the module.

Run by handing `SCAFFOLD_BODY` to `mcp__touchdesigner-stdio__execute_python_script`.

Note: TD's MCP `execute_python_script` sometimes drops the trailing return
value for longer multi-statement scripts. The side effects always land; just
follow up with a small read script to verify state if you need confirmation.
"""

SCAFFOLD_BODY = r'''
PARENT = '/project1'
NAME   = 'module_a'
TAG    = 'unicorner.layer-b'

parent = op(PARENT)
existing = parent.op(NAME)
if existing is not None:
    existing.destroy()

mod = parent.create('containerCOMP', NAME)
mod.nodeX = -300
mod.nodeY = 100
mod.tags.add(TAG)

page = mod.appendCustomPage('Controls')

p = page.appendFloat('Intensity', label='Intensity')[0]
p.default = 0.5;   p.normMin = 0.0; p.normMax = 1.0;   p.clampMin = True; p.clampMax = True; p.val = 0.5

p = page.appendFloat('Colorhue',  label='Color Hue')[0]
p.default = 200.0; p.normMin = 0.0; p.normMax = 360.0; p.clampMin = True; p.clampMax = True; p.val = 200.0

p = page.appendFloat('Scale',     label='Scale')[0]
p.default = 1.0;   p.normMin = 0.1; p.normMax = 10.0;  p.clampMin = True; p.clampMax = True; p.val = 1.0

p = page.appendFloat('Speed',     label='Speed')[0]
p.default = 1.0;   p.normMin = 0.0; p.normMax = 2.0;   p.clampMin = True; p.clampMax = True; p.val = 1.0

p = page.appendToggle('Active',   label='Active')[0]
p.default = True;  p.val = True

# Cheap verification — read back what we wrote
[(pr.name, pr.style, pr.default, pr.normMin, pr.normMax) for pr in mod.customPars]
'''

if __name__ == '__main__':
    print(SCAFFOLD_BODY)
