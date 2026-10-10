#!/usr/bin/env bash
#
# CITS5503 Lab 1 - run the three checkpoints end to end.
#
# Usage:
#   ./lab1_verify.sh              # expected region from ~/cits5503/lab.env
#   ./lab1_verify.sh us-east-1    # or pass it explicitly
#
# Exits 0 if every checkpoint passes, non-zero otherwise.
#
set -uo pipefail

LAB_DIR="${LAB_DIR:-$HOME/cits5503}"
VENV_DIR="$LAB_DIR/.venv"
# regions_table.py sits next to this script, wherever you keep your Lab 1 files
# (e.g. ~/cits5503/lab1/). Only the venv and lab.env live in $LAB_DIR.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FAILURES=0

pass()  { printf '  \033[1;32mPASS\033[0m  %s\n' "$*"; }
fail()  { printf '  \033[1;31mFAIL\033[0m  %s\n' "$*"; FAILURES=$((FAILURES + 1)); }
head1() { printf '\n\033[1;34m--- %s\033[0m\n' "$*"; }
cmd()   { printf '\033[2m$ %s\033[0m\n' "$*"; }

# ----------------------------------------------------------------- config ---
LAB_ENV="${LAB_ENV:-$LAB_DIR/lab.env}"
EXPECTED_REGION=""

if [[ -f "$LAB_ENV" ]]; then
  # shellcheck disable=SC1090
  source "$LAB_ENV"
  # AWS_REGION is the current name; AWS_LAB_REGION is kept as a fallback so an
  # older lab.env still works before you re-run lab1_setup.sh.
  EXPECTED_REGION="${AWS_REGION:-${AWS_LAB_REGION:-}}"
fi

# A region on the command line wins; ap-southeast-2 is the documented default.
EXPECTED_REGION="${1:-${EXPECTED_REGION:-ap-southeast-2}}"

# --------------------------------------------------------------- pre-flight -
head1 "Pre-flight"

if [[ -n "${VIRTUAL_ENV:-}" ]]; then
  pass "venv active: $VIRTUAL_ENV"
elif [[ -f "$VENV_DIR/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
  pass "venv activated for this run: $VENV_DIR"
  echo "        (during the live demo, activate it yourself first: labenv)"
else
  fail "No venv at $VENV_DIR — run ./lab1_setup.sh"
fi

# WSL clocks drift while Windows sleeps, which makes AWS reject every request
# with an expired-signature error that looks like a credentials problem.
if command -v hwclock >/dev/null 2>&1; then
  echo "        (if AWS calls fail on signatures, resync: sudo hwclock -s)"
fi

# ----------------------------------------- Checkpoint 1: environment + ARN --
head1 "Checkpoint 1: Environment and AWS identity"

cmd "python3 --version"
if python3 --version; then pass "python3 present"; else fail "python3 missing"; fi

cmd "aws --version"
if AWS_VER=$(aws --version 2>&1); then
  echo "$AWS_VER"
  pass "aws cli present"
  grep -q 'aws-cli/2' <<<"$AWS_VER" \
    || fail "this is AWS CLI v1 — the lab expects v2; re-run ./lab1_setup.sh"
else
  fail "aws cli missing"
fi

cmd "python3 -c \"import boto3; print('boto3', boto3.__version__)\""
if python3 -c "import boto3; print('boto3', boto3.__version__)"; then
  pass "boto3 importable"
else
  fail "boto3 not importable — activate the venv: labenv"
fi

cmd "aws sts get-caller-identity --output table"
if IDENTITY=$(aws sts get-caller-identity --output table 2>&1); then
  echo "$IDENTITY"
  pass "credentials resolve to an IAM identity"
else
  echo "$IDENTITY"
  fail "sts get-caller-identity failed (bad keys, or clock drift: sudo hwclock -s)"
fi

# ------------------------------------------------ Checkpoint 2: region -----
head1 "Checkpoint 2: AWS region"

cmd "aws configure get region"
ACTUAL_REGION=$(aws configure get region 2>/dev/null)
echo "${ACTUAL_REGION:-(not set)}"

if [[ "$ACTUAL_REGION" == "$EXPECTED_REGION" ]]; then
  pass "matches the configured region ($EXPECTED_REGION)"
else
  fail "configured region is '${ACTUAL_REGION:-unset}' but should be '$EXPECTED_REGION'"
  echo "        fix with:  aws configure set region $EXPECTED_REGION"
fi

# ------------------------------------------ Checkpoint 3: boto3 table ------
head1 "Checkpoint 3: Boto3 output table"

if [[ ! -f "$SCRIPT_DIR/regions_table.py" ]]; then
  fail "regions_table.py not found in $SCRIPT_DIR (keep it next to this script)"
else
  cmd "python3 regions_table.py"
  if OUTPUT=$(cd "$SCRIPT_DIR" && python3 regions_table.py 2>&1); then
    echo "$OUTPUT"
    HAS_ENDPOINT=0; HAS_REGIONNAME=0
    grep -q "Endpoint"   <<<"$OUTPUT" && HAS_ENDPOINT=1
    grep -q "RegionName" <<<"$OUTPUT" && HAS_REGIONNAME=1

    if (( HAS_ENDPOINT && HAS_REGIONNAME )); then
      pass "table has both required columns"
    else
      (( HAS_ENDPOINT ))   || fail "missing the Endpoint column"
      (( HAS_REGIONNAME )) || fail "missing the RegionName column"
    fi

    # The checkpoint calls for exactly two columns — flag a third if present.
    COLS=$(grep -m1 'Endpoint' <<<"$OUTPUT" | tr -cd '|' | wc -c)
    if (( COLS > 0 && COLS != 3 )); then
      fail "header row looks like it has more than two columns"
    fi
  else
    echo "$OUTPUT"
    fail "regions_table.py exited with an error"
  fi
fi

# ------------------------------------------------------------- summary -----
head1 "Summary"
if (( FAILURES == 0 )); then
  printf '  \033[1;32mAll three checkpoints pass.\033[0m\n'
  printf '  Remember to activate the venv (labenv) in whatever shell you demo from.\n\n'
  exit 0
else
  printf '  \033[1;31m%d check(s) failed — see the FAIL lines above.\033[0m\n\n' "$FAILURES"
  exit 1
fi
