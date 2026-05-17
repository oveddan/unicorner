"""
POC 7 — Build/rebuild the drop-in unicorner_controller COMP.

Idempotent scaffold for `td/unicorner_controller.tox` — the single COMP a user
drags onto any TouchDesigner project to get an iPad-driven, AI-generated
controller surface.

What the COMP contains, post-scaffold:

    /project1/unicorner_controller            (baseCOMP, tagged 'unicorner.controller')
    │   custom pars (Setup page):  Apikey, Savekey, Keystatus
    │   custom pars (Unicorner):   Target, Scene, Prompt, Generate
    ├── webserver1                            (webserverDAT, port 9980, WS on)
    ├── callbacks                             (textDAT — controller_webserver_script.py)
    ├── generator                             (textDAT — unicorner_generator.py)
    ├── init                                  (textDAT — imports the generator module)
    └── parexec1                              (parameterExecuteDAT — Generate/Savekey/Scene)

Design note: the scaffold reads `td/modules/*.py` *inside TD's exec scope* (not
at render time), so the rendered body stays ~3KB instead of ballooning with
inlined string constants. TD has filesystem access — let it do the file reads.

How to use:

    1. Open `td/main.toe` in TouchDesigner.
    2. From Claude Code, run this script via
       `mcp__touchdesigner-stdio__execute_python_script` (paste the output of
       `render()`). It scaffolds the COMP and saves it as
       `td/unicorner_controller.tox` for future drag-and-drop reuse.
    3. After it runs, set Apikey on the COMP (Setup page), click Save Key,
       set Target/Scene if needed, and the iPad is ready.
"""

import json

# ----- Config --------------------------------------------------------------

DEFAULT_PARENT      = '/project1'
DEFAULT_COMP_NAME   = 'unicorner_controller'
DEFAULT_PORT        = 9980

# Paths to the Python modules whose contents get loaded into Text DATs at
# scaffold-time. Relative to the repo root, then resolved against
# project.folder (which is `td/`, where main.toe lives).
GENERATOR_PY_REPO_PATH        = 'modules/unicorner_generator.py'
WEBSERVER_SCRIPT_PY_REPO_PATH = 'modules/controller_webserver_script.py'
PAREXEC_CALLBACKS_PY_REPO_PATH = 'modules/parexec_callbacks.py'

# .tox save path, resolved against project.folder. Empty string skips the save.
DEFAULT_TOX_SAVE_NAME = 'unicorner_controller.tox'


# ----- Body fed to TD ------------------------------------------------------
#
# The body is small (~3KB). It does the heavy file reads itself, inside TD,
# at execution time. project.folder == "<repo>/td/" so all the path math is
# relative to that. No `debug()` calls in the outer scope — TD only injects
# `debug` inside functions; at the module-level exec scope it's not bound.

