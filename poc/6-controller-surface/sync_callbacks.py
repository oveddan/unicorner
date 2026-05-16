"""
POC 6 — Sync the source-controlled ws_callbacks.py into TD's callbacks DAT.

After editing `poc/0-ws-roundtrip/ws_callbacks.py`, the file on disk doesn't
auto-propagate into TD — the Web Server DAT runs whatever the in-project
Text DAT says. This script reads the file and writes its contents into the
Text DAT, so changes take effect on the next WS connection (existing open
sockets keep using the old code).

Run by handing `BODY` to `mcp__touchdesigner-stdio__execute_python_script`.
"""

CALLBACKS_SRC = '/Users/danoved/Source/unicorner/poc/0-ws-roundtrip/ws_callbacks.py'
DAT_PATH      = '/project1/poc0_ws_callbacks'


BODY = f'''
src = {CALLBACKS_SRC!r}
dat_path = {DAT_PATH!r}

with open(src) as f:
    text = f.read()

dat = op(dat_path)
if dat is None:
    raise RuntimeError(f"callbacks DAT not found at {{dat_path!r}}")

dat.text = text

{{
    "wrote_bytes":         len(text),
    "first_line":          text.splitlines()[0] if text else None,
    "has_build_schema":    "_build_schema" in dat.text,
    "has_onWebSocketOpen": "def onWebSocketOpen" in dat.text,
}}
'''


if __name__ == '__main__':
    print(BODY)
