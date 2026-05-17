"""
controller_webserver_script — Web Server DAT callbacks for the drop-in
unicorner_controller COMP.

Wraps the existing POC 0 protocol (`{type:"set"}` knob moves, `{type:"schema"}`
on connect) with the new generator + scene-swap protocol:

    iPad -> TD:  {type:"generate", prompt, scene, history}
    TD -> iPad:  {type:"thinking"}
                 {type:"spec", scene, spec}
                 {type:"error", msg}
                 {type:"scene_changed", scene, spec}

The generator itself lives in `unicorner_generator.py`. This script is just
glue between the Web Server DAT and that module.

Paste this into the Web Server DAT callbacks DAT inside the drop-in COMP, or
import it from a Text DAT — both work.

The COMP's custom params are read via `parent()` so this script doesn't care
where it's nested:

    parent().par.Target  — root COMP whose subtree to catalog
    parent().par.Scene   — active scene COMP (child of Target)
"""

import json
import socket
import threading
import traceback
import webbrowser


def _generator():
    """Resolve the generator module via the sibling Text DAT inside the
    drop-in COMP. `import unicorner_generator` doesn't work because td/modules
    isn't on sys.path; the drop-in pattern is to bundle the module as a Text
    DAT and load it via `.module` so the .tox stays self-contained.

    Returns None if the COMP layout is wrong (caller falls back to a useful
    error message)."""
    try:
        return parent().op('generator').module  # noqa: F821 — TD globals
    except Exception:
        return None


# Last spec applied per scene_id. Used when a fresh client connects so we can
# replay the current state without recomputing. Resets on TD reload.
_last_spec_by_scene: dict = {}

# Pending response from a background generation thread. The worker thread
# fills this in, then schedules `_apply_pending` on the main thread via
# `run("...", delayFrames=1)`. We keep a per-scene queue so multiple in-flight
# generations don't clobber each other (race-prone but unlikely in practice —
# the iPad disables the Send button while thinking).
_pending: dict = {}  # scene_id -> {'response': str, 'catalog': dict, 'error': str|None}

# Per-scene alternatives waiting for the user to pick. Cleared when the user
# either picks one or starts a new generation.
_pending_alternatives: dict = {}  # scene_id -> [{'id', 'label', 'description', 'spec'}, ...]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _comp():
    """The drop-in COMP that hosts this web server. We expect the callbacks
    DAT to live two levels deep (DAT -> webserver -> drop-in COMP), so
    `parent(2)` would work, but using me.parent() chain is fragile. The
    cleanest robust path: walk up until we find the tag."""
    me_ = me  # noqa: F821 — TD-injected global
    cur = me_
    for _ in range(10):
        if cur is None:
            break
        if 'unicorner.controller' in (cur.tags or set()):
            return cur
        cur = cur.parent()
    return None


def _config() -> dict:
    """Read Target + Scene from the COMP's custom params."""
    comp = _comp()
    if comp is None:
        return {'target': '/project1', 'scene': '', 'scene_id': 'default'}
    target = comp.par.Target.eval() if hasattr(comp.par, 'Target') else '/project1'
    scene  = comp.par.Scene.eval()  if hasattr(comp.par, 'Scene')  else ''
    scene_root  = scene or target
    scene_id    = (scene.rsplit('/', 1)[-1] if scene else (target.rsplit('/', 1)[-1] or 'scene'))
    scene_parent = scene or target  # surface lives directly under the scene root
    return {
        'comp':         comp,
        'target':       target,
        'scene':        scene,
        'scene_root':   scene_root,
        'scene_parent': scene_parent,
        'scene_id':     scene_id,
    }


def _surface_path(cfg: dict) -> str:
    return f"{cfg['scene_parent']}/controller_surface"


def _send(ws_dat, client, obj) -> None:
    try:
        ws_dat.webSocketSendText(client, json.dumps(obj))
    except Exception as e:
        debug(f"controller_ws: send failed to {client}: {e}")  # noqa: F821


