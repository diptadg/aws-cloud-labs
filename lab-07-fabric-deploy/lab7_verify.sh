#!/usr/bin/env bash
#
# CITS5503 Lab 7 - read-only checkpoint rehearsal
# ------------------------------------------------
# Checks everything a facilitator will look at, and changes nothing. Run it
# once before you go up, and again if anything looks off during the demo.
#
#   labenv
#   ./lab7_verify.sh
#
#   ./lab7_verify.sh --cleanup    # only the Checkpoint 3 leftover check

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lab7_lib.sh
source "${HERE}/lab7_lib.sh"

lab_env_load
lab_env_require LAB_PREFIX AWS_REGION

# Values lab7_ec2.py and lab7_fabric.py own are read, never re-derived here:
# an empty one means that script has not run, which is worth saying out loud.
VM_NAME="${LAB7_VM_NAME:-}"
VM_ID="${LAB7_VM_ID:-}"
KEY_PATH="${KEY_PATH:-}"
INSTANCE_TYPE="${LAB7_INSTANCE_TYPE:-}"
SERVICE="${LAB7_SERVICE:-}"
RUNNER="${LAB7_RUNNER:-}"
DJANGO_PORT="${DJANGO_PORT:-8000}"
SSH_USER="${SSH_USER:-ubuntu}"
SSH_CONFIG="${LAB7_SSH_CONFIG:-${HOME}/.ssh/config}"
FABRIC_SCRIPT="${HERE}/lab7_fabric.py"

CLEANUP_ONLY=0
[[ "${1:-}" == "--cleanup" ]] && CLEANUP_ONLY=1

pass=0; fail=0
step()  { printf '\n== %s\n' "$1"; }
ok()    { printf '   ok    %s\n' "$1"; pass=$((pass+1)); }
bad()   { printf '   FAIL  %s\n' "$1"; fail=$((fail+1)); }
note()  { printf '         %s\n' "$1"; }

aws_ec2() { aws ec2 "$@" --region "${AWS_REGION}" --output text 2>/dev/null; }

remote() {
  ssh -o BatchMode=yes \
      -o StrictHostKeyChecking=no \
      -o UserKnownHostsFile=/dev/null \
      -o ConnectTimeout=10 \
      -o LogLevel=ERROR \
      -i "${KEY_PATH}" "${SSH_USER}@${VM_IP}" "$@" 2>/dev/null
}

# ===================================================================
# Checkpoint 3 leftovers (also run at the end of a full pass)
# ===================================================================
cleanup_check() {
  step "Checkpoint 3 - nothing left running"

  local live
  live="$(aws_ec2 describe-instances \
      --filters "Name=instance-state-name,Values=pending,running,stopping,stopped" \
      --query 'Reservations[].Instances[].[InstanceId,Tags[?Key==`Name`]|[0].Value,State.Name]')"
  if [[ -z "${live}" ]]; then
    ok "no instances in ${AWS_REGION}"
  else
    note "instances still present in ${AWS_REGION}:"
    echo "${live}" | while read -r line; do note "  ${line}"; done
    note "terminate these in the console for Checkpoint 3"
  fi

  local eips
  eips="$(aws_ec2 describe-addresses --query 'Addresses[].[PublicIp,AllocationId,AssociationId]')"
  if [[ -z "${eips}" ]]; then
    ok "no Elastic IPs allocated"
  else
    note "Elastic IPs still allocated:"
    echo "${eips}" | while read -r line; do note "  ${line}"; done
    note "release these in the console for Checkpoint 3"
  fi
}

if (( CLEANUP_ONLY )); then
  cleanup_check
  echo
  exit 0
fi

lab_env_report LAB_PREFIX AWS_REGION LAB7_VM_NAME LAB7_VM_ID LAB7_VM_IP DJANGO_PORT KEY_PATH

# ===================================================================
# Local tooling
# ===================================================================
step "Local environment"

if [[ -n "${VIRTUAL_ENV:-}" ]]; then
  ok "venv active: ${VIRTUAL_ENV}"
else
  bad "no venv active - run 'labenv'"
fi

if python3 -c "import fabric" 2>/dev/null; then
  ok "fabric $(python3 -c 'import fabric; print(fabric.__version__)')"
