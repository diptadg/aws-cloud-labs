#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# CITS5503 Lab 4 - display the policy attached to the S3 bucket.
#
# This is the exact command from Checkpoint 1, plus a readable pretty-print
# and a quick sanity read of what the policy actually covers.
# ---------------------------------------------------------------------------
set -euo pipefail

# Every value comes from ~/cits5503/lab.env - nothing is hardcoded here.
LAB_ENV="${LAB_ENV:-${HOME}/cits5503/lab.env}"

if [[ ! -f "${LAB_ENV}" ]]; then
  echo "ERROR: ${LAB_ENV} not found." >&2
  echo "       Run ./lab4_prepare.sh first - it populates the Lab 4 values." >&2
  exit 1
fi

# shellcheck disable=SC1090
source "${LAB_ENV}"

require_var() {
  local key="$1"
  if [[ -z "${!key:-}" ]]; then
    echo "ERROR: ${key} is not set in ${LAB_ENV}" >&2
    echo "       Run ./lab4_prepare.sh to populate the Lab 4 values." >&2
    exit 1
  fi
}
require_var BUCKET_NAME
require_var AWS_REGION
require_var ROOT_DIR

echo "=== The Checkpoint 1 command ==="
echo "aws s3api get-bucket-policy --bucket ${BUCKET_NAME} --query Policy --output text"
echo

if ! POLICY="$(aws s3api get-bucket-policy \
                 --bucket "${BUCKET_NAME}" \
                 --region "${AWS_REGION}" \
                 --query Policy \
                 --output text 2>/dev/null)"; then
  echo "No policy came back for ${BUCKET_NAME}."
  echo "Apply it first:  python3 bucket_policy.py"
  exit 1
fi

echo "${POLICY}"
echo
echo "=== Same policy, pretty-printed ==="
if command -v python3 >/dev/null 2>&1; then
  echo "${POLICY}" | python3 -m json.tool
fi

echo
echo "=== Quick read ==="
grep -q '"Deny"' <<<"${POLICY}" \
  && echo "  Effect is Deny" \
  || echo "  WARNING: no Deny effect found"
grep -q "arn:aws:s3:::${BUCKET_NAME}\"" <<<"${POLICY}" \
  && echo "  Covers the bucket itself" \
  || echo "  WARNING: bucket ARN not covered - listing would not be denied"
grep -q "arn:aws:s3:::${BUCKET_NAME}/\*" <<<"${POLICY}" \
  && echo "  Covers the objects inside the bucket" \
  || echo "  WARNING: object ARN not covered"
grep -q 'aws:username' <<<"${POLICY}" \
  && echo "  Condition keys off aws:username" \
  || echo "  WARNING: no aws:username condition"

cat <<EOF

Also part of Checkpoint 1, done in the browser:
  1. AWS S3 console, signed in as your own account: open ${BUCKET_NAME}/${ROOT_DIR}
     and confirm you can read it.
  2. A facilitator signs in with their own AWS account and opens the same
     ${ROOT_DIR} folder - they should see AccessDenied.
EOF