def _broadcast(ws_dat, obj) -> None:
    """Best-effort broadcast. Web Server DAT exposes connected clients via
    `connections` — names vary by TD version. Iterate defensively."""
    text = json.dumps(obj)
    sent_to = 0
    for attr in ('webSocketConnections', 'connections'):
        clients = getattr(ws_dat, attr, None)
        if clients is None:
            continue
        try:
            for client in list(clients):
                ws_dat.webSocketSendText(client, text)
                sent_to += 1
            break
        except Exception:
            continue
    if sent_to == 0:
        debug("controller_ws: no clients to broadcast to (or API mismatch)")  # noqa: F821


def _build_schema_message(cfg: dict) -> dict:
    """Backward-compat: emit POC-0 style {type:"schema", params:[...]} listing
    the current surface's custom params. Always includes the *expected*
    surface_path so the renderer can show users where the surface will live
    even before it's been generated."""
    surface_path = _surface_path(cfg)
    surface = op(surface_path)  # noqa: F821
    base = {
        'type':         'schema',
        'scene':        cfg['scene_id'],
        'surface_path': surface_path,
    }
    if surface is None:
        return {**base, 'params': []}
    params = []
    for par in surface.customPars:
        widget = _widget_type_for_par(par)
        if widget is None:
            continue
        entry = {'name': par.name, 'label': par.label, 'type': widget}
        if widget in ('float', 'int'):
            entry['min'] = par.normMin
            entry['max'] = par.normMax
        try:
            entry['default'] = par.default
        except Exception:
            pass
        params.append(entry)
    return {**base, 'params': params}


def _widget_type_for_par(par):
    style = par.style
    if style == 'Float':  return 'float'
    if style == 'Int':    return 'int'
    if style == 'Toggle': return 'bool'
    if style in ('Pulse', 'Momentary'): return 'pulse'
    return None


def _set_param(path: str, value) -> bool:
    node_path, _, param_name = path.rpartition('/')
    if not node_path or not param_name:
        debug(f"controller_ws: malformed path {path!r}")  # noqa: F821
        return False
    node = op(node_path)  # noqa: F821
    if node is None:
        debug(f"controller_ws: no node at {node_path!r}")  # noqa: F821
        return False
    par = getattr(node.par, param_name, None)
    if par is None:
        debug(f"controller_ws: no param {param_name!r} on {node_path!r}")  # noqa: F821
        return False
    par.val = value
    return True


# ---------------------------------------------------------------------------
# Message dispatch
# ---------------------------------------------------------------------------

def _handle_generate(ws_dat, client, msg) -> None:
    """Async generate. The pipeline splits into three phases:
      1. Main thread: extract catalog, build messages, resolve API key.
         These touch TD's op tree, so they must run on the cook thread.
      2. Worker thread: POST to Anthropic. Blocking I/O — the whole reason
         we're threading. While this runs, TD keeps cooking and the scene
         doesn't freeze.
      3. Main thread again (scheduled via `run(...)`): parse + validate +
         apply_spec + broadcast. apply_spec writes to the TD op tree.
    """
    gen = _generator()
    if gen is None:
        _send(ws_dat, client, {
            'type': 'error',
            'msg':  'generator Text DAT not found — re-run poc/7-drop-in/scaffold.py',
        })
        return

    cfg = _config()
    user_prompt = (msg.get('prompt') or '').strip()
    if not user_prompt:
        _send(ws_dat, client, {'type': 'error', 'msg': 'empty prompt'})
        return

    # Phase 1: main-thread prep (catalog walk + API key + messages).
    try:
        catalog  = gen.extract_catalog(cfg['scene_root'], cfg['scene_id'])
        api_key  = gen.resolve_api_key(cfg.get('comp'))
        messages = gen.build_messages(
            catalog,
            user_prompt,
            history=msg.get('history') or [],
            current_spec=_last_spec_by_scene.get(cfg['scene_id']),
        )
    except Exception as e:
        traceback.print_exc()
        _send(ws_dat, client, {'type': 'error', 'msg': str(e)})
        return

    if not api_key:
        _send(ws_dat, client, {'type': 'error', 'msg': gen._api_key_not_found_msg()})
        return

    _send(ws_dat, client, {'type': 'thinking', 'scene': cfg['scene_id']})

    scene_id = cfg['scene_id']
    import time as _time
    started_at = _time.time()

    def _worker():
        try:
            response_text = gen._call_anthropic(
                messages, model=gen.DEFAULT_MODEL, api_key=api_key
            )
            _pending[scene_id] = {
                'response':   response_text,
                'catalog':    catalog,
                'prompt':     user_prompt,
                'started_at': started_at,
                'error':      None,
            }
        except Exception as e:
            traceback.print_exc()
            _pending[scene_id] = {
                'response':   None,
                'catalog':    catalog,
                'prompt':     user_prompt,
                'started_at': started_at,
                'error':      str(e),
            }
        # Hop back to TD's main thread to apply + broadcast.
        # `run` isn't auto-injected into thread globals — import explicitly.
        # The string is exec'd on the main cook thread N frames from now.
        try:
            import td
            td.run(
                "op('/project1/unicorner_controller/callbacks').module._apply_pending("
                f"{scene_id!r})",
                delayFrames=1,
            )
        except Exception as e:
            traceback.print_exc()
            print(f"controller_ws: failed to schedule _apply_pending: {e}")

    threading.Thread(target=_worker, daemon=True, name='unicorner-generate').start()


