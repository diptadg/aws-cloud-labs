#!/usr/bin/env bash
# CITS5503 Lab 8 - local environment check and lab.env seeding.
#
# Run this once before anything else. It:
#   1. checks Docker, AWS CLI v2, boto3 and the sagemaker/pandas tooling
#   2. checks WSL2's two recurring breakages (DNS, clock drift)
#   3. writes the Lab 8 resource names into ~/cits5503/lab.env
#
# It is the only Lab 8 script that writes name/config keys. Every other script
# reads them. Values Labs 1-7 already recorded are reused, never duplicated.
#
#   ./lab8_prepare.sh

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lab8_lib.sh
source "${HERE}/lab8_lib.sh"

lab_env_load
lab_env_require LAB_PREFIX AWS_REGION

ok=0; warn=0; bad=0
pass() { printf '  [ ok ] %s\n' "$*"; ok=$((ok+1)); }
note() { printf '  [warn] %s\n' "$*"; warn=$((warn+1)); }
fail() { printf '  [FAIL] %s\n' "$*"; bad=$((bad+1)); }

echo "=============================================================="
echo " CITS5503 Lab 8 - preparation"
echo "=============================================================="
echo

# ---------------------------------------------------------------- 1. Docker
echo "1. Docker"
if command -v docker >/dev/null 2>&1; then
  pass "docker on PATH: $(docker --version 2>/dev/null)"
  if docker info >/dev/null 2>&1; then
    pass "docker daemon reachable"
  else
    fail "docker is installed but the daemon is not reachable."
    echo "         Start Docker Desktop on Windows and make sure"
    echo "         Settings -> Resources -> WSL integration is enabled for this distro."
  fi
else
  fail "docker not found."
  echo "         Install Docker Desktop on Windows and enable WSL integration."
  echo "         Do not 'apt install docker.io' inside WSL - it fights Docker Desktop."
fi
echo

# --------------------------------------------------------------- 2. AWS CLI
echo "2. AWS CLI"
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

# ----------------------------------------------------------------- 3. boto3
echo "3. Python tooling"
if python3 -c "import boto3" >/dev/null 2>&1; then
  pass "boto3 $(python3 -c 'import boto3;print(boto3.__version__)')"
else
  fail "boto3 not importable. Activate the shared lab venv first:  labenv"
  echo "         A bare 'pip3 install' fails on Ubuntu 26.04 (PEP 668) - use the venv."
fi
echo

# ------------------------------------------------------- 4. AWS credentials
echo "4. AWS credentials"
if command -v aws >/dev/null 2>&1 && \
   caller="$(aws sts get-caller-identity --region "${AWS_REGION}" --output text \
             --query 'Account,Arn' 2>/dev/null)"; then
  account_id="$(awk '{print $1}' <<<"${caller}")"
  pass "authenticated as $(awk '{print $2}' <<<"${caller}")"
  pass "account ${account_id}"
else
  account_id=""
  fail "aws sts get-caller-identity failed."
  echo "         If the error mentions a signature or the request time, WSL2's clock"
  echo "         has drifted after a Windows sleep. Fix it with:  sudo hwclock -s"
fi
echo

# --------------------------------------------------------- 5. WSL2 specifics
echo "5. WSL2 checks"
if grep -qi microsoft /proc/version 2>/dev/null; then
  pass "running under WSL2"

  # DNS. The lab sheet calls this out: ECR pushes fail with "no such host".
  if getent hosts "api.ecr.${AWS_REGION}.amazonaws.com" >/dev/null 2>&1; then
    pass "DNS resolves api.ecr.${AWS_REGION}.amazonaws.com"
  else
    fail "cannot resolve api.ecr.${AWS_REGION}.amazonaws.com"
    echo "         WSL2 DNS is broken. Repair it with:"
    echo "           sudo rm /etc/resolv.conf"
    echo '           echo "nameserver 8.8.8.8" | sudo tee /etc/resolv.conf'
    echo "           sudo chattr +i /etc/resolv.conf"
    echo "         then restart WSL2 from PowerShell:  wsl --shutdown"
  fi

  # Clock drift. Signature errors after the laptop sleeps come from here.
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

  # A push of a ~2 GB image needs somewhere to put the layers.
  free_gb="$(df -BG --output=avail / 2>/dev/null | tail -1 | tr -dc '0-9')"
  if [[ -n "${free_gb}" ]] && (( free_gb < 15 )); then
    note "only ${free_gb}G free on / - the Lab 8 image is roughly 2 GB before layers"
  else
    pass "${free_gb}G free on /"
  fi
