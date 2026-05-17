#!/usr/bin/env bash
# Fetch + extract the TouchDesigner MCP server artifact into vendor/.
# Idempotent — safe to re-run. Defaults to the upstream "latest" release.

set -euo pipefail

# Pin to a known-good upstream release. Bump deliberately when we want to update.
RELEASE_TAG="${TD_MCP_RELEASE:-latest}"
DEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/vendor/touchdesigner-mcp-td"
ZIP_URL_LATEST="https://github.com/8beeeaaat/touchdesigner-mcp/releases/latest/download/touchdesigner-mcp-td.zip"
ZIP_URL_TAGGED="https://github.com/8beeeaaat/touchdesigner-mcp/releases/download/${RELEASE_TAG}/touchdesigner-mcp-td.zip"

if [[ "${RELEASE_TAG}" == "latest" ]]; then
	ZIP_URL="${ZIP_URL_LATEST}"
else
	ZIP_URL="${ZIP_URL_TAGGED}"
fi

echo "==> setup-td-mcp"
echo "    release: ${RELEASE_TAG}"
echo "    dest:    ${DEST_DIR}"

if [[ -f "${DEST_DIR}/mcp_webserver_base.tox" ]]; then
	echo "==> already installed at ${DEST_DIR}"
	echo "    re-running anyway to ensure a clean state (Ctrl-C to abort)"
	sleep 1
fi

rm -rf "${DEST_DIR}"
mkdir -p "${DEST_DIR}"

TMP_ZIP="$(mktemp -t touchdesigner-mcp-td.XXXXX.zip)"
trap 'rm -f "${TMP_ZIP}"' EXIT

echo "==> downloading ${ZIP_URL}"
curl -fSL -o "${TMP_ZIP}" "${ZIP_URL}"

echo "==> extracting"
unzip -o -q "${TMP_ZIP}" -d "${DEST_DIR}"

if [[ ! -f "${DEST_DIR}/mcp_webserver_base.tox" ]]; then
	echo "ERROR: expected ${DEST_DIR}/mcp_webserver_base.tox to exist after extract" >&2
	exit 1
fi

echo
echo "==> done."
echo "    .tox path (drag this into TD's /project1):"
echo "    ${DEST_DIR}/mcp_webserver_base.tox"
echo
echo "    next steps:"
echo "      1. open td/main.toe in TouchDesigner (or create one and save it there)"
echo "      2. drag the .tox above onto /project1, name it 'mcp_webserver_base'"
echo "      3. open the textport (Dialogs > Textport and DATs); look for 'HTTP server started'"
echo "      4. save"
echo "      5. wire Claude Code to the MCP server (one-time, per machine):"
echo "         claude mcp add -s user touchdesigner -- npx -y touchdesigner-mcp-server@latest --stdio"