def _apply_pending(scene_id: str) -> None:
    """Main-thread continuation of an async generate. Pulls the worker's
    result from `_pending[scene_id]`, parses/validates, applies the spec
    to the surface, broadcasts to all clients. Logs every outcome to
    td/.unicorner_log/<scene>.jsonl for post-mortem + future feedback loop."""
    entry = _pending.pop(scene_id, None)
    if entry is None:
        return

    ws = op('webserver1')  # noqa: F821 — sibling DAT
    gen = _generator()
    if gen is None:
        if ws is not None:
            _broadcast(ws, {'type': 'error', 'msg': 'generator module gone'})
        return

    import time as _time
    elapsed_ms = int((_time.time() - entry.get('started_at', _time.time())) * 1000)
    log_base = {
        'prompt':       entry.get('prompt'),
        'raw_response': entry.get('response'),
        'ms':           elapsed_ms,
        'model':        gen.DEFAULT_MODEL,
    }

    if entry['error']:
        gen.log_event(scene_id, {**log_base, 'outcome': 'api_error', 'error': entry['error']})
        if ws is not None:
            _broadcast(ws, {'type': 'error', 'msg': entry['error']})
        return

    try:
        parsed = gen.parse_response(entry['response'], entry['catalog'])
    except Exception as e:
        traceback.print_exc()
        gen.log_event(scene_id, {**log_base, 'outcome': 'validation_error', 'error': str(e)})
        if ws is not None:
            _broadcast(ws, {'type': 'error', 'msg': str(e)})
        return

    cfg = _config()

    if parsed['mode'] == 'alternatives':
        alts = parsed['alternatives']
        _pending_alternatives[cfg['scene_id']] = alts
        gen.log_event(scene_id, {
            **log_base,
            'outcome':      'alternatives',
            'alternatives': [{'id': a['id'], 'label': a['label']} for a in alts],
        })
        if ws is not None:
            _broadcast(ws, {
                'type':         'alternatives',
                'scene':        cfg['scene_id'],
                'alternatives': [
                    {'id': a['id'], 'label': a['label'], 'description': a['description']}
                    for a in alts
                ],
            })
        return

    # Single-spec mode — apply immediately.
    spec = parsed['spec']
    try:
        _apply_and_broadcast(spec, cfg, ws)
        gen.log_event(scene_id, {**log_base, 'outcome': 'applied', 'spec': spec})
    except Exception as e:
        gen.log_event(scene_id, {**log_base, 'outcome': 'apply_error', 'error': str(e), 'spec': spec})
        raise


