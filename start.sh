#!/usr/bin/env bash

# Exit immediately if a command exits with a non-zero status, treat unset variables
# as an error, and ensure pipeline failures propagate to prevent silent errors.
set -euo pipefail

# Determine repository root directory relative to the script location so that the
# script can be run from any working directory reliably.
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

Environment Variables:
  PYTHON_BIN    Python executable to use for creating venv (default: python3)
  UPDATE_DEPS   Set to 1 to force reinstalling/updating dependencies

Examples:
  ./start.sh
  ./start.sh --debug backup
  eval "$(./start.sh env)"
  UPDATE_DEPS=1 ./start.sh
EOF
}

setup_env() {
    # Check for an executable python binary inside the venv rather than just directory existence.
    # This ensures partially created or corrupted environments without a python binary are properly recreated.
    if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
        echo "Creating virtual environment at ${VENV_DIR} using ${PYTHON_BIN}..." >&2
        "${PYTHON_BIN}" -m venv "${VENV_DIR}" >&2
    fi

    # Track requirements installation with a stamp file to avoid running pip on every launch.
    # Reinstall occurs when the stamp file is missing, requirements.txt is newer, or forced via env var.
    local stamp_file="${VENV_DIR}/.requirements_installed"
    if [[ ! -f "${stamp_file}" || ( -f requirements.txt && requirements.txt -nt "${stamp_file}" ) || "${UPDATE_DEPS:-0}" == "1" || "${REINSTALL_DEPS:-0}" == "1" ]]; then
        echo "Installing or updating dependencies in virtual environment..." >&2
        "${VENV_DIR}/bin/python" -m pip install --upgrade pip >&2
        "${VENV_DIR}/bin/python" -m pip install -r requirements.txt >&2
        touch "${stamp_file}"
    fi

    # Source activation script to set up environment variables (such as PATH and VIRTUAL_ENV)
    # for any subshells or subprocesses in this session.
    # shellcheck source=/dev/null
    source "${VENV_DIR}/bin/activate"
}

case "${1:-}" in
    -h|--help)
        usage
        ;;
    env)
        setup_env
        # Informational message is routed to stderr so stdout remains clean
        # for eval consumption by the calling shell.
        echo "Virtual environment ready at ${VENV_DIR}" >&2
        # Print the shell activation command to stdout so that running
        # eval "$(./start.sh env)" properly activates the venv in the caller's shell.
        printf 'source %q\n' "${VENV_DIR}/bin/activate"
        ;;
    *)
        setup_env
        # Replace the current script process with Python to handle signals cleanly
        # and avoid unnecessary parent shell processes hanging around.
        exec "${VENV_DIR}/bin/python" main.py "$@"
        ;;
esac
