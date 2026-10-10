#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# CITS5503 Lab 4 - preparation
#
# Gets the machine ready for Lab 4:
#   * finds the shared virtual environment and installs boto3 + pycryptodome
#   * rebuilds the local rootdir/ tree (rootfile.txt + subdir/subfile.txt)
#   * makes sure the S3 bucket <prefix>-cloudstorage exists and holds
#     the same contents as the Lab 3 bucket
#   * records the Lab 4 variables in ~/cits5503/lab.env
#
# Run this WITHOUT sudo. Running lab scripts under sudo makes files root-owned
# and then the venv and ~/.aws stop working for your normal user.
# ---------------------------------------------------------------------------
set -euo pipefail

if [[ ${EUID} -eq 0 ]]; then
  echo "ERROR: do not run this with sudo. Run it as your normal user." >&2
  exit 1
fi

LAB_ENV="${LAB_ENV:-${HOME}/cits5503/lab.env}"
LAB_DIR="${HOME}/cits5503/lab4"

# --- load the shared lab environment ---------------------------------------
# Every value comes from ~/cits5503/lab.env - nothing is hardcoded here. This
# script is the only one allowed to WRITE to lab.env; the rest read it and
# refuse to run if something is missing.
if [[ ! -f "${LAB_ENV}" ]]; then
  cat >&2 <<EOF
ERROR: ${LAB_ENV} not found.

It is created by the Lab 1 setup script and carries the values every lab
shares. If it has been lost, recreate it with at least:

  mkdir -p ~/cits5503
  cat > ${LAB_ENV} <<'VARS'
LAB_PREFIX="<your resource-name prefix>"
AWS_REGION="ap-southeast-2"
VARS
EOF
  exit 1
fi

# shellcheck disable=SC1090
source "${LAB_ENV}"

require_var() {
  local key="$1"
  if [[ -z "${!key:-}" ]]; then
    echo "ERROR: ${key} is not set in ${LAB_ENV}" >&2
    echo "       Add it there, then re-run this script." >&2
    exit 1
  fi
}

# These two must already exist - they come from Lab 1 and this script has no
# business guessing them.
require_var LAB_PREFIX
require_var AWS_REGION

# These two are Lab 4's own. If lab.env does not carry them yet, derive them
# once here and write them back, so every later script can just read them.
if [[ -z "${BUCKET_NAME:-}" ]]; then
  BUCKET_NAME="${LAB_PREFIX}-cloudstorage"
  echo "[env]  BUCKET_NAME not in lab.env - deriving ${BUCKET_NAME}"
fi
if [[ -z "${KMS_ALIAS:-}" ]]; then
  KMS_ALIAS="alias/${LAB_PREFIX}"
  echo "[env]  KMS_ALIAS not in lab.env - deriving ${KMS_ALIAS}"
fi
if [[ -z "${ROOT_DIR:-}" ]]; then
  ROOT_DIR="rootdir"
  echo "[env]  ROOT_DIR not in lab.env - deriving ${ROOT_DIR}"
fi

echo "=================================================="
echo "  Lab 4 preparation"
echo "  prefix  : ${LAB_PREFIX}"
echo "  region  : ${AWS_REGION}"
echo "  bucket  : ${BUCKET_NAME}"
echo "  alias   : ${KMS_ALIAS}"
echo "=================================================="

# --- [1] locate the virtual environment ------------------------------------
find_venv() {
  local c
  for c in "${VIRTUAL_ENV:-}" \
           "${HOME}/cits5503/labenv" "${HOME}/cits5503/.venv" "${HOME}/cits5503/venv" \
           "${HOME}/labenv" "${HOME}/.venv" "${HOME}/venv"; do
    [[ -n "${c}" && -x "${c}/bin/python3" ]] && { echo "${c}"; return 0; }
  done
  return 1
}

if VENV="$(find_venv)"; then
  echo "[venv] using ${VENV}"
else
  VENV="${HOME}/cits5503/.venv"
  echo "[venv] none found - creating ${VENV}"
  mkdir -p "${HOME}/cits5503"
  python3 -m venv "${VENV}"
fi

VPY="${VENV}/bin/python3"
VPIP="${VENV}/bin/pip"

# Ubuntu 26.04 blocks system-wide pip (PEP 668), so everything stays in the venv.
echo "[deps] installing boto3 and pycryptodome into the venv ..."
"${VPIP}" install --quiet --upgrade pip
"${VPIP}" install --quiet boto3 pycryptodome
echo "[deps] $("${VPY}" -c 'import boto3, Crypto; print("boto3", boto3.__version__, "| pycryptodome", Crypto.__version__)')"

# --- [2] AWS sanity ---------------------------------------------------------
if ! command -v aws >/dev/null 2>&1; then
  echo "ERROR: aws CLI not on PATH. Re-run the Lab 1 setup script." >&2
  exit 1
fi

if ! CALLER_ARN="$(aws sts get-caller-identity --query Arn --output text 2>/dev/null)"; then
  echo "ERROR: aws sts get-caller-identity failed." >&2
  echo "       Credentials may be expired, or the WSL clock may have drifted" >&2
  echo "       after a Windows sleep. Try: sudo hwclock -s" >&2
  exit 1