else
  note "not running under WSL2 - skipping the WSL-specific checks"
fi
echo

# ---------------------------------------------------- 6. seed lab.env keys
echo "6. Writing Lab 8 configuration into ${LAB_ENV_FILE}"

# Names derived from LAB_PREFIX exactly as the lab sheet specifies.
lab_env_set \
  LAB8_IMAGE_NAME    "${LAB_PREFIX}-lab8" \
  LAB8_IMAGE_TAG     "latest" \
  LAB8_ECR_REPO      "${LAB_PREFIX}_ecr_repo" \
  LAB8_BUCKET        "${LAB_PREFIX}-lab8" \
  LAB8_CLUSTER       "${LAB_PREFIX}-cluster" \
  LAB8_SERVICE_NAME  "${LAB_PREFIX}-service" \
  LAB8_TASK_FAMILY   "${LAB_PREFIX}-task-family" \
  LAB8_CONTAINER     "${LAB_PREFIX}-container" \
  LAB8_SG_NAME       "${LAB_PREFIX}-sg-lab8" \
  LAB8_TUNING_JOB    "${LAB_PREFIX}-xgboost-tuningjob-01" \
  LAB8_S3_PREFIX     "sagemaker/${LAB_PREFIX}-hpo-xgboost-dm"

# Values fixed by the lab sheet rather than by identity, plus a random Jupyter
# token generated once (kept on re-runs). Written so that the
# Dockerfile, the security group, the ECS port mapping and the browser URL all
# read one number instead of four copies of it.
lab_env_set \
  LAB8_JUPYTER_PORT  "${LAB8_JUPYTER_PORT:-8888}" \
  LAB8_JUPYTER_TOKEN "${LAB8_JUPYTER_TOKEN:-$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')}" \
  LAB8_TASK_ROLE     "${LAB8_TASK_ROLE:-SageMakerRole}" \
  LAB8_EXEC_ROLE     "${LAB8_EXEC_ROLE:-ecsTaskExecutionRole}" \
  LAB8_TASK_CPU      "${LAB8_TASK_CPU:-256}" \
  LAB8_TASK_MEMORY   "${LAB8_TASK_MEMORY:-512}" \
  LAB8_BASE_IMAGE    "${LAB8_BASE_IMAGE:-python:3.10}" \
  LAB8_SAGEMAKER_SPEC "${LAB8_SAGEMAKER_SPEC:-sagemaker>=2.200,<3}" \
  LAB8_NOTEBOOK      "${LAB8_NOTEBOOK:-LabAI.ipynb}" \
  LAB8_NOTEBOOK_URL  "${LAB8_NOTEBOOK_URL:-https://raw.githubusercontent.com/zhangzhics/CITS5503_Sem2/master/Labs/src/LabAI.ipynb}" \
  LAB8_LOG_GROUP     "${LAB8_LOG_GROUP:-/ecs/${LAB8_TASK_FAMILY}}"

# AWS_ACCOUNT_ID is not a Lab 8 value - if an earlier lab recorded it, leave it.
if [[ -n "${account_id}" && -z "${AWS_ACCOUNT_ID:-}" ]]; then
  lab_env_set AWS_ACCOUNT_ID "${account_id}"
  pass "recorded AWS_ACCOUNT_ID=${account_id}"
elif [[ -n "${AWS_ACCOUNT_ID:-}" ]]; then
  pass "AWS_ACCOUNT_ID already recorded (${AWS_ACCOUNT_ID}) - left alone"
fi
echo

lab_env_report LAB_PREFIX AWS_REGION AWS_ACCOUNT_ID \
  LAB8_IMAGE_NAME LAB8_ECR_REPO LAB8_BUCKET LAB8_CLUSTER LAB8_SERVICE_NAME \
  LAB8_TASK_FAMILY LAB8_CONTAINER LAB8_SG_NAME LAB8_JUPYTER_PORT \
  LAB8_TASK_ROLE LAB8_EXEC_ROLE LAB8_TASK_CPU LAB8_TASK_MEMORY \
  LAB8_BASE_IMAGE LAB8_SAGEMAKER_SPEC LAB8_NOTEBOOK LAB8_LOG_GROUP

echo "=============================================================="
printf ' %d ok, %d warnings, %d failures\n' "${ok}" "${warn}" "${bad}"
echo "=============================================================="
if (( bad )); then
  echo
  echo "Fix the failures above before running ./lab8_image.sh."
  exit 1
fi
echo
echo "Next:  ./lab8_image.sh --test"