def _apply_and_broadcast(spec: dict, cfg: dict, ws) -> None:
    gen = _generator()
    if gen is None:
        return
    try:
        gen.apply_spec(spec, cfg['scene_parent'])
    except Exception as e:
        traceback.print_exc()
        if ws is not None:
            _broadcast(ws, {'type': 'error', 'msg': str(e)})
        return
    _last_spec_by_scene[cfg['scene_id']] = spec
    _pending_alternatives.pop(cfg['scene_id'], None)
    if ws is not None:
        _broadcast(ws, {'type': 'spec', 'scene': cfg['scene_id'], 'spec': spec})
        _broadcast(ws, _build_schema_message(cfg))


def _handle_pick_alternative(ws_dat, client, msg) -> None:
    """User tapped one of the alternative chips. Find the chosen spec and
    apply it; clear the pending set."""
    cfg = _config()
    scene_id = cfg['scene_id']
    alts = _pending_alternatives.get(scene_id, [])
    alt_id = msg.get('alt_id')
    chosen = next((a for a in alts if a['id'] == alt_id), None)
    if chosen is None:
        _send(ws_dat, client, {
            'type': 'error',
            'msg':  f"alternative {alt_id!r} not in pending set",
        })
        return
    gen = _generator()
    if gen is not None:
        gen.log_event(scene_id, {
            'outcome':   'pick_alternative',
            'alt_id':    alt_id,
            'alt_label': chosen.get('label'),
            'spec':      chosen['spec'],
            # Snapshot the rejected siblings so we can mine negative examples later
            'rejected':  [{'id': a['id'], 'label': a['label']} for a in alts if a['id'] != alt_id],
        })
    _apply_and_broadcast(chosen['spec'], cfg, ws_dat)


def _handle_scene_change(ws_dat) -> None:
    cfg = _config()
    payload = {
        'type':  'scene_changed',
        'scene': cfg['scene_id'],
    }
    spec = _last_spec_by_scene.get(cfg['scene_id'])
    if spec is not None:
        payload['spec'] = spec
    _broadcast(ws_dat, payload)


# ---------------------------------------------------------------------------
# Web Server DAT callbacks
# ---------------------------------------------------------------------------

def onWebSocketOpen(webServerDAT, client, uri):
    debug(f"controller_ws: open client={client} uri={uri}")  # noqa: F821
    cfg = _config()
    # Send last-known spec if we have one (covers a reload mid-set).
    spec = _last_spec_by_scene.get(cfg['scene_id'])
    if spec is not None:
        _send(webServerDAT, client, {'type': 'spec', 'scene': cfg['scene_id'], 'spec': spec})
    # Always send the schema view too — it's what the legacy POC-5 path uses.
    _send(webServerDAT, client, _build_schema_message(cfg))


def onWebSocketClose(webServerDAT, client):
    debug(f"controller_ws: close client={client}")  # noqa: F821


def onWebSocketReceiveText(webServerDAT, client, data):
    try:
        msg = json.loads(data)
    except json.JSONDecodeError as e:
        debug(f"controller_ws: bad json from {client}: {e} :: {data!r}")  # noqa: F821
        return

    t = msg.get('type')
    if t == 'set':
        path = msg.get('path')
        value = msg.get('value')
        if path is None or value is None:
            debug(f"controller_ws: set missing path/value :: {msg!r}")  # noqa: F821
            return
        _set_param(path, value)
    elif t == 'generate':
        _handle_generate(webServerDAT, client, msg)
    elif t == 'pick_alternative':
        _handle_pick_alternative(webServerDAT, client, msg)
    else:
        debug(f"controller_ws: ignoring msg type={t!r}")  # noqa: F821


# Called from the COMP's parameter callbacks DAT when `Scene` changes, and
# when `Generate` pulse fires (passes msg=None to trigger refresh).
def onSceneChanged():
    ws = op('webserver1')  # noqa: F821 — relative lookup; COMP has a child named `webserver1`
    if ws is None:
        debug("controller_ws: onSceneChanged: no webserver1 sibling DAT found")  # noqa: F821
        return
    _handle_scene_change(ws)


