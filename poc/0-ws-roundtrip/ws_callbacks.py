# Web Server DAT callbacks for POC 0 + controller-surface schema push.
# Pastes into the auto-generated callbacks DAT on the Web Server DAT.
#
# Handles:
#   onWebSocketOpen          — enumerate /project1/controller_surface custom params
#                              and push a {type:"schema", params:[...]} message to
#                              the connecting client so the iPad app can build its UI
#   onWebSocketReceiveText   — parse {type:"set", path, value} and write to a param
#   onWebSocketClose         — log connection lifecycle for visibility
#
# Keep this file in source control as the spec for what the callback does.
# In TD, the callbacks DAT contents must match this.

import json


SURFACE_PATH = "/project1/controller_surface"


def _set_param(path: str, value):
	# path is "<node_path>/<param_name>", e.g. "/project1/controller_surface/Intensity"
	# Split into node path + param name. Param names never contain "/", node paths can.
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


def _widget_type_for_par(par):
	# Map TD par style → controller widget type. Returns None for styles we don't render.
	style = par.style
	if style in ("Float",):
		return "float"
	if style in ("Int",):
		return "int"
	if style in ("Toggle",):
		return "bool"
	if style in ("Pulse", "Momentary"):
		return "pulse"
	return None


def _build_schema():
	surface = op(SURFACE_PATH)
	if surface is None:
		debug(f"poc0: no controller surface at {SURFACE_PATH!r}; sending empty schema")
		return {"type": "schema", "params": []}

	params = []
	for par in surface.customPars:
		widget_type = _widget_type_for_par(par)
		if widget_type is None:
			continue
		entry = {
			"name": par.name,
			"label": par.label,
			"type": widget_type,
		}
		if widget_type in ("float", "int"):
			entry["min"] = par.normMin
			entry["max"] = par.normMax
		try:
			entry["default"] = par.default
		except Exception:
			pass
		params.append(entry)

	return {"type": "schema", "params": params}


def onWebSocketOpen(webServerDAT, client, uri):
	debug(f"poc0: ws open  client={client} uri={uri}")
	try:
		schema = _build_schema()
		webServerDAT.webSocketSendText(client, json.dumps(schema))
		debug(f"poc0: sent schema with {len(schema['params'])} param(s) to {client}")
	except Exception as e:
		debug(f"poc0: failed to send schema to {client}: {e}")
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


# The Web Server DAT also calls these — leave as no-ops so TD doesn't warn.
def onHTTPRequest(webServerDAT, request, response):
	response['statusCode'] = 200
	response['statusReason'] = 'OK'
	response['data'] = 'POC 0 web server is up. Use WebSocket.'
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
