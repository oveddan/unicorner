# Web Server DAT callbacks for POC 0.
# Pastes into the auto-generated callbacks DAT on the Web Server DAT.
#
# Handles:
#   onWebSocketReceiveText  — parse {type:"set", path, value} and write to a param
#   onWebSocketOpen / Close — log connection lifecycle for visibility during testing
#
# Keep this file in source control as the spec for what the callback does.
# In TD, the callbacks DAT contents must match this.

import json


def _set_param(path: str, value):
	# path is "<node_path>/<param_name>", e.g. "/project1/poc0_target/value0"
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
