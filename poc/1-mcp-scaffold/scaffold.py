"""
POC 1 — Programmatic scaffold of POC 0's TD scene, via MCP.

Run by handing the contents of this file to the
`mcp__touchdesigner-stdio__execute_python_script` tool. TD's MCP web server
executes it in-process; it builds the same scene that POC 0 set up by hand:

    /project1/poc0_target       (Constant CHOP, value0 is the test param)
    /project1/poc0_ws           (Web Server DAT on port 9980, WebSocket on)
    /project1/poc0_ws_callbacks (Text DAT, holds the JSON-handling callback)

Idempotent: deletes any of these three ops if they already exist before
recreating. Safe to re-run repeatedly.

The callback code is embedded as a string below — keep it in sync with
`../0-ws-roundtrip/ws_callbacks.py` (which is the human-readable reference
used for the manual POC 0 path).
"""

# ----- Config ---------------------------------------------------------------

PARENT_PATH      = '/project1'
TARGET_NAME      = 'poc0_target'
WS_DAT_NAME      = 'poc0_ws'
CB_DAT_NAME      = 'poc0_ws_callbacks'
WS_PORT          = 9980

# ----- Callback code (mirrors ../0-ws-roundtrip/ws_callbacks.py) -----------

CALLBACK_CODE = r'''# POC 0/1 — Web Server DAT callbacks. Mirrors ws_callbacks.py.
import json


def _set_param(path, value):
	node_path, _, param_name = path.rpartition("/")
	if not node_path or not param_name:
		debug(f"poc0: malformed path {path!r}")
		return False

	node = op(node_path)
	if node is None:
		debug(f"poc0: no node at {node_path!r}")
		return False

	par = getattr(node.par, param_name, None)
	if par is None:
		debug(f"poc0: no param {param_name!r} on {node_path!r}")
		return False

	par.val = value
	return True


def onWebSocketOpen(webServerDAT, client, uri):
	debug(f"poc0: ws open  client={client} uri={uri}")
	return


def onWebSocketClose(webServerDAT, client):
	debug(f"poc0: ws close client={client}")
	return


def onWebSocketReceiveText(webServerDAT, client, data):
	try:
		msg = json.loads(data)
	except json.JSONDecodeError as e:
		debug(f"poc0: bad json from {client}: {e} :: {data!r}")
		return

	if msg.get("type") != "set":
		debug(f"poc0: ignoring msg type={msg.get('type')!r}")
		return

	path = msg.get("path")
	value = msg.get("value")
	if path is None or value is None:
		debug(f"poc0: missing path/value :: {msg!r}")
		return

	_set_param(path, value)


def onHTTPRequest(webServerDAT, request, response):
	response['statusCode'] = 200
	response['statusReason'] = 'OK'
	response['data'] = 'POC 0/1 web server is up. Use WebSocket.'
	return response


def onWebSocketReceiveBinary(webServerDAT, client, data):
	return


def onWebSocketReceivePing(webServerDAT, client, data):
	webServerDAT.webSocketSendPong(client, data=data)
	return


def onWebSocketReceivePong(webServerDAT, client, data):
	return


def onServerStart(webServerDAT):
	debug("poc0: web server started")
	return


def onServerStop(webServerDAT):
	debug("poc0: web server stopped")
	return
'''

# ----- Scaffold -------------------------------------------------------------

parent = op(PARENT_PATH)
assert parent is not None, f"parent {PARENT_PATH!r} does not exist"

report = []

# Idempotent cleanup
for name in (WS_DAT_NAME, CB_DAT_NAME, TARGET_NAME):
	existing = parent.op(name)
	if existing is not None:
		existing.destroy()
		report.append(f"destroyed existing {name}")

# 1) Target Constant CHOP — has `value0` we'll mutate from the browser
target = parent.create('constantCHOP', TARGET_NAME)
target.par.value0 = 0.0
target.nodeX = 0
target.nodeY = 0
report.append(f"created {target.path} (Constant CHOP)")

# 2) Callbacks Text DAT — holds the Python that the Web Server DAT calls
cb = parent.create('textDAT', CB_DAT_NAME)
cb.text = CALLBACK_CODE
cb.nodeX = 0
cb.nodeY = -150
report.append(f"created {cb.path} (Text DAT, {len(CALLBACK_CODE)} chars)")

# 3) Web Server DAT — actual listener
ws = parent.create('webserverDAT', WS_DAT_NAME)
ws.nodeX = 200
ws.nodeY = -150
report.append(f"created {ws.path} (Web Server DAT)")

# Set port, point at callbacks, then activate.
# Web Server DAT parameter names (TD 2023+):
#   port      — listening port
#   callbacks — path to the callbacks DAT
#   active    — toggle the server on
# WebSocket support is enabled at runtime when a client connects with
# Upgrade: websocket; no separate toggle is needed in current TD builds.
ws.par.port = WS_PORT
ws.par.callbacks = cb.path
ws.par.active = True
report.append(f"configured {ws.path}: port={WS_PORT}, callbacks={cb.path}, active=True")

# Verify
final = {
	'target':    target.path,
	'callbacks': cb.path,
	'webserver': ws.path,
	'port':      int(ws.par.port.eval()),
	'active':    bool(ws.par.active.eval()),
}

print('\n'.join(report))
print('---')
print('POC 1 scaffold complete:')
for k, v in final.items():
	print(f'  {k}: {v}')
print(f'\nNext: point poc0.html at ws://127.0.0.1:{WS_PORT} and move the slider.')
