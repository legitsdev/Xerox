#!/bin/bash
set -euo pipefail

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$ROOT_DIR"

if ! ./install.sh; then
  echo
  read -r -p "Press Enter to close..." _
  exit 1
fi
