#!/usr/bin/env bash
# =============================================================================
# CITS5503 Lab 3 - Preparation
#
# Creates the local rootdir/ tree required by the lab, makes sure the shared
# venv exists with boto3 available, and records the bucket name in lab.env.
#
# Run from ~/cits5503/lab3   (NOT with sudo - see README)
# =============================================================================
set -euo pipefail

ENV_FILE="${LAB_ENV:-${HOME}/cits5503/lab.env}"
VENV_DIR="${HOME}/cits5503/.venv"
LAB_DIR="${HOME}/cits5503/lab3"

# --- guard: never run this as root -------------------------------------------
if [[ ${EUID} -eq 0 ]]; then
  echo "ERROR: do not run this with sudo. It would make ~/cits5503 root-owned"
  echo "       and break the venv. If that already happened:"
  echo "       sudo chown -R \$USER:\$USER ~/cits5503"
  exit 1
fi

# --- self-heal: repair anything a previous sudo run left root-owned -----------
# Only touches paths on the Linux filesystem. ~/.aws is a symlink into
# /mnt/c/... where chown is a no-op under DrvFs, so it is skipped.
if find "${HOME}/cits5503" -maxdepth 2 ! -user "${USER}" -print -quit 2>/dev/null | grep -q .; then
  echo "Found root-owned files under ~/cits5503 - repairing ownership ..."
  sudo chown -R "${USER}:${USER}" "${HOME}/cits5503"
  echo "Ownership repaired."
fi

# --- load lab.env (written by the Lab 1 setup script - read only here) --------
if [[ ! -f "${ENV_FILE}" ]]; then
  echo "ERROR: ${ENV_FILE} not found."
  echo "       It is created by the Lab 1 setup script. Run that first rather"
  echo "       than recreating it here, so every lab reads the same values."
  exit 1
fi
# shellcheck disable=SC1090
source "${ENV_FILE}"

for var in LAB_PREFIX AWS_REGION; do
  if [[ -z "${!var:-}" ]]; then
    echo "ERROR: ${var} is not set in ${ENV_FILE}."
    echo "       Add it there (Lab 1 owns that file) and re-run."
    exit 1
  fi
done

# Lab 3's own key. LAB_PREFIX and AWS_REGION are read-only here (Lab 1
# owns those), but BUCKET_NAME is created by this lab, so it gets recorded in
# lab.env for the other Lab 3 scripts to pick up.
BUCKET_NAME="${LAB_PREFIX}-cloudstorage"

if grep -q '^BUCKET_NAME=' "${ENV_FILE}"; then
  sed -i "s|^BUCKET_NAME=.*|BUCKET_NAME=${BUCKET_NAME}|" "${ENV_FILE}"
else
  echo "BUCKET_NAME=${BUCKET_NAME}" >> "${ENV_FILE}"
fi

echo "lab.env  -> LAB_PREFIX=${LAB_PREFIX}  AWS_REGION=${AWS_REGION}"
echo "lab.env  -> BUCKET_NAME=${BUCKET_NAME}  (written by this script)"

# --- venv (shared across all labs) ---------------------------------------------
if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  echo "Creating venv at ${VENV_DIR} ..."
  python3 -m venv "${VENV_DIR}"
fi
# Ubuntu 26.04 is PEP 668 managed: always use the venv's pip, never system pip3.
"${VENV_DIR}/bin/pip" install --quiet --upgrade pip boto3
echo "venv     -> $("${VENV_DIR}/bin/python" -c 'import boto3,sys; print("python", sys.version.split()[0], "| boto3", boto3.__version__)')"

# --- the local directory tree the lab asks for ---------------------------------
mkdir -p "${LAB_DIR}/rootdir/subdir"
printf '1\n2\n3\n4\n5\n' > "${LAB_DIR}/rootdir/rootfile.txt"
printf '1\n2\n3\n4\n5\n' > "${LAB_DIR}/rootdir/subdir/subfile.txt"

echo
echo "Local tree under ${LAB_DIR}:"
if command -v tree >/dev/null 2>&1; then
  tree "${LAB_DIR}/rootdir"
else
  find "${LAB_DIR}/rootdir" | sed "s|${LAB_DIR}/||"
fi

echo
echo "Next:"
echo "  labenv                       # activate the venv"
echo "  cd ${LAB_DIR}"
echo "  python3 cloudstorage.py"
