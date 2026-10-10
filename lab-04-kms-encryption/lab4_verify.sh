#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# CITS5503 Lab 4 - read-only rehearsal.
#
# Checks everything the four checkpoints need. It creates nothing, deletes
# nothing and changes nothing - every AWS call here is a describe/get/list.
# Run it before the demo; anything marked FAIL is something to fix first.
# ---------------------------------------------------------------------------
set -uo pipefail

pass=0
fail=0
ok()   { echo "  PASS  $*"; pass=$((pass + 1)); }
bad()  { echo "  FAIL  $*"; fail=$((fail + 1)); }
note() { echo "  ..    $*"; }

# Every value comes from ~/cits5503/lab.env - nothing is hardcoded here.
LAB_ENV="${LAB_ENV:-${HOME}/cits5503/lab.env}"

if [[ ! -f "${LAB_ENV}" ]]; then
  echo "ERROR: ${LAB_ENV} not found." >&2
  echo "       Run ./lab4_prepare.sh first - it populates the Lab 4 values." >&2
  exit 1
fi

# shellcheck disable=SC1090
source "${LAB_ENV}"

missing=0
for key in LAB_PREFIX AWS_REGION BUCKET_NAME KMS_ALIAS IAM_USERNAME ACCOUNT_ID ROOT_DIR; do
  if [[ -z "${!key:-}" ]]; then
    echo "ERROR: ${key} is not set in ${LAB_ENV}" >&2
    missing=1
  fi
done
if [[ ${missing} -eq 1 ]]; then
  echo "       Run ./lab4_prepare.sh to populate the Lab 4 values." >&2
  exit 1
fi

ALIAS_SHORT="${KMS_ALIAS#alias/}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=================================================="
echo "  Lab 4 verification (read-only)"
echo "  bucket ${BUCKET_NAME} | alias ${KMS_ALIAS}"
echo "=================================================="

# --- environment ------------------------------------------------------------
echo
echo "=== Environment ==="

if aws --version 2>&1 | grep -q 'aws-cli/2'; then
  ok "AWS CLI v2 ($(aws --version 2>&1))"
else
  bad "AWS CLI is not v2 ($(aws --version 2>&1 || echo 'not installed'))"
fi

CALLER_ARN="$(aws sts get-caller-identity --query Arn --output text 2>/dev/null)"
if [[ -z "${CALLER_ARN}" ]]; then
  bad "sts get-caller-identity failed - credentials expired, or WSL clock drift (try: sudo hwclock -s)"
else
  ok "Identity: ${CALLER_ARN}"
  if [[ "${CALLER_ARN}" == *":user/"* ]]; then
    LIVE_USER="${CALLER_ARN##*:user/}"
    LIVE_ACCOUNT="$(aws sts get-caller-identity --query Account --output text 2>/dev/null)"
    # lab.env is the source of truth; the live identity only has to agree.
    if [[ "${LIVE_USER}" == "${IAM_USERNAME}" && "${LIVE_ACCOUNT}" == "${ACCOUNT_ID}" ]]; then
      ok "Live identity matches lab.env (${ACCOUNT_ID} / ${IAM_USERNAME})"
    else
      bad "lab.env says ${ACCOUNT_ID}/${IAM_USERNAME} but credentials are ${LIVE_ACCOUNT}/${LIVE_USER} - re-run ./lab4_prepare.sh"
    fi
  else
    bad "Not an IAM user - aws:username will not exist and the bucket policy cannot work"
  fi
fi

REGION_CFG="$(aws configure get region 2>/dev/null)"
if [[ "${REGION_CFG}" == "${AWS_REGION}" ]]; then
  ok "Region: ${REGION_CFG}"
else
  bad "Region is '${REGION_CFG}', expected '${AWS_REGION}'"
fi

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
  if "${VENV}/bin/python3" -c 'import boto3, Crypto' 2>/dev/null; then
    ok "venv ${VENV} has boto3 and pycryptodome"
  else
    bad "venv ${VENV} is missing boto3 or pycryptodome - re-run ./lab4_prepare.sh"
  fi
else
  bad "no virtual environment found - re-run ./lab4_prepare.sh"
fi

# --- Checkpoint 1 -----------------------------------------------------------
echo
echo "=== Checkpoint 1: Bucket policy ==="

if aws s3api head-bucket --bucket "${BUCKET_NAME}" --region "${AWS_REGION}" >/dev/null 2>&1; then
  ok "Bucket ${BUCKET_NAME} exists and is reachable"
else
  bad "Bucket ${BUCKET_NAME} not reachable - run ./lab4_prepare.sh"
fi

