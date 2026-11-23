#!/usr/bin/env bash
# Run on the OFFLINE server (no PyPI access required).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WHEELHOUSE="${ROOT}/wheelhouse"
REQ="${ROOT}/requirements.lock.txt"
VENV="${ROOT}/.venv"

if [[ ! -d "${WHEELHOUSE}" ]]; then
  echo "ERROR: ${WHEELHOUSE} not found. Copy the wheelhouse folder to this host first." >&2
  exit 1
fi

if [[ ! -f "${REQ}" ]]; then
  echo "ERROR: ${REQ} not found." >&2
  exit 1
fi

python3 -m venv "${VENV}"
# shellcheck disable=SC1091
source "${VENV}/bin/activate"

python -m pip install --upgrade pip --no-index --find-links="${WHEELHOUSE}" || \
  python -m pip install --upgrade pip

python -m pip install --no-index --find-links="${WHEELHOUSE}" -r "${REQ}"

echo
echo "Offline install complete."
echo "Activate and run:"
echo "  source ${VENV}/bin/activate"
echo "  python -m src.main"
