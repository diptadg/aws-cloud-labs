#!/usr/bin/env bash
# =============================================================================
# CITS5503 Lab 3 - Read-only verification
#
# Checks everything the checkpoints need, without creating, modifying or
# deleting a single thing. Safe to run as many times as you like.
# =============================================================================
set -uo pipefail

ENV_FILE="${LAB_ENV:-${HOME}/cits5503/lab.env}"
PORT="${DDB_PORT:-8000}"
ENDPOINT="http://localhost:${PORT}"
TABLE="${DDB_TABLE:-CloudFiles}"

pass=0; fail=0
ok()   { echo "  [ OK ] $*";   pass=$((pass+1)); }
bad()  { echo "  [FAIL] $*";   fail=$((fail+1)); }
note() { echo "  [note] $*"; }

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "ERROR: ${ENV_FILE} not found - it is created by the Lab 1 setup script."
  exit 1
fi
# shellcheck disable=SC1090
source "${ENV_FILE}"

for var in LAB_PREFIX AWS_REGION; do
  if [[ -z "${!var:-}" ]]; then
    echo "ERROR: ${var} is not set in ${ENV_FILE}."
    exit 1
  fi
done

BUCKET_NAME="${BUCKET_NAME:-${LAB_PREFIX}-cloudstorage}"

echo "=== Environment ==="
python3 -c "import boto3" 2>/dev/null \
  && ok "boto3 importable ($(python3 -c 'import boto3;print(boto3.__version__)'))" \
  || bad "boto3 not importable - did you run 'labenv'?"

aws --version 2>&1 | grep -q 'aws-cli/2' \
  && ok "AWS CLI v2 ($(aws --version 2>&1))" \
  || bad "AWS CLI is not v2"

BADOWN=$(find "${HOME}/cits5503" ! -user "${USER}" -printf '%p\n' 2>/dev/null | head -5)
if [[ -z "${BADOWN}" ]]; then
  ok "All files under ~/cits5503 owned by ${USER}"
else
  bad "Root-owned files found - run tools/lab_fixperms.sh from the repo"
  echo "${BADOWN}" | sed 's/^/         /'
fi

CALLER=$(aws sts get-caller-identity --query Arn --output text 2>/dev/null)
if [[ -n "${CALLER}" ]]; then
  ok "Identity: ${CALLER}"
else
  bad "sts get-caller-identity failed - creds expired, or WSL clock drift (try: sudo hwclock -s)"
fi

REGION_CFG=$(aws configure get region 2>/dev/null)
[[ "${REGION_CFG}" == "${AWS_REGION}" ]] \
  && ok "Region: ${REGION_CFG}" \
  || bad "Region is '${REGION_CFG}', expected '${AWS_REGION}'"

echo
echo "=== Checkpoint 1: S3 upload and traverse ==="
if [[ "${BUCKET_NAME}" == "${LAB_PREFIX}-cloudstorage" ]]; then
  ok "Bucket name follows <prefix>-cloudstorage: ${BUCKET_NAME}"
else
  bad "BUCKET_NAME in lab.env is '${BUCKET_NAME}', expected '${LAB_PREFIX}-cloudstorage'"
fi

if aws s3api head-bucket --bucket "${BUCKET_NAME}" >/dev/null 2>&1; then
  ok "Bucket exists and is reachable"
  KEYS=$(aws s3api list-objects-v2 --bucket "${BUCKET_NAME}" \
           --query 'Contents[].Key' --output text 2>/dev/null | tr '\t' '\n' | sort)
  echo "  Objects:"
  echo "${KEYS}" | sed 's/^/    /'
  echo "${KEYS}" | grep -qx 'rootdir/rootfile.txt' \
    && ok "rootdir/rootfile.txt present" \
    || bad "rootdir/rootfile.txt missing"
  echo "${KEYS}" | grep -qx 'rootdir/subdir/subfile.txt' \
    && ok "rootdir/subdir/subfile.txt present" \
    || bad "rootdir/subdir/subfile.txt missing"
else
  bad "Bucket ${BUCKET_NAME} not found - run cloudstorage.py"
fi

grep -q 'os.walk' cloudstorage.py 2>/dev/null \
  && ok "cloudstorage.py contains the os.walk traversal to show" \
  || note "cloudstorage.py not in the current directory - cd ~/cits5503/lab3"

echo
echo "=== Checkpoint 2: DynamoDB Local table ==="
if aws dynamodb list-tables --endpoint-url "${ENDPOINT}" >/dev/null 2>&1; then
  ok "DynamoDB Local answering on ${ENDPOINT}"

  if aws dynamodb describe-table --table-name "${TABLE}" --endpoint-url "${ENDPOINT}" \
       >/dev/null 2>&1; then
    ok "Table ${TABLE} exists"
    COUNT=$(aws dynamodb scan --table-name "${TABLE}" --endpoint-url "${ENDPOINT}" \
              --select COUNT --query Count --output text 2>/dev/null)
    [[ "${COUNT}" == "2" ]] \
      && ok "Scan returns ${COUNT} items" \
      || bad "Scan returns ${COUNT} items, expected 2"

    for attr in userId fileName path lastUpdated; do
      aws dynamodb scan --table-name "${TABLE}" --endpoint-url "${ENDPOINT}" \
        --query 'Items[0]' --output json 2>/dev/null | grep -q "\"${attr}\"" \
        && ok "attribute ${attr} present" \
        || bad "attribute ${attr} missing"
    done
  else
    bad "Table ${TABLE} not found - run write_to_dynamodb.py"
  fi
else
  bad "DynamoDB Local not reachable - ./dynamodb_local.sh start"
fi

echo
echo "=== Checkpoint 3: Cleanup readiness ==="
note "Bucket deletion is done in the AWS Management Console, not here."
note "S3 requires the bucket to be EMPTY before it can be deleted:"
note "  Console -> S3 -> ${BUCKET_NAME} -> Empty, then Delete."
note "DynamoDB Local runs on your laptop and is outside the cleanup check."

echo
echo "=================================================="
echo "  ${pass} passed, ${fail} failed"
echo "=================================================="
[[ ${fail} -eq 0 ]] || exit 1