SCAFFOLD_BODY = r'''
import os

PARENT_PATH          = __PARENT_PATH__
COMP_NAME            = __COMP_NAME__
PORT                 = __PORT__
GENERATOR_PY         = __GENERATOR_PY__
WEBSERVER_SCRIPT_PY  = __WEBSERVER_SCRIPT_PY__
PAREXEC_CALLBACKS_PY = __PAREXEC_CALLBACKS_PY__
TOX_SAVE_NAME        = __TOX_SAVE_NAME__

td_folder = project.folder  # e.g. /Users/.../unicorner/td

def _read(rel_path):
    abs_path = os.path.join(td_folder, rel_path)
    with open(abs_path, 'r', encoding='utf-8') as f:
        return f.read()

generator_src        = _read(GENERATOR_PY)
webserver_script_src = _read(WEBSERVER_SCRIPT_PY)
parexec_callbacks_src = _read(PAREXEC_CALLBACKS_PY)

parent_op = op(PARENT_PATH)
if parent_op is None:
    raise RuntimeError(f"scaffold: parent COMP {PARENT_PATH!r} not found")

# Tear down existing COMP so this script is idempotent.
existing = parent_op.op(COMP_NAME)
if existing is not None:
    existing.destroy()

comp = parent_op.create('baseCOMP', COMP_NAME)
comp.nodeX = -800
comp.nodeY = 500
comp.tags.add('unicorner.controller')

# ----- Setup page: Apikey -> Savekey -> Keystatus ------------------------
setup_page = comp.appendCustomPage('Setup')

p_apikey = setup_page.appendStr('Apikey', label='Anthropic API key (paste here)')[0]
p_apikey.default = ''
try:
    p_apikey.help = (
        "Paste your key here, then click 'Save Key'. The key is written to "
        "td/.unicorner_config.json (gitignored) and this field is cleared so "
        "the key never lands in the .toe."
    )
except Exception:
    pass

p_savekey = setup_page.appendPulse('Savekey', label='Save Key')[0]
try:
    p_savekey.help = "Write Apikey to td/.unicorner_config.json and clear the field."
except Exception:
    pass

p_keystatus = setup_page.appendStr('Keystatus', label='Status')[0]
p_keystatus.default = ''
try:
    p_keystatus.readOnly = True
except Exception:
    pass

# ----- Controller bundle (Distpath) — same-port HTTP serving -------------
# When this resolves to a folder containing index.html, the COMP's Web
# Server DAT serves the bundle directly on the WebSocket port (9980) —
# no Vite dev server needed. Released zips ship a sibling folder named
# unicorner_controller_dist next to the .tox, so the default Just Works.
try:
    p_distpath = setup_page.appendFolder('Distpath', label='Controller dist folder')[0]
except AttributeError:
    p_distpath = setup_page.appendStr('Distpath', label='Controller dist folder')[0]
p_distpath.default = './unicorner_controller_dist'
p_distpath.val     = './unicorner_controller_dist'
try:
    p_distpath.help = (
        "Folder containing the built controller (Vite dist/, must have "
        "index.html). Resolved against the .toe's location via tdu.expandPath. "
        "When set and valid, the COMP serves the controller on the same port "
        "as the WebSocket — clear this field to fall back to external Vite "
        "(see Uiport)."
    )
except Exception:
    pass

# ----- Controller URL block (also on Setup page) -------------------------
# Uiport is the *dev-mode* fallback — only consulted when Distpath is empty
# or doesn't resolve. Defaults to Vite's dev server port.
p_uiport = setup_page.appendInt('Uiport', label='Controller UI port (dev / Vite)')[0]
p_uiport.default = 5173
p_uiport.val     = 5173
p_uiport.normMin = 1; p_uiport.normMax = 65535
p_uiport.min     = 1; p_uiport.max     = 65535

p_url = setup_page.appendStr('Url', label='Controller URL')[0]
p_url.default = ''
try:
    p_url.readOnly = True
except Exception:
    pass

p_openbrowser = setup_page.appendPulse('Openbrowser', label='Open in browser')[0]
p_refreshurl  = setup_page.appendPulse('Refreshurl',  label='Refresh URL')[0]

# ----- Unicorner page: Target/Scene/Prompt/Generate ----------------------
page = comp.appendCustomPage('Unicorner')

p_target = page.appendStr('Target', label='Target COMP path')[0]
p_target.default = '/project1'
p_target.val     = '/project1'

p_scene = page.appendStr('Scene', label='Active scene path')[0]
p_scene.default = ''

# Optional: path to a DJay Pro CHOP (or a COMP whose first null/out CHOP carries
# the DJay channels). When set, the AI generator can wire `routings` from
# DJay signals (beat, bar, bpm, stem amplitudes) directly into scene params.
# Leave empty to disable routings entirely — generator falls back to today's
# controls-only behavior.
p_djaypath = page.appendStr('Djaypath', label='DJay Pro CHOP/COMP path (optional)')[0]
p_djaypath.default = ''
try:
    p_djaypath.help = (
        "Absolute TD path to a CHOP exposing DJay Pro channels (or a COMP "
        "containing one). E.g. /project1/djayPro or /project1/djayPro/out1. "
        "When set, the generator can output `routings` that wire DJay signals "
        "into scene parameters via expressions, BPM-synced LFOs, and bar "
        "triggers. Leave empty to disable music-reactive routings."
    )
except Exception:
    pass

p_prompt = page.appendStr('Prompt', label='Prompt (TD-native)')[0]
p_prompt.default = ''

p_generate = page.appendPulse('Generate', label='Generate now')[0]

# ----- Generator + init Text DATs ----------------------------------------
generator_dat = comp.create('textDAT', 'generator')
generator_dat.text = generator_src
generator_dat.nodeX = -300; generator_dat.nodeY = 0

init_dat = comp.create('textDAT', 'init')
init_dat.text = (
    "# Expose the generator module to the webserver callbacks DAT.\n"
    "globals()['unicorner_generator'] = parent().op('generator').module\n"
)
init_dat.nodeX = -300; init_dat.nodeY = -100

# ----- Web Server DAT + callbacks ----------------------------------------
ws = comp.create('webserverDAT', 'webserver1')
ws.par.port = PORT
if hasattr(ws.par, 'websocket'):
    ws.par.websocket = True
for attr in ('active', 'Active'):
    if hasattr(ws.par, attr):
        setattr(ws.par, attr, True)
        break
ws.nodeX = 0; ws.nodeY = 0

callbacks_dat = comp.create('textDAT', 'callbacks')
callbacks_dat.text = webserver_script_src
callbacks_dat.nodeX = 0; callbacks_dat.nodeY = -100

if hasattr(ws.par, 'callbacks'):
    ws.par.callbacks = callbacks_dat.path

# ----- Parameter Execute DAT for Scene / Generate / Savekey --------------
parexec = comp.create('parameterexecuteDAT', 'parexec1')
parexec.nodeX = 200; parexec.nodeY = -100
if hasattr(parexec.par, 'op'):
    parexec.par.op = comp.path
for attr_name, attr_val in (
    ('pars',       'Scene Generate Savekey Openbrowser Refreshurl'),
    ('parameters', 'Scene Generate Savekey Openbrowser Refreshurl'),
):
    if hasattr(parexec.par, attr_name):
        setattr(parexec.par, attr_name, attr_val); break
for attr in ('valuechange', 'Valuechange'):
    if hasattr(parexec.par, attr):
        setattr(parexec.par, attr, True); break
for attr in ('onpulse', 'Onpulse'):
    if hasattr(parexec.par, attr):
        setattr(parexec.par, attr, True); break

parexec.text = parexec_callbacks_src

# ----- Save .tox so future drag-and-drop is one step ---------------------
# project.folder == td/, so the .tox lives alongside main.toe.
if TOX_SAVE_NAME:
    save_path = os.path.join(td_folder, TOX_SAVE_NAME)
    try:
        comp.save(save_path)
        print(f"unicorner_controller: saved {save_path}")
    except Exception as e:
        print(f"unicorner_controller: .tox save FAILED: {e}")

print(f"unicorner_controller: ready at {comp.path}")
[(p.name, p.label) for p in comp.customPars]
'''