else
  bad "fabric not importable - run ./lab7_prepare.sh"
fi

if [[ -z "${KEY_PATH}" ]]; then
  bad "KEY_PATH is not set in ${LAB_ENV_FILE} - run python3 lab7_ec2.py"
elif [[ -f "${KEY_PATH}" ]]; then
  MODE="$(stat -c '%a' "${KEY_PATH}" 2>/dev/null)"
  [[ "${MODE}" == "600" ]] && ok "${KEY_PATH} mode 600" || bad "${KEY_PATH} is mode ${MODE}, want 600"
else
  bad "${KEY_PATH} not found"
fi

# ===================================================================
# Checkpoint 1 - where the three required pieces live
# ===================================================================
step "Checkpoint 1 - Fabric script"

if [[ -f "${FABRIC_SCRIPT}" ]]; then
  ok "$(basename "${FABRIC_SCRIPT}") present"
  note "jump to these lines when asked:"
  note "  connecting to the instance     line $(grep -n '^def connect' "${FABRIC_SCRIPT}" | cut -d: -f1)  connect()"
  note "  nginx proxy to the dev server  line $(grep -n '^def configure_nginx' "${FABRIC_SCRIPT}" | cut -d: -f1)  configure_nginx()"
  note "  dev server on ${DJANGO_PORT}, backgrounded  line $(grep -n '^def start_django_in_background' "${FABRIC_SCRIPT}" | cut -d: -f1)  start_django_in_background()"
else
  bad "${FABRIC_SCRIPT} not found"
fi

if [[ -z "${VM_NAME}" ]]; then
  bad "LAB7_VM_NAME is not set in ${LAB_ENV_FILE} - run python3 lab7_ec2.py"
elif [[ -f "${SSH_CONFIG}" ]] && grep -q "Host ${VM_NAME}" "${SSH_CONFIG}"; then
  ok "${SSH_CONFIG} has a Host block for ${VM_NAME}"
  CFG_HOSTNAME="$(awk -v h="Host ${VM_NAME}" '
      $0 ~ "^"h"$" {found=1; next}
      found && /^[[:space:]]*Hostname/ {print $2; exit}
      found && /^Host / {exit}' "${SSH_CONFIG}")"
  note "Hostname ${CFG_HOSTNAME:-<none>}"
else
  bad "no Host block for ${VM_NAME} in ${SSH_CONFIG} - run lab7_sshconfig.py"
fi

# ===================================================================
# Checkpoint 2 - the instance, the deployment, the page
# ===================================================================
step "Checkpoint 2 - instance"

if [[ -z "${VM_ID}" ]]; then
  bad "LAB7_VM_ID is not set in lab.env - run lab7_ec2.py"
  VM_IP=""
else
  read -r STATE ITYPE VM_IP AZ <<<"$(aws_ec2 describe-instances --instance-ids "${VM_ID}" \
      --query 'Reservations[0].Instances[0].[State.Name,InstanceType,PublicIpAddress,Placement.AvailabilityZone]')"

  [[ "${STATE}" == "running" ]] && ok "${VM_NAME} (${VM_ID}) is running" \
                                || bad "${VM_NAME} (${VM_ID}) is '${STATE}'"
  if [[ -z "${INSTANCE_TYPE}" ]]; then
    bad "LAB7_INSTANCE_TYPE is not set in ${LAB_ENV_FILE} - re-run python3 lab7_ec2.py"
  elif [[ "${ITYPE}" == "${INSTANCE_TYPE}" ]]; then
    ok "instance type ${ITYPE}"
  else
    bad "instance type is ${ITYPE}, the lab expects ${INSTANCE_TYPE}"
  fi
  ok "availability zone ${AZ} (region ${AWS_REGION})"

  if [[ "${VM_IP}" == "None" || -z "${VM_IP}" ]]; then
    bad "no public IPv4 address"
    VM_IP=""
  else
    ok "public IP ${VM_IP}"
    if [[ -n "${LAB7_VM_IP:-}" && "${LAB7_VM_IP}" != "${VM_IP}" ]]; then
      bad "lab.env says ${LAB7_VM_IP} but AWS says ${VM_IP}"
      note "the instance was restarted - re-run: python3 lab7_sshconfig.py"
    fi
    if [[ -n "${CFG_HOSTNAME:-}" ]]; then
      if [[ "${CFG_HOSTNAME}" == *"${VM_IP//./-}"* || "${CFG_HOSTNAME}" == "${VM_IP}" ]]; then
        ok "${SSH_CONFIG} Hostname matches the current address"
      else
        bad "${SSH_CONFIG} Hostname is stale (${CFG_HOSTNAME})"
        note "re-run: python3 lab7_sshconfig.py"
      fi
    fi
  fi
