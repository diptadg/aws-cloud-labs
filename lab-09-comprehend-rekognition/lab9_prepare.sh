#!/usr/bin/env bash
# CITS5503 Lab 9 - local environment check and lab.env seeding.
#
# Run this once before anything else. It:
#   1. checks the AWS CLI, boto3 and your credentials
#   2. checks that Comprehend and Rekognition are available in your region
#   3. checks WSL2's usual breakages (clock drift, DNS)
#   4. writes the Lab 9 keys into ~/cits5503/lab.env
#
# It is the only Lab 9 script that writes name/config keys. Every other script
# reads them. LAB_PREFIX and AWS_REGION come from Lab 1 and are never
# rewritten here; AWS_ACCOUNT_ID is only written if no earlier lab recorded it.
#
#   ./lab9_prepare.sh

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lab9_lib.sh
source "${HERE}/lab9_lib.sh"

lab_env_load
lab_env_require LAB_PREFIX AWS_REGION

ok=0; warn=0; bad=0
pass() { printf '  [ ok ] %s\n' "$*"; ok=$((ok+1)); }
note() { printf '  [warn] %s\n' "$*"; warn=$((warn+1)); }
fail() { printf '  [FAIL] %s\n' "$*"; bad=$((bad+1)); }

echo "=============================================================="
echo " CITS5503 Lab 9 - preparation"
echo "=============================================================="
echo

# --------------------------------------------------------------- 1. AWS CLI
echo "1. AWS CLI"
if command -v aws >/dev/null 2>&1; then
  aws_ver="$(aws --version 2>&1 | head -1)"
  if [[ "${aws_ver}" == aws-cli/2* ]]; then
    pass "${aws_ver}"
  else
    fail "${aws_ver} - this is v1."
    echo "         'apt install awscli' gives v1. Install v2 from the AWS zip installer."
  fi
else
  fail "aws not found. Install AWS CLI v2."
fi
echo

# ----------------------------------------------------------- 2. Python tooling
echo "2. Python tooling"
if python3 -c "import boto3" >/dev/null 2>&1; then
  pass "boto3 $(python3 -c 'import boto3;print(boto3.__version__)')"
else
  fail "boto3 not importable. Activate the shared lab venv first:  labenv"
  echo "         A bare 'pip3 install' fails on Ubuntu 26.04 (PEP 668) - use the venv."
fi

# Nothing beyond boto3 is required. Babel or pycountry, if present, give
# lab9_languages.py a fuller language table than its built-in one.
if python3 -c "import babel" >/dev/null 2>&1; then
  pass "babel available - language names come from CLDR"
elif python3 -c "import pycountry" >/dev/null 2>&1; then
  pass "pycountry available - language names come from ISO 639"
else
  pass "using the built-in language table (neither babel nor pycountry needed)"
fi
echo

# ------------------------------------------------------- 3. AWS credentials
echo "3. AWS credentials"
account_id=""
if command -v aws >/dev/null 2>&1 && \
   caller="$(aws sts get-caller-identity --region "${AWS_REGION}" --output text \
             --query 'Account,Arn' 2>/dev/null)"; then
  account_id="$(awk '{print $1}' <<<"${caller}")"
  pass "authenticated as $(awk '{print $2}' <<<"${caller}")"
  pass "account ${account_id}"
else
  fail "aws sts get-caller-identity failed."
  echo "         If the error mentions a signature or the request time, WSL2's clock"
  echo "         has drifted after a Windows sleep. Fix it with:  sudo hwclock -s"
fi
echo

# --------------------------------------------- 4. service availability by region
echo "4. Comprehend and Rekognition in ${AWS_REGION}"

# botocore ships the endpoint table locally, so this needs no API call and no
# permissions - it just reads what regions each service has an endpoint in.
for service in comprehend rekognition; do
  if python3 - "${service}" "${AWS_REGION}" <<'PY' 2>/dev/null
import sys
import botocore.session
service, region = sys.argv[1], sys.argv[2]
regions = botocore.session.get_session().get_available_regions(service)
sys.exit(0 if region in regions else 1)
PY
  then
    pass "${service} has an endpoint in ${AWS_REGION}"
  else
    fail "${service} has no endpoint in ${AWS_REGION}"
    echo "         Use ap-southeast-2 for this lab instead."
  fi
done

# The lab sheet's own caveat: these two regions have endpoints but do not carry
# the full feature set, so a call can fail even though the endpoint resolves.
case "${AWS_REGION}" in
  eu-north-1|ca-central-1)
    note "${AWS_REGION} does not fully support Comprehend and Rekognition."
    echo "         The lab sheet says to use ap-southeast-2 instead. Change"
    echo "         AWS_REGION in ${LAB_ENV_FILE} before continuing."
    ;;
esac

# A live call is the only real proof the permissions are there.
if command -v aws >/dev/null 2>&1; then
  if aws comprehend detect-dominant-language --text "permission check" \
       --region "${AWS_REGION}" >/dev/null 2>&1; then
    pass "comprehend:DetectDominantLanguage permitted"
  else
    fail "comprehend:DetectDominantLanguage was refused."
    echo "         Check the permissions attached to your lab credentials."
  fi
fi
echo