fi
echo "[aws]  identity: ${CALLER_ARN}"

case "${CALLER_ARN}" in
  *":user/"*) IAM_USERNAME="${CALLER_ARN##*:user/}" ;;
  *) echo "ERROR: you are not signed in as an IAM user." >&2
     echo "       The Lab 4 bucket policy keys off aws:username, which only" >&2
     echo "       exists for IAM users. Configure your IAM user credentials." >&2
     exit 1 ;;
esac
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
echo "[aws]  iam user: ${IAM_USERNAME}"
echo "[aws]  account : ${ACCOUNT_ID}"

# --- [3] local tree ---------------------------------------------------------
mkdir -p "${LAB_DIR}"
cd "${LAB_DIR}"

if [[ -d "${HOME}/cits5503/lab3/${ROOT_DIR}" && ! -d "${LAB_DIR}/${ROOT_DIR}" ]]; then
  echo "[tree] copying ${ROOT_DIR}/ across from lab3"
  cp -r "${HOME}/cits5503/lab3/${ROOT_DIR}" "${LAB_DIR}/${ROOT_DIR}"
fi

# The tree and the file contents are fixed by the Lab 3 specification, so they
# are literals rather than configuration.
mkdir -p "${ROOT_DIR}/subdir"
[[ -s "${ROOT_DIR}/rootfile.txt" ]]       || printf '1\n2\n3\n4\n5\n' > "${ROOT_DIR}/rootfile.txt"
[[ -s "${ROOT_DIR}/subdir/subfile.txt" ]] || printf '1\n2\n3\n4\n5\n' > "${ROOT_DIR}/subdir/subfile.txt"
echo "[tree] local tree ready:"
find "${ROOT_DIR}" -type f | sed 's/^/         /'

# --- [4] make sure the bucket exists and holds the originals ---------------
if aws s3api head-bucket --bucket "${BUCKET_NAME}" --region "${AWS_REGION}" >/dev/null 2>&1; then
  echo "[s3]   bucket ${BUCKET_NAME} already exists - reusing it"
else
  echo "[s3]   creating bucket ${BUCKET_NAME} in ${AWS_REGION}"
  if [[ "${AWS_REGION}" == "us-east-1" ]]; then
    # us-east-1 is the one region that must NOT be given a LocationConstraint.
    aws s3api create-bucket --bucket "${BUCKET_NAME}" --region "${AWS_REGION}" >/dev/null
  else
    aws s3api create-bucket \
      --bucket "${BUCKET_NAME}" \
      --region "${AWS_REGION}" \
      --create-bucket-configuration "LocationConstraint=${AWS_REGION}" >/dev/null
  fi
  aws s3api wait bucket-exists --bucket "${BUCKET_NAME}" --region "${AWS_REGION}"
fi

echo "[s3]   uploading the original ${ROOT_DIR}/ tree"
aws s3 cp "${ROOT_DIR}" "s3://${BUCKET_NAME}/${ROOT_DIR}" --recursive --only-show-errors \
  --region "${AWS_REGION}" \
  --exclude "*.aes.enc" --exclude "*.aes.dec" \
  --exclude "*.kms.enc" --exclude "*.kms.dec"

echo "[s3]   objects now in the bucket:"
aws s3api list-objects-v2 --bucket "${BUCKET_NAME}" --region "${AWS_REGION}" \
  --query 'Contents[].Key' --output text 2>/dev/null | tr '\t' '\n' | sed 's/^/         /'

# --- [5] record everything in lab.env --------------------------------------
touch "${LAB_ENV}"
set_var() {
  local key="$1" val="$2"
  if grep -q "^${key}=" "${LAB_ENV}"; then
    sed -i "s|^${key}=.*|${key}=\"${val}\"|" "${LAB_ENV}"
  else
    echo "${key}=\"${val}\"" >> "${LAB_ENV}"
  fi
}
set_var LAB_PREFIX "${LAB_PREFIX}"
set_var AWS_REGION     "${AWS_REGION}"
set_var BUCKET_NAME    "${BUCKET_NAME}"
set_var KMS_ALIAS      "${KMS_ALIAS}"
set_var ROOT_DIR       "${ROOT_DIR}"
set_var IAM_USERNAME   "${IAM_USERNAME}"
set_var ACCOUNT_ID     "${ACCOUNT_ID}"

echo
echo "lab.env updated:"
sed 's/^/  /' "${LAB_ENV}"

cat <<EOF

Preparation done. Next:

  labenv                          # activate the shared venv
  python3 bucket_policy.py        # apply the deny-everyone-but-me policy
  ./lab4_check_policy.sh          # read the policy back with the CLI
  python3 kms_key.py              # create the KMS key + alias + key policy
  python3 kms_encrypt.py          # encrypt/decrypt each file with KMS
  python3 fileencrypt.py          # encrypt/decrypt with pycryptodome, then upload
  ./lab4_verify.sh                # read-only rehearsal of every checkpoint
EOF
