#!/usr/bin/env bash
#
# CITS5503 Lab 2 - checkpoint rehearsal
#
# Read-only. Creates and deletes nothing. Run it before you demonstrate, so
# nothing surprises you on your single attempt at each checkpoint.
#
# Checks:
#   Checkpoint 1 - <prefix>-vm1 exists, is t3.micro, has a public IP
#   Checkpoint 2 - <prefix>-vm2 exists via boto3, is t3.micro, name differs
#                  from vm1, and the boto3 script contains the run/tag calls
#   Checkpoint 3 - reports current instance states so you can confirm they
#                  reach shutting-down / terminated after you act in the console
#   Extras       - Docker: image built, container serving "Hello World!"
#
# Usage:
#   ./lab2_verify.sh                 # prefix from ~/cits5503/lab.env
#   ./lab2_verify.sh --prefix myname
#   ./lab2_verify.sh --skip-docker
#
set -uo pipefail   # deliberately no -e: we want every check to run

LAB_DIR="${LAB_DIR:-$HOME/cits5503}"
LAB2_DIR="$LAB_DIR/lab2"
ENV_FILE="$LAB_DIR/lab.env"
BOTO_SCRIPT="${BOTO_SCRIPT:-$LAB2_DIR/lab2_boto3.py}"
[[ -f "$BOTO_SCRIPT" ]] || BOTO_SCRIPT="$(dirname "$0")/lab2_boto3.py"

PREFIX_ARG=""
SKIP_DOCKER=0
DOCKER_PORT=80

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) PREFIX_ARG="${2:-}"; shift 2 ;;
    --skip-docker) SKIP_DOCKER=1; shift ;;
    --port) DOCKER_PORT="${2:-80}"; shift 2 ;;
    -h|--help) awk 'NR>1 && /^#/{sub(/^# ?/,""); print; next} NR>1{exit}' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

c_reset=$'\033[0m'; c_blue=$'\033[1;34m'; c_green=$'\033[1;32m'
c_yellow=$'\033[1;33m'; c_red=$'\033[1;31m'; c_dim=$'\033[2m'
FAILURES=0

head1() { printf '\n%s=== %s ===%s\n' "$c_blue" "$1" "$c_reset"; }
pass()  { printf '  %sPASS%s  %s\n' "$c_green"  "$c_reset" "$1"; }
fail()  { printf '  %sFAIL%s  %s\n' "$c_red"    "$c_reset" "$1"; FAILURES=$((FAILURES+1)); }
note()  { printf '  %sNOTE%s  %s\n' "$c_yellow" "$c_reset" "$1"; }
info()  { printf '        %s%s%s\n' "$c_dim" "$1" "$c_reset"; }


# --------------------------------------------------------------- preflight ---
head1 "Preflight"

LOADER="$(dirname "$0")/lab_env.sh"
[[ -f "$LOADER" ]] || LOADER="$LAB_DIR/lab2/lab_env.sh"
if [[ ! -f "$LOADER" ]]; then fail "lab_env.sh not found next to this script"; exit 1; fi
# shellcheck disable=SC1090
source "$LOADER"
load_lab_env "$PREFIX_ARG" || exit 1
lab_env_report

VM1="$VM1_NAME"
VM2="$VM2_NAME"

command -v aws >/dev/null && pass "aws CLI present ($(aws --version 2>&1 | cut -d' ' -f1))" \
                          || { fail "aws CLI not found"; exit 1; }

ACTUAL_REGION="$(aws configure get region 2>/dev/null)"
[[ "$ACTUAL_REGION" == "$REGION" ]] && pass "region is $REGION" \
  || fail "region is '${ACTUAL_REGION:-unset}' but should be '$REGION'"

if ARN=$(aws sts get-caller-identity --query Arn --output text 2>&1); then
  pass "identity: $ARN"
else
  echo "$ARN" | sed 's/^/        /'
  fail "sts get-caller-identity failed (signature errors mean clock drift: sudo hwclock -s)"
  exit 1
fi

# Pull every relevant instance once.
INSTANCES=$(aws ec2 describe-instances \
  --filters "Name=tag:Name,Values=${PREFIX}-*" \
  --query 'Reservations[].Instances[].{Name:Tags[?Key==`Name`]|[0].Value,ID:InstanceId,Type:InstanceType,State:State.Name,IP:PublicIpAddress}' \
  --output json 2>/dev/null)

check_instance() {
  local want_name=$1
  python3 - "$want_name" <<PY
import json, sys
name = sys.argv[1]
data = json.loads('''$INSTANCES''' or "[]")
alive = {"pending", "running", "stopping", "stopped"}
match = [i for i in data if i.get("Name") == name and i.get("State") in alive]
if not match:
    dead = [i for i in data if i.get("Name") == name]
    if dead:
        # keep field positions aligned with the FOUND line below
        print("TERMINATED %s - %s -" % (dead[0]["ID"], dead[0]["State"]))
    else:
        print("MISSING - - - -")
    sys.exit(0)
i = match[0]
print("FOUND %s %s %s %s" % (i["ID"], i["Type"], i["State"], i.get("IP") or "-"))
PY
}

# ------------------------------------------------------------ checkpoint 1 ---
head1 "Checkpoint 1: AWS CLI-based EC2 and SSH"

read -r status id itype istate iip <<<"$(check_instance "$VM1")"
case "$status" in
  FOUND)
    pass "$VM1 exists: $id ($istate)"
    [[ "$itype" == "$INSTANCE_TYPE" ]] && pass "instance type is $INSTANCE_TYPE" \
                                 || fail "instance type is $itype, must be $INSTANCE_TYPE"
    if [[ "$iip" != "-" ]]; then
      pass "public IP: $iip"
    else
      fail "no public IP - the console needs one to show for this checkpoint"
    fi
    info "console: https://${REGION}.console.aws.amazon.com/ec2/home?region=${REGION}#Instances:"
    ;;
  TERMINATED) fail "$VM1 is $istate ($id) - relaunch it before demonstrating" ;;
  *)          fail "$VM1 not found - run ./lab2_awscli.sh" ;;
