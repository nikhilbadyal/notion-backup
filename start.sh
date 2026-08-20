#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"

cd "${SCRIPT_DIR}"

usage() {
    cat <<'EOF'
Usage: ./start.sh [OPTIONS] [COMMAND] [ARGS...]

Bootstrap the Python virtual environment and run the Notion Backup CLI.

Options:
  -h, --help    Show this help message and exit

Commands:
  env           Create/update the virtual environment and print the command
                to activate it. Run 'eval "$(./start.sh env)"' to activate
                the venv in your current shell, then run 'python3 main.py'.
  (none)        Create/update the virtual environment, then run 'main.py'
                with any remaining arguments.

Examples:
  ./start.sh
  ./start.sh --debug backup
  eval "$(./start.sh env)"
EOF
}

setup_env() {
    if [[ ! -d "${VENV_DIR}" ]]; then
        "${PYTHON_BIN}" -m venv "${VENV_DIR}" >&2
    fi
    "${VENV_DIR}/bin/python" -m pip install --upgrade pip >&2
    "${VENV_DIR}/bin/python" -m pip install -r requirements.txt >&2
    source "${VENV_DIR}/bin/activate"
}

case "${1:-}" in
    -h|--help)
        usage
        ;;
    env)
        setup_env
        echo "Virtual environment ready and activated at ${VENV_DIR}" >&2
        ;;
    *)
        setup_env
        exec "${VENV_DIR}/bin/python" main.py "$@"
        ;;
esac