POLICY="$(aws s3api get-bucket-policy --bucket "${BUCKET_NAME}" --region "${AWS_REGION}" --query Policy --output text 2>/dev/null)"
if [[ -z "${POLICY}" ]]; then
  bad "No bucket policy attached - run: python3 bucket_policy.py"
else
  ok "get-bucket-policy returns a policy"

  grep -q '"Deny"' <<<"${POLICY}" \
    && ok "Effect is Deny" \
    || bad "Policy has no Deny effect"

  grep -q "arn:aws:s3:::${BUCKET_NAME}\"" <<<"${POLICY}" \
    && ok "Resource covers the bucket itself" \
    || bad "Resource does not cover the bucket ARN - listing would not be denied"

  grep -q "arn:aws:s3:::${BUCKET_NAME}/\*" <<<"${POLICY}" \
    && ok "Resource covers the objects in the bucket" \
    || bad "Resource does not cover arn:aws:s3:::${BUCKET_NAME}/*"

  grep -q 'aws:username' <<<"${POLICY}" \
    && ok "Condition keys off aws:username" \
    || bad "No aws:username condition in the policy"

  grep -q "${IAM_USERNAME}" <<<"${POLICY}" \
    && ok "Condition names your own username (${IAM_USERNAME})" \
    || bad "Policy does not mention ${IAM_USERNAME} - you would lock yourself out"
fi

KEYS="$(aws s3api list-objects-v2 --bucket "${BUCKET_NAME}" --region "${AWS_REGION}" \
          --query 'Contents[].Key' --output text 2>/dev/null | tr '\t' '\n' | sort)"

DERIVED_RE='\.aes\.enc$|\.aes\.dec$|\.kms\.enc$|\.kms\.dec$'

# The expected originals come from the local tree lab4_prepare.sh built, not
# from a list written into this script - so a different tree still verifies.
EXPECTED=""
if [[ -d "${SCRIPT_DIR}/${ROOT_DIR}" ]]; then
  EXPECTED="$(cd "${SCRIPT_DIR}" && find "${ROOT_DIR}" -type f | grep -Ev "${DERIVED_RE}" | sort)"
fi
if [[ -z "${EXPECTED}" && -n "${KEYS}" ]]; then
  EXPECTED="$(grep -Ev "${DERIVED_RE}" <<<"${KEYS}")"
  note "no local ${ROOT_DIR}/ tree next to this script - using the bucket's own originals"
fi

if [[ -n "${KEYS}" ]]; then
  echo "  Objects in the bucket:"
  sed 's/^/          /' <<<"${KEYS}"
  if [[ -z "${EXPECTED}" ]]; then
    bad "Could not work out which objects are the originals"
  else
    while read -r base; do
      [[ -z "${base}" ]] && continue
      grep -qx "${base}" <<<"${KEYS}" \
        && ok "${base} present" \
        || bad "${base} missing from the bucket"
    done <<<"${EXPECTED}"
  fi
else
  bad "Bucket has no objects"
fi

note "In the console: open ${BUCKET_NAME}/${ROOT_DIR} yourself (works), then have a"
note "facilitator open it with their own account (AccessDenied)."

# --- Checkpoint 2 -----------------------------------------------------------
echo
echo "=== Checkpoint 2: KMS configuration ==="

ALIAS_TARGET="$(aws kms list-aliases --region "${AWS_REGION}" \
                  --query "Aliases[?AliasName=='${KMS_ALIAS}'].TargetKeyId | [0]" \
                  --output text 2>/dev/null)"

if [[ -z "${ALIAS_TARGET}" || "${ALIAS_TARGET}" == "None" ]]; then
  bad "No key behind ${KMS_ALIAS} - run: python3 kms_key.py"
  KEY_ID=""
