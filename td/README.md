# TouchDesigner project

`main.toe` is the working TD project for the prototype. Open it, do work in `/project1`, save.

## First-time setup

1. Open TouchDesigner.
2. Save a new project as `td/main.toe` in this repo.
3. Drag in [`../vendor/touchdesigner-mcp-td/mcp_webserver_base.tox`](../vendor/touchdesigner-mcp-td/mcp_webserver_base.tox) and drop it onto `/project1`. Rename the resulting COMP to `mcp_webserver_base` (the default).
4. Open the textport (`Dialogs → Textport and DATs`). You should see `HTTP server started` and no `[ERROR]` lines. The MCP bridge is now live on `127.0.0.1:9981`.
5. Save (`Ctrl/Cmd+S`).

After that, opening `main.toe` brings the MCP bridge up automatically.

## Verify

From a Claude Code session in this repo:

```
mcp__touchdesigner-stdio__get_td_info
```

Should return server info (version, project path), not a connection error.

## Notes

- The `.tox` is loaded externally — the .toe file just stores a path reference, so saving `main.toe` won't blow up to MBs.
- Don't move `vendor/touchdesigner-mcp-td/` — its internal structure (`import_modules.py` + `modules/` siblings) must stay intact for the .tox to find its Python modules.
- TD's auto-backups (`*.toe.N`, `Backup/`) are gitignored. Don't commit them.
