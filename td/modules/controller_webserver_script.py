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
import mimetypes
import os
import posixpath
import socket
import threading
import traceback
import webbrowser
from urllib.parse import unquote, urlsplit


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

# Per-scene clarifying-question state. When the model returns a question
# instead of a spec, we stash:
#   - the answer chips ({id, label, description})
#   - the original user prompt that triggered the question
#   - the assistant's question text (so we can append it to history when the
#     user picks an answer)
#   - the conversation history at the time of the question
#   - the in-flight scene_summary (if any) so we keep using it when the user
#     answers
_pending_questions: dict = {}  # scene_id -> {answers, prompt, question, history, scene_summary}

# Per-scene pending scene-understanding request. The worker stores its result
# here, then schedules `_apply_pending_summary` on the main thread.
_pending_summary: dict = {}  # scene_id -> {'summary': str|None, 'error': str|None}

# Per-scene context for the *current* in-flight generate. We need this when
# the model comes back with a question — we have to remember what the user
# asked + what summary was active so the follow-up generate (after they pick
# an answer chip) can reconstruct the conversation.
_generate_context_by_scene: dict = {}  # scene_id -> {prompt, history, scene_summary}


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
    """Read Target + Scene + Djaypath from the COMP's custom params."""
    comp = _comp()
    if comp is None:
        return {'target': '/project1', 'scene': '', 'scene_id': 'default', 'djay_path': ''}
    target = comp.par.Target.eval() if hasattr(comp.par, 'Target') else '/project1'
    scene  = comp.par.Scene.eval()  if hasattr(comp.par, 'Scene')  else ''
    djay_path = ''
    if hasattr(comp.par, 'Djaypath'):
        try:
            djay_path = (comp.par.Djaypath.eval() or '').strip()
        except Exception:
            djay_path = ''
    catalog_depth = 'full'
    if hasattr(comp.par, 'Depth'):
        try:
            catalog_depth = comp.par.Depth.eval() or 'full'
        except Exception:
            catalog_depth = 'full'
    scene_root  = scene or target
    scene_id    = (scene.rsplit('/', 1)[-1] if scene else (target.rsplit('/', 1)[-1] or 'scene'))
    scene_parent = scene or target  # surface lives directly under the scene root
    return {
        'comp':          comp,
        'target':        target,
        'scene':         scene,
        'scene_root':    scene_root,
        'scene_parent':  scene_parent,
        'scene_id':      scene_id,
        'djay_path':     djay_path,
        'catalog_depth': catalog_depth,
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
    scene_summary = msg.get('scene_summary') if isinstance(msg.get('scene_summary'), str) else None
    # Message-level depth overrides the COMP param (iPad can send it directly).
    msg_depth = msg.get('depth')
    catalog_depth = msg_depth if msg_depth in ('full', 'curated', 'minimal') else cfg.get('catalog_depth', 'full')
    try:
        catalog    = gen.trim_catalog(
            gen.extract_catalog(cfg['scene_root'], cfg['scene_id']),
            catalog_depth,
        )
        dj_catalog = gen.extract_djay_catalog(cfg.get('djay_path') or '')
        api_key    = gen.resolve_api_key(cfg.get('comp'))
        messages   = gen.build_messages(
            catalog,
            user_prompt,
            history=msg.get('history') or [],
            current_spec=_last_spec_by_scene.get(cfg['scene_id']),
            scene_summary=scene_summary,
            dj_catalog=dj_catalog,
        )
    except Exception as e:
        traceback.print_exc()
        _send(ws_dat, client, {'type': 'error', 'msg': str(e)})
        return

    if not api_key:
        _send(ws_dat, client, {'type': 'error', 'msg': gen._api_key_not_found_msg()})
        return

    # Starting a new generate invalidates any prior pending question for this
    # scene — the user has moved on.
    _pending_questions.pop(cfg['scene_id'], None)

    _send(ws_dat, client, {'type': 'thinking', 'scene': cfg['scene_id']})

    scene_id = cfg['scene_id']
    import time as _time
    started_at = _time.time()
    # Stash the prompt + summary + history so the question-mode branch in
    # _apply_pending can build _pending_questions[...] without a second copy
    # of the message data.
    _generate_context_by_scene[scene_id] = {
        'prompt':        user_prompt,
        'history':       msg.get('history') or [],
        'scene_summary': scene_summary,
    }
    # Resolve me.path on the main thread — TD objects must not be touched
    # from background threads (causes the cross-thread warning on Windows).
    me_path = me.path  # noqa: F821

    def _worker():
        try:
            response_text = gen.call_anthropic_with_json_retry(
                messages, model=gen.DEFAULT_MODEL, api_key=api_key,
            )
            _pending[scene_id] = {
                'response':   response_text,
                'catalog':    catalog,
                'dj_catalog': dj_catalog,
                'prompt':     user_prompt,
                'started_at': started_at,
                'error':      None,
            }
        except Exception as e:
            traceback.print_exc()
            _pending[scene_id] = {
                'response':   None,
                'catalog':    catalog,
                'dj_catalog': dj_catalog,
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
                f"op({me_path!r}).module._apply_pending({scene_id!r})",
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
        parsed = gen.parse_response(
            entry['response'], entry['catalog'], dj_catalog=entry.get('dj_catalog'),
        )
    except Exception as e:
        traceback.print_exc()
        gen.log_event(scene_id, {**log_base, 'outcome': 'validation_error', 'error': str(e)})
        if ws is not None:
            _broadcast(ws, {'type': 'error', 'msg': str(e)})
        return

    cfg = _config()

    if parsed['mode'] == 'question':
        ctx = _generate_context_by_scene.get(cfg['scene_id']) or {}
        question_text = parsed['question']
        answers = parsed['alternatives']
        _pending_questions[cfg['scene_id']] = {
            'answers':       answers,
            'prompt':        ctx.get('prompt') or '',
            'question':      question_text,
            'history':       ctx.get('history') or [],
            'scene_summary': ctx.get('scene_summary'),
        }
        # A question supersedes any pending spec-alternatives — the user can
        # only act on one outstanding decision at a time.
        _pending_alternatives.pop(cfg['scene_id'], None)
        gen.log_event(scene_id, {
            **log_base,
            'outcome':  'question',
            'question': question_text,
            'answers':  [{'id': a['id'], 'label': a['label']} for a in answers],
        })
        if ws is not None:
            _broadcast(ws, {
                'type':         'alternatives',
                'scene':        cfg['scene_id'],
                'question':     question_text,
                'alternatives': [
                    {'id': a['id'], 'label': a['label'],
                     'description': a['description'], 'kind': 'question'}
                    for a in answers
                ],
            })
        return

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
                    {'id': a['id'], 'label': a['label'],
                     'description': a['description'], 'kind': 'choice'}
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
    # Resolve the DJay CHOP path so apply_spec can wire any routings in the
    # spec. Cheap walk — re-cataloging is fine here (we're on the main thread
    # and apply_spec needs the chop_path either way).
    dj_chop_path = None
    djay_path = cfg.get('djay_path') or ''
    if djay_path:
        dj_catalog = gen.extract_djay_catalog(djay_path)
        if dj_catalog is not None:
            dj_chop_path = dj_catalog.get('chop_path')
    try:
        gen.apply_spec(spec, cfg['scene_parent'], dj_chop_path=dj_chop_path)
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
    """User tapped one of the alternative chips. Two flows depending on what's
    pending for this scene:
      - 'choice' kind: a spec is already attached; apply it and clear pending.
      - 'question' kind: the user just answered a clarifying question. Append
        the Q+A to history and kick off a follow-up generate.
    """
    cfg = _config()
    scene_id = cfg['scene_id']
    alt_id = msg.get('alt_id')

    # Question-mode pick takes precedence: it's the most recently pending.
    qpend = _pending_questions.get(scene_id)
    if qpend is not None:
        answer = next((a for a in qpend['answers'] if a['id'] == alt_id), None)
        if answer is None:
            _send(ws_dat, client, {
                'type': 'error',
                'msg':  f"answer {alt_id!r} not in pending question",
            })
            return
        gen = _generator()
        if gen is not None:
            gen.log_event(scene_id, {
                'outcome':     'answer_question',
                'question':    qpend['question'],
                'answer_id':   alt_id,
                'answer':      answer.get('label'),
            })
        # Append the question + answer to history, then re-enter generate.
        followup_history = list(qpend.get('history') or [])
        followup_history.append({'role': 'user',      'content': qpend['prompt']})
        followup_history.append({'role': 'assistant', 'content': qpend['question']})
        # Clear before recursing so a *new* question (if the model asks one)
        # overwrites cleanly.
        _pending_questions.pop(scene_id, None)
        _handle_generate(ws_dat, client, {
            'prompt':        answer.get('label') or '',
            'history':       followup_history,
            'scene_summary': qpend.get('scene_summary'),
        })
        return

    alts = _pending_alternatives.get(scene_id, [])
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


def _handle_understand_scene(ws_dat, client, msg) -> None:
    """Async scene summary. Same threading pattern as `_handle_generate`:
    main thread walks the catalog + resolves the API key, worker thread hits
    Anthropic, main thread broadcasts the summary back to all clients.
    """
    gen = _generator()
    if gen is None:
        _send(ws_dat, client, {
            'type': 'error',
            'msg':  'generator Text DAT not found — re-run poc/7-drop-in/scaffold.py',
        })
        return

    cfg = _config()
    msg_depth = msg.get('depth')
    catalog_depth = msg_depth if msg_depth in ('full', 'curated', 'minimal') else cfg.get('catalog_depth', 'full')
    try:
        catalog = gen.trim_catalog(
            gen.extract_catalog(cfg['scene_root'], cfg['scene_id']),
            catalog_depth,
        )
        api_key = gen.resolve_api_key(cfg.get('comp'))
    except Exception as e:
        traceback.print_exc()
        _send(ws_dat, client, {'type': 'error', 'msg': str(e)})
        return

    if not api_key:
        _send(ws_dat, client, {'type': 'error', 'msg': gen._api_key_not_found_msg()})
        return

    _send(ws_dat, client, {'type': 'understand_thinking', 'scene': cfg['scene_id']})

    scene_id = cfg['scene_id']
    me_path = me.path  # noqa: F821 — captured on main thread before worker starts

    def _worker():
        try:
            summary = gen.summarize_catalog(catalog, model=gen.DEFAULT_MODEL, api_key=api_key)
            _pending_summary[scene_id] = {'summary': summary, 'error': None}
        except Exception as e:
            traceback.print_exc()
            _pending_summary[scene_id] = {'summary': None, 'error': str(e)}
        try:
            import td
            td.run(
                f"op({me_path!r}).module._apply_pending_summary({scene_id!r})",
                delayFrames=1,
            )
        except Exception as e:
            traceback.print_exc()
            print(f"controller_ws: failed to schedule _apply_pending_summary: {e}")

    threading.Thread(target=_worker, daemon=True, name='unicorner-summarize').start()


def _apply_pending_summary(scene_id: str) -> None:
    entry = _pending_summary.pop(scene_id, None)
    if entry is None:
        return
    ws = op('webserver1')  # noqa: F821
    if ws is None:
        return
    if entry.get('error'):
        _broadcast(ws, {'type': 'error', 'msg': entry['error']})
        return
    _broadcast(ws, {
        'type':    'scene_summary',
        'scene':   scene_id,
        'summary': entry.get('summary') or '',
    })


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


def _handle_reset_scene(ws_dat, client, msg) -> None:
    """Full reset for the active scene: destroy the controller_surface and
    any unicorner.routing-tagged ops under the scene parent, clear all
    server-side caches (last spec, pending alternatives/questions/summary,
    routing target params), then broadcast an empty schema + a cleared
    scene_summary so all clients reset in lockstep.

    The iPad-side companion clears localStorage (chat log, draft, summary)
    so the next prompt starts from a clean slate.
    """
    cfg = _config()
    scene_id = cfg['scene_id']
    gen = _generator()

    # Tear down the controller surface (and clear any expressions on
    # downstream pars that referenced it).
    parent = op(cfg['scene_parent'])  # noqa: F821
    if parent is not None and gen is not None:
        surface = parent.op(gen.SURFACE_NAME)
        if surface is not None:
            try:
                # Reuse the existing teardown to clear dangling expressions.
                gen._teardown_surface(parent, _surface_path(cfg), {})
            except Exception:
                traceback.print_exc()

        # Also destroy routing-tagged ops (LFO CHOPs, CHOP Execute DATs)
        # and clear expressions on params tracked from prior routings.
        try:
            gen._apply_routings(
                routings=[],
                scene_parent=cfg['scene_parent'],
                dj_chop_path=None,
                surface_path=_surface_path(cfg),
                control_id_to_par={},
                scene_id=scene_id,
                EXPRESSION=gen._par_mode_enum(parent).EXPRESSION,
            )
        except Exception:
            traceback.print_exc()

    # Clear every per-scene cache. Use .pop with default so missing keys
    # don't blow up.
    _last_spec_by_scene.pop(scene_id, None)
    _pending.pop(scene_id, None)
    _pending_alternatives.pop(scene_id, None)
    _pending_questions.pop(scene_id, None)
    _pending_summary.pop(scene_id, None)
    _generate_context_by_scene.pop(scene_id, None)
    if gen is not None and hasattr(gen, '_last_routing_targets_by_scene'):
        gen._last_routing_targets_by_scene.pop(scene_id, None)

    if gen is not None:
        try:
            gen.log_event(scene_id, {'outcome': 'reset_scene'})
        except Exception:
            pass

    # Broadcast the reset so all clients (and any extra browser tabs) flush
    # their local state and the surface goes back to the "empty" view.
    _broadcast(ws_dat, _build_schema_message(cfg))
    _broadcast(ws_dat, {'type': 'scene_summary', 'scene': scene_id, 'summary': ''})
    _broadcast(ws_dat, {'type': 'scene_reset',   'scene': scene_id})


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
    elif t == 'understand_scene':
        _handle_understand_scene(webServerDAT, client, msg)
    elif t == 'reset_scene':
        _handle_reset_scene(webServerDAT, client, msg)
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
    # If the COMP can self-serve the controller (Distpath points at a real
    # dist with index.html), use the WebSocket port — single URL for the
    # iPad, no Vite needed. Otherwise fall back to the dev-mode Uiport
    # (external Vite at 5173 by default).
    if _dist_root(comp) is not None:
        port = 9980
        ws = comp.op('webserver1') if comp is not None else None
        if ws is not None and hasattr(ws.par, 'port'):
            try:
                port = int(ws.par.port.eval())
            except Exception:
                pass
        return f"http://{_lan_ip()}:{port}"
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


# ---------------------------------------------------------------------------
# Static file serving — the COMP doubles as an HTTP server for the built
# controller (Vite dist/). When Distpath resolves to a folder containing
# index.html, every GET is served from disk; otherwise we return a help
# stub so the user knows what to set.
# ---------------------------------------------------------------------------

_MIME_OVERRIDES = {
    '.js':    'text/javascript',
    '.mjs':   'text/javascript',
    '.css':   'text/css',
    '.html':  'text/html; charset=utf-8',
    '.htm':   'text/html; charset=utf-8',
    '.json':  'application/json',
    '.map':   'application/json',
    '.svg':   'image/svg+xml',
    '.png':   'image/png',
    '.jpg':   'image/jpeg',
    '.jpeg':  'image/jpeg',
    '.gif':   'image/gif',
    '.webp':  'image/webp',
    '.ico':   'image/x-icon',
    '.woff':  'font/woff',
    '.woff2': 'font/woff2',
    '.ttf':   'font/ttf',
    '.txt':   'text/plain; charset=utf-8',
    '.wasm':  'application/wasm',
}
_TEXT_EXTS = {'.js', '.mjs', '.css', '.html', '.htm', '.json', '.map', '.svg', '.txt'}


def _dist_root(comp):
    """Resolve the COMP's Distpath param against the saved project location.
    Return the absolute folder path only if it exists and contains
    index.html — otherwise None (HTTP handler falls back to a help stub).

    We resolve relative paths against project.folder (the .toe directory)
    rather than tdu.expandPath so that the result is reliable on Windows —
    tdu.expandPath can embed a literal './' in the returned string which
    os.path.isdir then rejects, and its base varies depending on how the
    .tox was loaded.
    """
    if comp is None or not hasattr(comp.par, 'Distpath'):
        return None
    raw = (comp.par.Distpath.eval() or '').strip()
    if not raw:
        return None
    try:
        if os.path.isabs(raw):
            abs_path = os.path.normpath(raw)
        else:
            abs_path = os.path.normpath(
                os.path.join(project.folder, raw)  # noqa: F821 — TD global
            )
    except Exception:
        return None
    if not os.path.isdir(abs_path):
        return None
    if not os.path.isfile(os.path.join(abs_path, 'index.html')):
        return None
    return abs_path


def _http_error(response, code: int, reason: str, body: str = '') -> dict:
    response['statusCode'] = code
    response['statusReason'] = reason
    response['data'] = body or (reason + '\n')
    response['Content-Type'] = 'text/plain; charset=utf-8'
    return response


def _serve_static(request, response, dist_root: str) -> dict:
    raw_uri = request.get('uri') or '/'
    path_only = urlsplit(raw_uri).path or '/'
    decoded = unquote(path_only)
    # Reject backslash in the URL outright. URL paths are forward-slash;
    # backslash here would let an attacker smuggle a Windows path separator
    # past posixpath.normpath (which treats '\' as a regular character) and
    # have it interpreted as a directory boundary by os.path.realpath later.
    if '\\' in decoded:
        return _http_error(response, 403, 'Forbidden')
    if decoded.endswith('/'):
        decoded += 'index.html'
    # posixpath.normpath collapses ../ — afterwards, anything starting with
    # '..' means the request tried to escape the dist root.
    normalized = posixpath.normpath(decoded)
    if normalized.startswith('..'):
        return _http_error(response, 403, 'Forbidden')
    rel = normalized.lstrip('/')
    abs_path = os.path.realpath(os.path.join(dist_root, rel))
    dist_real = os.path.realpath(dist_root)
    # Belt-and-suspenders: even after normalization, ensure the resolved
    # path is still under dist_root (catches symlinks pointing outside).
    # os.path.normcase makes the comparison case-insensitive on Windows
    # (where the filesystem ignores case but realpath preserves the on-disk
    # spelling, which may differ from what the user typed into Distpath).
    abs_cmp  = os.path.normcase(abs_path)
    dist_cmp = os.path.normcase(dist_real)
    if not (abs_cmp == dist_cmp or abs_cmp.startswith(dist_cmp + os.sep)):
        return _http_error(response, 403, 'Forbidden')
    if not os.path.isfile(abs_path):
        return _http_error(response, 404, 'Not Found', f'not found: {rel}\n')

    ext = os.path.splitext(abs_path)[1].lower()
    mime = _MIME_OVERRIDES.get(ext) or mimetypes.guess_type(abs_path)[0] or 'application/octet-stream'

    if ext in _TEXT_EXTS:
        with open(abs_path, 'r', encoding='utf-8') as f:
            data = f.read()
    else:
        with open(abs_path, 'rb') as f:
            data = f.read()

    # Vite emits hashed names under /assets/* — safe to long-cache.
    # Everything else (notably index.html) must always revalidate so a fresh
    # build reaches the iPad without manual cache-busting.
    cache = 'public, max-age=31536000, immutable' if rel.startswith('assets/') else 'no-cache'

    response['statusCode'] = 200
    response['statusReason'] = 'OK'
    response['data'] = data
    response['Content-Type'] = mime
    response['Cache-Control'] = cache
    return response


def onHTTPRequest(webServerDAT, request, response):
    comp = _comp()
    dist_root = _dist_root(comp)
    if dist_root is None:
        # No bundled UI — serve a help stub so curl-based debugging shows
        # what's missing instead of a silent 200.
        response['statusCode'] = 200
        response['statusReason'] = 'OK'
        response['data'] = (
            "Unicorner controller WebSocket is up on this port.\n"
            "No controller UI bundle found.\n"
            "  - Set the COMP's Distpath param to a folder containing the\n"
            "    built controller (Vite dist/, must contain index.html), or\n"
            "  - run `npm run dev` in controller/ and use the Uiport URL.\n"
        )
        response['Content-Type'] = 'text/plain; charset=utf-8'
        return response
    return _serve_static(request, response, dist_root)


def onWebSocketReceiveBinary(webServerDAT, client, data):
    return


def onWebSocketReceivePing(webServerDAT, client, data):
    webServerDAT.webSocketSendPong(client, data=data)


def onWebSocketReceivePong(webServerDAT, client, data):
    return


def onServerStart(webServerDAT):
    print("HTTP server started")
    # Auto-compute and refresh the Url param so it always reflects the
    # current machine's LAN IP — even if the .tox was saved on a different
    # machine with a different IP baked in.
    comp = _comp()
    if comp is not None and hasattr(comp.par, 'Url'):
        url = _compute_url(comp)
        try:
            comp.par.Url.val = url
        except Exception:
            pass
        print(f"HTTP controller URL: {url}")


def onServerStop(webServerDAT):
    print("HTTP server stopped")
