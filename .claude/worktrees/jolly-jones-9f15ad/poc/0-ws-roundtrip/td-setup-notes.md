# POC 0 — TouchDesigner setup notes

Manual setup to prove a browser can write a TD parameter over WebSocket. Once this works end-to-end, [POC 1](../1-mcp-scaffold/) will recreate exactly this scene programmatically via MCP.

## What we're building

```
poc0.html (browser)
     │
     │  ws://127.0.0.1:9980
     │  { "type":"set", "path":"/project1/poc0_target/value0", "value": 0.42 }
     ▼
Web Server DAT  ──onWebSocketReceiveText──>  op('/project1/poc0_target').par.value0 = 0.42
```

Two TD nodes total: a **Web Server DAT** (the listener) and a **Constant CHOP** named `poc0_target` (the target whose `value0` parameter we mutate).

## Steps

### 1. Create the target

In `/project1`:
- Add a **Constant CHOP** named `poc0_target`.
- It has a `value0` parameter — that's what we'll be mutating. Default 0.

This is the smallest possible "did the message land?" probe. The value shows in the Parameter window and on the operator preview.

### 2. Create the Web Server DAT

In `/project1`:
- Add a **Web Server DAT** named `poc0_ws`.
- Parameters:
  - **Active**: On
  - **Port**: `9980`
  - **WebSocket**: On (this is what makes it accept WebSocket clients; without it, it's HTTP-only)
- The DAT auto-creates a callbacks DAT named `webserver1_callbacks` (or similar). Open it.

### 3. Paste the callback

Replace the contents of the callbacks DAT with the code from [`ws_callbacks.py`](./ws_callbacks.py) in this folder. The relevant callback is `onWebSocketReceiveText` — it parses the incoming JSON and writes to the target param.

**Important:** TD's `op(path).par.<name>` syntax expects the parent operator path and a parameter attribute, **not** the full param path as one string. So we split `/project1/poc0_target/value0` into:
- node path: `/project1/poc0_target`
- param name: `value0`

The handler does this split.

### 4. Test it

1. Save the TD project somewhere (e.g. `~/Source/unicorner-td/poc0.toe`) so reloading is fast.
2. Open `poc0.html` in a browser on the same machine (drag-drop into Chrome works; later POCs will use Vite).
3. Click **Connect**. Status should flip to `connected ws://127.0.0.1:9980`.
4. Move the slider. The `value0` parameter on `poc0_target` should update in real time inside TD.

## Pass criteria

✅ Browser shows `connected`.
✅ Slider drag visibly changes `poc0_target`'s `value0` value in the TD Parameter window or operator preview.
✅ No errors in the TD textport (open with Alt+T).

If those three are true, the entire v1 runtime transport is proven and we move to POC 1.

## Common gotchas

- **`error` immediately on connect** — Web Server DAT's WebSocket toggle is off, or the port is taken. Check `lsof -i :9980` on macOS.
- **`disconnected (1006)`** — DAT is set to HTTP-only. Toggle WebSocket on.
- **Slider moves but param doesn't update** — the callback is throwing. Open Alt+T (textport) and watch for the traceback as you move the slider.
- **`KeyError` or `AttributeError: 'NoneType'`** — the `path` in your JSON doesn't resolve. Verify in the textport: `op('/project1/poc0_target')` should print the node, not `None`.
- **iPad can't connect** — POC 0 is loopback only. Binding to `0.0.0.0` and LAN access is a [POC 8](../../docs/venue-networking.md) concern; don't worry about it yet.

## Next

Once green, [POC 1](../1-mcp-scaffold/) recreates this same scene from an empty TD project using MCP — proving we can scaffold scenes programmatically.
