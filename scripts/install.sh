#!/usr/bin/env bash
# Bootstrap Muninn: check Python, create venv, install, pull embedding model.
# Usage: ./scripts/install.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
_ORIG_DIR="$PWD"
cd "$PROJECT_ROOT"
trap 'cd "$_ORIG_DIR"' EXIT

VENV="$PROJECT_ROOT/.venv"
MIN_PY="3.11"

# ---- Find Python >= 3.11 ----
find_python() {
  for cmd in python3.14 python3.13 python3.12 python3.11 python3 python; do
    if command -v "$cmd" >/dev/null 2>&1; then
      local ver
      ver=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null) || continue
      local major minor
      major=${ver%%.*}
      minor=${ver#*.}
      if [ "$major" -ge 3 ] && [ "$minor" -ge 11 ]; then
        echo "$cmd"
        return
      fi
    fi
  done
  return 1
}

PYTHON=$(find_python) || {
  echo "ERROR: Python >= $MIN_PY required. Found none."
  echo "  Install: brew install python@3.12  (macOS)"
  echo "           sudo apt install python3.12  (Ubuntu)"
  exit 1
}
echo "==> Python: $($PYTHON --version)"

# ---- Create venv ----
if [ ! -d "$VENV" ]; then
  echo "==> Creating virtualenv at .venv/"
  "$PYTHON" -m venv "$VENV"
fi

echo "==> Installing Muninn"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -e ".[all]"

# ---- Verify CLI works ----
if "$VENV/bin/muninn" --version >/dev/null 2>&1; then
  echo "==> $(${VENV}/bin/muninn --version)"
else
  echo "ERROR: muninn CLI failed to install"
  exit 1
fi

# ---- Pull embedding model if Ollama is running ----
if command -v ollama >/dev/null 2>&1; then
  echo "==> Pulling embedding model (mxbai-embed-large)"
  ollama pull mxbai-embed-large 2>/dev/null || true
fi

# ---- Make runner scripts executable ----
chmod +x "$SCRIPT_DIR"/run_*.sh 2>/dev/null || true

echo ""
echo "Done. Next steps:"
echo ""
echo "  # Activate the venv (or use .venv/bin/muninn directly)"
echo "  source .venv/bin/activate"
echo ""
echo "  # Pull an LLM"
echo "  ollama pull gemma4:e4b"
echo ""
echo "  # Try the demo vault"
echo "  muninn setup-obsidian Muninn-Demo"
echo "  muninn serve"
echo ""
echo "  # Or set up your own"
echo "  muninn init"