esac

# ------------------------------------------------------------ checkpoint 2 ---
head1 "Checkpoint 2: Boto3-based EC2"

if [[ -f "$BOTO_SCRIPT" ]]; then
  pass "boto3 script found: $BOTO_SCRIPT"

  # Find whichever venv holds boto3 - labenv, .venv, whatever it is called.
  VENV_PY=""
  if python3 -c "import boto3" 2>/dev/null; then
    VENV_PY="$(command -v python3)"
    pass "boto3 importable on the current python3 ($(python3 -c 'import boto3;print(boto3.__version__)'))"
  else
    for cand in "${VIRTUAL_ENV:-}" "$LAB_DIR/labenv" "$LAB_DIR/.venv" "$LAB_DIR/venv" \
                "$HOME/labenv" "$HOME/.venv"; do
      [[ -n "$cand" && -x "$cand/bin/python3" ]] || continue
      if "$cand/bin/python3" -c "import boto3" 2>/dev/null; then VENV_PY="$cand/bin/python3"; break; fi
    done
    if [[ -n "$VENV_PY" ]]; then
      note "boto3 is not on the active python3, but lives in ${VENV_PY%/bin/python3}"
      info "activate it before you demo:  labenv"
    else
      fail "boto3 not importable and no venv containing it found - run: labenv"
    fi
  fi

  grep -q "run_instances"  "$BOTO_SCRIPT" && pass "script calls run_instances()" \
                                          || fail "script has no run_instances() call to show"
  grep -q "create_tags"    "$BOTO_SCRIPT" && pass "script calls create_tags()" \
                                          || fail "script has no create_tags() call to show"
  grep -q "INSTANCE_TYPE"  "$BOTO_SCRIPT" && pass "script pins the instance type" \
                                          || fail "script does not pin an instance type"
  info "have this file open at the run_instances / create_tags lines when you demo"
else
  fail "lab2_boto3.py not found - the facilitator needs to see the source"
fi

read -r status2 id2 itype2 istate2 iip2 <<<"$(check_instance "$VM2")"
case "$status2" in
  FOUND)
    pass "$VM2 exists: $id2 ($istate2)"
    [[ "$itype2" == "$INSTANCE_TYPE" ]] && pass "instance type is $INSTANCE_TYPE" \
                                  || fail "instance type is $itype2, must be $INSTANCE_TYPE"
    [[ "$VM2" != "$VM1" ]] && pass "name differs from the Checkpoint 1 instance" \
                           || fail "vm2 name collides with vm1"
    [[ "${id2:-}" != "${id:-}" ]] && pass "distinct instance from Checkpoint 1" \
                                  || fail "same instance id as Checkpoint 1"
    ;;
  TERMINATED) fail "$VM2 is $istate2 ($id2) - relaunch it before demonstrating" ;;
  *)          fail "$VM2 not found - run: python3 lab2_boto3.py" ;;
esac

# ------------------------------------------------------------ checkpoint 3 ---
head1 "Checkpoint 3: Cleanup"

note "Terminate BOTH instances from the AWS Management Console, not the CLI."
info "console: https://${REGION}.console.aws.amazon.com/ec2/home?region=${REGION}#Instances:"
info "select instance > Instance state > Terminate instance"
echo
printf '  Current states:\n'
aws ec2 describe-instances \
  --filters "Name=tag:Name,Values=${PREFIX}-*" \
  --query 'Reservations[].Instances[].{Name:Tags[?Key==`Name`]|[0].Value,ID:InstanceId,Type:InstanceType,State:State.Name}' \
  --output table 2>/dev/null | sed 's/^/  /'
info "re-run this script after terminating: both should read shutting-down or terminated"

# ---------------------------------------------------------------- docker -----
if (( ! SKIP_DOCKER )); then
  head1 "Docker (practice task, not a checkpoint)"

  if ! command -v docker >/dev/null; then
    note "docker not on PATH - enable WSL integration in Docker Desktop settings"
  elif ! docker info >/dev/null 2>&1; then
    note "docker CLI present but the engine is unreachable - start Docker Desktop"
  else
    pass "docker engine reachable ($(docker --version | cut -d, -f1))"
    docker image ls --format '{{.Repository}}' | grep -qx "$DOCKER_IMAGE" \
      && pass "image $DOCKER_IMAGE built" || note "image $DOCKER_IMAGE not built yet"
    if docker ps --format '{{.Names}}' | grep -qx "$DOCKER_CONTAINER"; then
      pass "container $DOCKER_CONTAINER is running"
      if curl -fsS --max-time 3 "http://localhost:$DOCKER_PORT" 2>/dev/null | grep -q "Hello World!"; then
        pass "http://localhost:$DOCKER_PORT serves \"Hello World!\""
      else
        note "container up but localhost:$DOCKER_PORT did not return Hello World!"
      fi
    else
      note "container $DOCKER_CONTAINER not running (fine if you already stopped and removed it)"
    fi
  fi
fi

# --------------------------------------------------------------- summary -----
head1 "Summary"
if (( FAILURES == 0 )); then
  printf '  %sEverything checks out.%s\n\n' "$c_green" "$c_reset"
  exit 0
else
  printf '  %s%d check(s) failed - fix these before you demonstrate.%s\n\n' \
    "$c_red" "$FAILURES" "$c_reset"
  exit 1
fi
