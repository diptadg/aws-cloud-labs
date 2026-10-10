#!/usr/bin/env bash
# CITS5503 Lab 6 - deploy Django + nginx onto the EC2 instance
#
#   ./lab6_deploy.sh
#
# Waits for SSH, stages your AWS credentials on the instance, pushes
# lab6_remote_setup.sh and runs it. Safe to re-run: the remote script reuses an
# existing venv/project and just rewrites the app files and service.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lab6_lib.sh
source "${SCRIPT_DIR}/lab6_lib.sh"

lab_env_load
lab_env_require LAB_PREFIX AWS_REGION KEY_PATH SSH_USER LAB6_VM1_ID

VM_NAME="${LAB6_VM1_NAME:-${LAB_PREFIX}-vm1}"
AWS_DDB_TABLE="${AWS_DDB_TABLE:-UserFiles}"
DJANGO_PORT="${DJANGO_PORT:-8000}"
CRED_STAGE="/home/${SSH_USER}/.aws-stage"
CRED_FILE="${AWS_SHARED_CREDENTIALS_FILE:-${HOME}/.aws/credentials}"
REMOTE_SCRIPT="${SCRIPT_DIR}/lab6_remote_setup.sh"

[[ -f "${REMOTE_SCRIPT}" ]] || { echo "ERROR: ${REMOTE_SCRIPT} not found" >&2; exit 1; }
[[ -f "${KEY_PATH}" ]] || { echo "ERROR: private key ${KEY_PATH} not found - run lab6_ec2.py" >&2; exit 1; }

hdr "Configuration"
echo "  Instance      ${VM_NAME}  (${LAB6_VM1_ID})"
echo "  Region        ${AWS_REGION}"
echo "  Key           ${KEY_PATH}"
echo "  AWS table     ${AWS_DDB_TABLE}"
echo "  Django port   ${DJANGO_PORT}"

# ----------------------------------------------------------- public IP
hdr "Resolving the public IP"
IP="$(lab_instance_ip "${VM_NAME}")"
if [[ -z "${IP}" || "${IP}" == "None" ]]; then
  bad "No running instance named ${VM_NAME} - run: python3 lab6_ec2.py"
  exit 1
fi
ok "${VM_NAME} is at ${IP}"
# The IP changes across stop/start, so keep lab.env current.
lab_env_set LAB6_VM1_IP "${IP}"

# --------------------------------------------------------------- SSH
hdr "Waiting for SSH"
for attempt in $(seq 1 30); do
  if ssh "${SSH_OPTS[@]}" -i "${KEY_PATH}" "${SSH_USER}@${IP}" true 2>/dev/null; then
    ok "SSH is up (attempt ${attempt})"
    break
  fi
  if [[ "${attempt}" -eq 30 ]]; then
    bad "SSH never came up on ${IP}:22"
    note "Check the security group allows tcp/22 and that the instance is running."
    note "If AWS calls fail with an expired signature, resync the WSL clock: sudo hwclock -s"
    exit 1
  fi
  printf '  waiting for sshd (%s/30)\r' "${attempt}"
  sleep 10
done

# ------------------------------------------------------- credentials
hdr "Staging AWS credentials on the instance"
if [[ -f "${CRED_FILE}" ]]; then
  ssh "${SSH_OPTS[@]}" -i "${KEY_PATH}" "${SSH_USER}@${IP}" \
      "mkdir -p '${CRED_STAGE}' && chmod 700 '${CRED_STAGE}' && cat > '${CRED_STAGE}/credentials' && chmod 600 '${CRED_STAGE}/credentials'" \
      < "${CRED_FILE}"
  ok "copied ${CRED_FILE} (moved to /root/.aws by the remote script)"
  note "These are long-lived keys on a public host - terminate the instance after the demo."
else
  bad "${CRED_FILE} not found - the /polls/ page will report a DynamoDB error"
fi

# ------------------------------------------------------ remote setup
hdr "Running the remote setup"
scp "${SSH_OPTS[@]}" -i "${KEY_PATH}" -q "${REMOTE_SCRIPT}" "${SSH_USER}@${IP}:/home/${SSH_USER}/lab6_remote_setup.sh"
ssh "${SSH_OPTS[@]}" -i "${KEY_PATH}" -t "${SSH_USER}@${IP}" \
  "chmod +x /home/${SSH_USER}/lab6_remote_setup.sh && \
   AWS_REGION='${AWS_REGION}' \
   AWS_DDB_TABLE='${AWS_DDB_TABLE}' \
   VM_NAME='${VM_NAME}' \
   DJANGO_PORT='${DJANGO_PORT}' \
   CRED_STAGE='${CRED_STAGE}' \
   bash /home/${SSH_USER}/lab6_remote_setup.sh"

# ------------------------------------------------------------ check
hdr "Checking from here"
CODE="$(curl -s -m 15 -o /dev/null -w '%{http_code}' "http://${IP}/polls/" || true)"
if [[ "${CODE}" == "200" ]]; then
  ok "http://${IP}/polls/ returned 200"
else
  bad "http://${IP}/polls/ returned ${CODE:-nothing} from this machine"
  note "The app may still be fine - the UWA network commonly blocks outbound port 80."
  note "Try a phone hotspot or your home connection before assuming it is broken."
fi

echo
echo "  Open in a browser:  http://${IP}/polls/"
echo "  Next:  python3 lab6_alb.py"
