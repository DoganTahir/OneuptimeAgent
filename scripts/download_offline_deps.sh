#!/usr/bin/env bash
# Run on a machine WITH internet. Produces ./wheelhouse for offline install.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WHEELHOUSE="${ROOT}/wheelhouse"
REQ="${ROOT}/requirements.lock.txt"

mkdir -p "${WHEELHOUSE}"

echo "Downloading wheels into ${WHEELHOUSE}"
python3 -m pip download -r "${REQ}" -d "${WHEELHOUSE}"

# Extra Linux wheels (common offline servers). Pure-Python wheels are reused.
for py in 311 312 313; do
  for plat in manylinux2014_x86_64 manylinux2014_aarch64 manylinux_2_17_x86_64 manylinux_2_17_aarch64; do
    echo "Trying platform=${plat} python=${py}"
    python3 -m pip download -r "${REQ}" -d "${WHEELHOUSE}" \
      --platform "${plat}" \
      --python-version "${py}" \
      --implementation cp \
      --abi "cp${py}" \
      --only-binary=:all: \
      || true
  done
done

echo
echo "Wheelhouse ready: ${WHEELHOUSE}"
echo "Copy this folder + requirements.lock.txt to the offline server."
ls -lh "${WHEELHOUSE}" | tail -n +1