def render(parent_path: str = None,
           comp_name: str = None,
           port: int = None,
           tox_save_name: str = None) -> str:
    """Render the scaffold body with all substitutions filled in. The result
    is a small (~3KB) Python string ready to pass to
    `mcp__touchdesigner-stdio__execute_python_script`."""
    body = SCAFFOLD_BODY
    body = body.replace('__PARENT_PATH__',         json.dumps(parent_path   or DEFAULT_PARENT))
    body = body.replace('__COMP_NAME__',           json.dumps(comp_name     or DEFAULT_COMP_NAME))
    body = body.replace('__PORT__',                json.dumps(port          or DEFAULT_PORT))
    body = body.replace('__GENERATOR_PY__',         json.dumps(GENERATOR_PY_REPO_PATH))
    body = body.replace('__WEBSERVER_SCRIPT_PY__',  json.dumps(WEBSERVER_SCRIPT_PY_REPO_PATH))
    body = body.replace('__PAREXEC_CALLBACKS_PY__', json.dumps(PAREXEC_CALLBACKS_PY_REPO_PATH))
    body = body.replace('__TOX_SAVE_NAME__',       json.dumps(
        tox_save_name if tox_save_name is not None else DEFAULT_TOX_SAVE_NAME))
    return body


if __name__ == '__main__':
    print(render())