def _lan_ip() -> str:
    """Best-effort IP an iPad on the same Wi-Fi would use to reach this laptop.
    Trick: open a UDP socket toward an external address — the OS picks the
    outbound interface; we read its local address. No packet is actually sent."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        return s.getsockname()[0]
    except Exception:
        return '127.0.0.1'
    finally:
        s.close()


def _compute_url(comp) -> str:
    port = 5173
    if hasattr(comp.par, 'Uiport'):
        try:
            port = int(comp.par.Uiport.eval() or 5173)
        except Exception:
            port = 5173
    return f"http://{_lan_ip()}:{port}"


def onRefreshUrlPulse():
    comp = _comp()
    if comp is None:
        return
    url = _compute_url(comp)
    if hasattr(comp.par, 'Url'):
        try:
            comp.par.Url.val = url
        except Exception:
            pass
    debug(f"controller_ws: URL = {url}")  # noqa: F821


def onOpenBrowserPulse():
    comp = _comp()
    if comp is None:
        return
    url = _compute_url(comp)
    if hasattr(comp.par, 'Url'):
        try:
            comp.par.Url.val = url
        except Exception:
            pass
    try:
        webbrowser.open(url)
    except Exception as e:
        debug(f"controller_ws: failed to open browser: {e}")  # noqa: F821


def onSaveKeyPulse():
    """Save the value currently in the `Apikey` parameter to the gitignored
    config file, then clear the parameter so the key never lands in the .toe.
    Updates `Keystatus` so the user can see whether the save worked."""
    comp = _comp()
    if comp is None:
        return
    gen = _generator()
    if gen is None:
        _set_status(comp, 'generator module not loaded')
        return
    raw = (comp.par.Apikey.eval() if hasattr(comp.par, 'Apikey') else '') or ''
    raw = raw.strip()
    if not raw:
        _set_status(comp, 'no key entered')
        return
    try:
        path = gen.save_config_key(raw)
    except Exception as e:
        traceback.print_exc()
        _set_status(comp, f'save failed: {e}')
        return
    # Clear the param so it isn't persisted in the .toe on next save.
    try:
        comp.par.Apikey.val = ''
    except Exception:
        pass
    _set_status(comp, f'saved to {path}')


def _set_status(comp, text: str) -> None:
    if hasattr(comp.par, 'Keystatus'):
        try:
            comp.par.Keystatus.val = text
        except Exception:
            pass


def onGeneratePulse():
    """Triggered when the COMP's `Generate` parameter is pulsed. Reads the
    `Prompt` parameter for the user text — escape hatch for TD-native users
    who never open the iPad. Runs through the same async pipeline as the
    WS path so it doesn't freeze the cook."""
    comp = _comp()
    if comp is None:
        return
    prompt = (comp.par.Prompt.eval() if hasattr(comp.par, 'Prompt') else '').strip()
    if not prompt:
        debug("controller_ws: Generate pulsed but Prompt is empty")  # noqa: F821
        return
    ws = op('webserver1')  # noqa: F821
    # Synthesize a minimal msg shape so we can reuse the threaded path.
    _handle_generate(ws, client=None, msg={'prompt': prompt, 'history': []})


# Web Server DAT also calls these; keep no-ops so TD doesn't warn.
def onHTTPRequest(webServerDAT, request, response):
    response['statusCode'] = 200
    response['statusReason'] = 'OK'
    response['data'] = 'Unicorner controller web server is up. Use WebSocket.'
    return response


def onWebSocketReceiveBinary(webServerDAT, client, data):
    return


def onWebSocketReceivePing(webServerDAT, client, data):
    webServerDAT.webSocketSendPong(client, data=data)


def onWebSocketReceivePong(webServerDAT, client, data):
    return


def onServerStart(webServerDAT):
    print("HTTP server started")


def onServerStop(webServerDAT):
    print("HTTP server stopped")
