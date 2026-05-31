#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$ROOT_DIR"

probe_python() {
  local candidate="$1"
  "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1 || return 1

  local probe_root
  probe_root="$(mktemp -d 2>/dev/null || mktemp -d -t xerox-python-probe)"
  if "$candidate" -m venv "$probe_root/venv" >/dev/null 2>&1; then
    rm -rf "$probe_root"
    return 0
  fi

  rm -rf "$probe_root"
  return 1
}

select_python() {
  local candidates=()

  if [[ -n "${XEROX_PYTHON:-}" ]]; then
    candidates+=("${XEROX_PYTHON}")
  fi

  candidates+=(python3.12 python3.11 python3.10 python3 python)

  local candidate
  for candidate in "${candidates[@]}"; do
    if ! command -v "$candidate" >/dev/null 2>&1; then
      continue
    fi
    if probe_python "$candidate"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  return 1
}

if ! PYTHON_BIN="$(select_python)"; then
  echo "No working Python 3.10+ interpreter with a usable venv module was found." >&2
  echo "Install Python 3.10+ and rerun ./install.sh." >&2
  echo "If you already have a specific interpreter, run: XEROX_PYTHON=/path/to/python3.12 ./install.sh" >&2
  exit 1
fi

"$PYTHON_BIN" "$ROOT_DIR/scripts/bootstrap.py" --shell unix --launcher "./xerox"