fi

if [[ -n "${VM_IP}" ]]; then
  step "Checkpoint 2 - on the instance"

  UNAME="$(remote 'uname -s')"
  if [[ "${UNAME}" == "Linux" ]]; then
    ok "ssh reachable, uname -s -> Linux"
  else
    bad "cannot ssh to ${SSH_USER}@${VM_IP}"
    note "UWA wifi blocks outbound 22 - try a hotspot"
  fi

  if [[ "${UNAME}" == "Linux" ]]; then
    LISTEN="$(remote "ss -ltnp 2>/dev/null | grep ':${DJANGO_PORT} ' || true")"
    if [[ -n "${LISTEN}" ]]; then
      ok "something is listening on ${DJANGO_PORT}"
    else
      bad "nothing listening on ${DJANGO_PORT}"
    fi

    if [[ -n "${SERVICE}" ]] \
       && [[ "$(remote "systemctl is-active ${SERVICE} 2>/dev/null || true")" == "active" ]]; then
      ok "${SERVICE} systemd unit is active (server is backgrounded)"
    else
      # '[m]anage.py' so pgrep does not match the ssh command line carrying it
      NOHUP="$(remote "pgrep -f '[m]anage.py runserver' >/dev/null && echo yes || echo no")"
      if [[ "${NOHUP}" == "yes" ]]; then
        ok "runserver running detached${RUNNER:+ (${RUNNER} mode)}"
      else
        bad "no runserver process found"
      fi
    fi

    PARENT="$(remote "ps -o ppid= -C python3 2>/dev/null | tr -d ' ' | head -1")"
    [[ -n "${PARENT}" ]] && note "runserver parent pid ${PARENT} (not this ssh session)"

    NGINX="$(remote "systemctl is-active nginx 2>/dev/null || true")"
    [[ "${NGINX}" == "active" ]] && ok "nginx is active" || bad "nginx is ${NGINX:-unknown}"

    # nginx -T dumps the *effective* configuration, so this does not need to
    # know which file lab7_fabric.py wrote.
    PROXY="$(remote "sudo nginx -T 2>/dev/null | grep -c 'proxy_pass http://127.0.0.1:${DJANGO_PORT}' || echo 0")"
    [[ "${PROXY}" -ge 1 ]] && ok "nginx proxies port 80 -> 127.0.0.1:${DJANGO_PORT}" \
                           || bad "nginx default site does not proxy to ${DJANGO_PORT}"

    CODE="$(remote "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1/polls/")"
    [[ "${CODE}" == "200" ]] && ok "http://127.0.0.1/polls/ on the instance -> 200" \
                             || bad "http://127.0.0.1/polls/ on the instance -> ${CODE:-no response}"
  fi

  step "Checkpoint 2 - from this machine"
  URL="http://${VM_IP}/polls/"
  BODY="$(curl -s --max-time 20 "${URL}" || true)"
  CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "${URL}" || true)"
  if [[ "${CODE}" == "200" ]]; then
    ok "${URL} -> 200"
    if grep -q 'Hello, world\.' <<<"${BODY}"; then
      ok "page contains 'Hello, world.'"
    else
      bad "page loaded but does not contain 'Hello, world.'"
      note "got: ${BODY:0:120}"
    fi
  else
    bad "${URL} -> ${CODE:-no response}"
    note "if the on-instance check above passed, this is your network, not AWS"
    note "UWA wifi blocks outbound 80 - open it on a phone hotspot for the demo"
  fi
  note "browser URL for the demo: ${URL}"
fi

cleanup_check

echo
printf 'Checks passed: %d   failed: %d\n' "${pass}" "${fail}"
if (( fail )); then
  echo "Fix the FAIL lines above before you go up."
  exit 1
fi
echo "Ready."