# --------------------------------------------------------- 5. WSL2 specifics
echo "5. WSL2 checks"
if grep -qi microsoft /proc/version 2>/dev/null; then
  pass "running under WSL2"

  if getent hosts "comprehend.${AWS_REGION}.amazonaws.com" >/dev/null 2>&1; then
    pass "DNS resolves comprehend.${AWS_REGION}.amazonaws.com"
  else
    fail "cannot resolve comprehend.${AWS_REGION}.amazonaws.com"
    echo "         WSL2 DNS is broken. Repair it with:"
    echo "           sudo rm /etc/resolv.conf"
    echo '           echo "nameserver 8.8.8.8" | sudo tee /etc/resolv.conf'
    echo "           sudo chattr +i /etc/resolv.conf"
    echo "         then restart WSL2 from PowerShell:  wsl --shutdown"
  fi

  if command -v curl >/dev/null 2>&1; then
    remote="$(curl -sS -I --max-time 10 "https://s3.${AWS_REGION}.amazonaws.com" 2>/dev/null \
              | tr -d '\r' | awk -F': ' 'tolower($1)=="date"{print $2}')"
    if [[ -n "${remote}" ]]; then
      skew=$(( $(date +%s) - $(date -d "${remote}" +%s) ))
      skew=${skew#-}
      if (( skew <= 60 )); then
        pass "clock within ${skew}s of AWS"
      else
        fail "clock is ${skew}s away from AWS - signatures will be rejected."
        echo "         Fix with:  sudo hwclock -s"
      fi
    else
      note "could not read the Date header from AWS to check clock drift"
    fi
  fi
else
  note "not running under WSL2 - skipping the WSL-specific checks"
fi
echo

# ---------------------------------------------------- 6. seed lab.env keys
echo "6. Writing Lab 9 configuration into ${LAB_ENV_FILE}"

IMAGE_DIR="${HERE}/images"
mkdir -p "${IMAGE_DIR}"

# Names the lab sheet specifies, derived from LAB_PREFIX at runtime.
lab_env_set \
  LAB9_BUCKET       "${LAB_PREFIX}-lab9" \
  LAB9_IMAGE_DIR    "${IMAGE_DIR}" \
  LAB9_IMAGE_URBAN  "urban.jpg" \
  LAB9_IMAGE_BEACH  "beach.jpg" \
  LAB9_IMAGE_FACES  "faces.jpg" \
  LAB9_IMAGE_TEXT   "text.jpg"

# Analysis thresholds. Not identity values, so they carry a default, but they
# live in lab.env so the Comprehend and Rekognition scripts read one number.
lab_env_set \
  LAB9_MIN_CONFIDENCE "${LAB9_MIN_CONFIDENCE:-80}" \
  LAB9_MAX_LABELS     "${LAB9_MAX_LABELS:-20}"

# AWS_ACCOUNT_ID is not a Lab 9 value - if an earlier lab recorded it, leave it.
if [[ -n "${account_id}" && -z "${AWS_ACCOUNT_ID:-}" ]]; then
  lab_env_set AWS_ACCOUNT_ID "${account_id}"
  pass "recorded AWS_ACCOUNT_ID=${account_id}"
elif [[ -n "${AWS_ACCOUNT_ID:-}" ]]; then
  pass "AWS_ACCOUNT_ID already recorded (${AWS_ACCOUNT_ID}) - left alone"
fi
echo

lab_env_report LAB_PREFIX AWS_REGION AWS_ACCOUNT_ID \
  LAB9_BUCKET LAB9_IMAGE_DIR LAB9_IMAGE_URBAN LAB9_IMAGE_BEACH \
  LAB9_IMAGE_FACES LAB9_IMAGE_TEXT LAB9_MIN_CONFIDENCE LAB9_MAX_LABELS

# ------------------------------------------------------------- 7. the images
echo "7. Images"
missing=0
for name in "${LAB9_IMAGE_URBAN}" "${LAB9_IMAGE_BEACH}" "${LAB9_IMAGE_FACES}" "${LAB9_IMAGE_TEXT}"; do
  stem="${name%.*}"
  found=""
  for ext in jpg jpeg png; do
    [[ -f "${IMAGE_DIR}/${stem}.${ext}" ]] && { found="${IMAGE_DIR}/${stem}.${ext}"; break; }
  done
  if [[ -n "${found}" ]]; then
    pass "${name} <- ${found}"
  else
    note "${name} not in ${IMAGE_DIR}/"
    missing=$((missing+1))
  fi
done

if (( missing )); then
  echo
  echo "  Save the four images from the lab sheet into ${IMAGE_DIR}/."
  echo "  The lab sheet pairs each one with exactly one analysis:"
  echo "    ${LAB9_IMAGE_URBAN}   an urban setting            label recognition"
  echo "    ${LAB9_IMAGE_BEACH}   a person on a beach         image moderation"
  echo "    ${LAB9_IMAGE_FACES}   people showing their faces  facial analysis"
  echo "    ${LAB9_IMAGE_TEXT}    an image containing text    text extraction"
  echo
  echo "  A .jpg, .jpeg or .png with the right stem is fine - the object is"
  echo "  stored under the name above either way. Rekognition needs JPEG or"
  echo "  PNG bytes, at least 80x80 pixels, under 15 MB."
  echo
  echo "  Check them without uploading:  python3 lab9_bucket.py --check"
fi
echo

echo "=============================================================="
printf ' %d ok, %d warnings, %d failures\n' "${ok}" "${warn}" "${bad}"
echo "=============================================================="
if (( bad )); then
  echo
  echo "Fix the failures above before running the analyses."
  exit 1
fi
echo
echo "Next:  python3 lab9_comprehend.py language    (prompts for a text)"