else
  KEY_ID="${ALIAS_TARGET}"
  ok "Alias ${KMS_ALIAS} points at key ${KEY_ID}"

  # kms_key.py records the key it created; make sure the two still agree.
  if [[ -z "${KMS_KEY_ID:-}" ]]; then
    bad "KMS_KEY_ID is not recorded in ${LAB_ENV} - run: python3 kms_key.py"
  elif [[ "${KMS_KEY_ID}" == "${KEY_ID}" ]]; then
    ok "Matches KMS_KEY_ID in lab.env"
  else
    bad "lab.env records KMS_KEY_ID=${KMS_KEY_ID} but the alias points at ${KEY_ID}"
  fi

  KEY_STATE="$(aws kms describe-key --key-id "${KEY_ID}" --region "${AWS_REGION}" \
                 --query 'KeyMetadata.KeyState' --output text 2>/dev/null)"
  [[ "${KEY_STATE}" == "Enabled" ]] \
    && ok "Key state: Enabled" \
    || bad "Key state is ${KEY_STATE} (expected Enabled)"

  KEY_POLICY="$(aws kms get-key-policy --key-id "${KEY_ID}" --region "${AWS_REGION}" \
                  --policy-name default --query Policy --output text 2>/dev/null)"
  if [[ -z "${KEY_POLICY}" ]]; then
    bad "Could not read the key policy"
  else
    USER_ARN="arn:aws:iam::${ACCOUNT_ID}:user/${IAM_USERNAME}"
    # Parse the key policy properly rather than grepping, so the admin and user
    # statements are checked separately.
    RESULT="$(KEY_POLICY="${KEY_POLICY}" USER_ARN="${USER_ARN}" python3 <<'PYEOF' 2>/dev/null
import json, os

policy = json.loads(os.environ["KEY_POLICY"])
user_arn = os.environ["USER_ARN"]

def principals(stmt):
    p = stmt.get("Principal", {})
    aws = p.get("AWS", []) if isinstance(p, dict) else p
    return [aws] if isinstance(aws, str) else list(aws)

admin = usage = False
for stmt in policy.get("Statement", []):
    if stmt.get("Effect") != "Allow" or user_arn not in principals(stmt):
        continue
    actions = stmt.get("Action", [])
    actions = [actions] if isinstance(actions, str) else actions
    if any(a.startswith("kms:ScheduleKeyDeletion") or a.startswith("kms:Put")
           or a.startswith("kms:Disable") for a in actions):
        admin = True
    if any(a in ("kms:Encrypt", "kms:Decrypt") for a in actions):
        usage = True

print(f"{int(admin)} {int(usage)}")
PYEOF
)"
    ADMIN_OK="${RESULT%% *}"
    USER_OK="${RESULT##* }"
    [[ "${ADMIN_OK}" == "1" ]] \
      && ok "Key policy lists ${USER_ARN} as key administrator" \
      || bad "Key policy does not list you as key administrator"
    [[ "${USER_OK}" == "1" ]] \
      && ok "Key policy lists ${USER_ARN} as key user" \
      || bad "Key policy does not list you as key user"
  fi
fi

note "In the console: KMS, Customer managed keys, select alias '${ALIAS_SHORT}',"
note "open the Key policy tab and point out the two statements above."

# --- Checkpoint 3 -----------------------------------------------------------
echo
echo "=== Checkpoint 3: pycryptodome ==="

FE="${SCRIPT_DIR}/fileencrypt.py"
if [[ -f "${FE}" ]]; then
  ok "fileencrypt.py found at ${FE}"
  grep -q 'def encrypt_file' "${FE}" && grep -q 'def decrypt_file' "${FE}" \
    && ok "fileencrypt.py has the encrypt_file / decrypt_file pair to show" \
    || bad "fileencrypt.py is missing encrypt_file or decrypt_file"
else
  bad "fileencrypt.py not next to this script"
fi

if [[ -n "${KEYS}" && -n "${EXPECTED}" ]]; then
  while read -r base; do
    [[ -z "${base}" ]] && continue
    grep -qx "${base}" <<<"${KEYS}" \
      && ok "original ${base} in bucket" \
      || bad "original ${base} missing"
    grep -qx "${base}.aes.enc" <<<"${KEYS}" \
      && ok "encrypted ${base}.aes.enc in bucket" \
      || bad "encrypted ${base}.aes.enc missing - run: python3 fileencrypt.py"
    grep -qx "${base}.aes.dec" <<<"${KEYS}" \
      && ok "decrypted ${base}.aes.dec in bucket" \
      || bad "decrypted ${base}.aes.dec missing - run: python3 fileencrypt.py"
  done <<<"${EXPECTED}"
fi

note "During the demo: open fileencrypt.py at the CHECKPOINT 3 block, run it"
note "live until it finishes, then show the three object kinds in the console."

# --- Checkpoint 4 -----------------------------------------------------------
echo
echo "=== Checkpoint 4: Cleanup (console only, nothing done here) ==="
note "1. S3: empty ${BUCKET_NAME} first, then delete the bucket."
note "2. KMS: select the key aliased '${ALIAS_SHORT}' and schedule deletion."
note "   KMS keys cannot be deleted immediately - schedule 7 days."
note "DynamoDB Local runs on your laptop and is outside the cleanup check."

echo
echo "=================================================="
echo "  ${pass} passed, ${fail} failed"
echo "=================================================="
[[ ${fail} -eq 0 ]] || exit 1
