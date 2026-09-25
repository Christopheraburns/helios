#!/usr/bin/env bash
#
# Restore Node.js/npm access after a fresh Cloudera AI session.
# Source this file so PATH is updated in the current shell:
#   source ./setup-node.sh

set -euo pipefail

NODE_HOME="${NODE_HOME:-/home/cdsw/node-22.14.0}"
USER_BIN="${HOME}/.local/bin"

find_first_file() {
  local candidate
  for candidate in "$@"; do
    if [[ -f "${candidate}" ]]; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done
  return 1
}

NODE_EXECUTABLE="$(
  find_first_file \
    "${NODE_HOME}/bin/node" \
    "${NODE_HOME}/node"
)" || {
  echo "Node executable not found under ${NODE_HOME}." >&2
  echo "Set NODE_HOME to the extracted Node.js directory and try again." >&2
  return 1 2>/dev/null || exit 1
}

NPM_CLI="$(
  find_first_file \
    "${NODE_HOME}/lib/node_modules/npm/bin/npm-cli.js" \
    "${NODE_HOME}/node_modules/npm/bin/npm-cli.js" \
    "${NODE_HOME}/npm/bin/npm-cli.js" \
    "${NODE_HOME}/bin/npm-cli.js" \
    "${NODE_HOME}/npm-cli.js"
)" || {
  echo "npm-cli.js not found under ${NODE_HOME}." >&2
  return 1 2>/dev/null || exit 1
}

NPX_CLI="$(
  find_first_file \
    "${NODE_HOME}/lib/node_modules/npm/bin/npx-cli.js" \
    "${NODE_HOME}/node_modules/npm/bin/npx-cli.js" \
    "${NODE_HOME}/npm/bin/npx-cli.js" \
    "${NODE_HOME}/bin/npx-cli.js" \
    "${NODE_HOME}/npx-cli.js"
)" || true

mkdir -p "${USER_BIN}"

cat >"${USER_BIN}/node" <<EOF
#!/usr/bin/env bash
exec "${NODE_EXECUTABLE}" "\$@"
EOF

cat >"${USER_BIN}/npm" <<EOF
#!/usr/bin/env bash
exec "${NODE_EXECUTABLE}" "${NPM_CLI}" "\$@"
EOF

chmod u+x "${USER_BIN}/node" "${USER_BIN}/npm"

if [[ -n "${NPX_CLI}" ]]; then
  cat >"${USER_BIN}/npx" <<EOF
#!/usr/bin/env bash
exec "${NODE_EXECUTABLE}" "${NPX_CLI}" "\$@"
EOF
  chmod u+x "${USER_BIN}/npx"
fi

PATH_LINE='export PATH="$HOME/.local/bin:$PATH"'
if [[ ! -f "${HOME}/.bashrc" ]] || ! grep -Fqx "${PATH_LINE}" "${HOME}/.bashrc"; then
  printf '\n# Helios Node.js/npm setup\n%s\n' "${PATH_LINE}" >>"${HOME}/.bashrc"
fi

export PATH="${USER_BIN}:${PATH}"
hash -r

echo "Node.js and npm are ready:"
node --version
npm --version
