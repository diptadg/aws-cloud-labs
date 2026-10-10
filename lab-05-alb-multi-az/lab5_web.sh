#!/usr/bin/env bash
#
# CITS5503 Lab 5 - install Apache2 on both instances and set the page <title>
# to the instance name (Checkpoint 2 asks for the title to show the name).
#
# For each of <prefix>-vm1 and <prefix>-vm2 this script:
#   1. looks up the current public IP from the Name tag
#   2. waits until SSH is accepting connections
#   3. runs  apt-get update  +  apt install apache2
#   4. rewrites the <title>...</title> in /var/www/html/index.html
#
# Do NOT run this with sudo - it would leave root-owned files in ~/cits5503
# and break the venv on the next lab.

set -euo pipefail

# All configuration comes from lab.env - nothing is hard-coded here.
# Override the file location with:  LAB_ENV=/path/to/file ./lab5_web.sh
ENV_FILE="${LAB_ENV:-${HOME}/cits5503/lab.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Config file not found: $ENV_FILE" >&2
  echo "It must define LAB_PREFIX and AWS_REGION." >&2
  exit 1
fi
# shellcheck source=/dev/null
source "$ENV_FILE"

for var in LAB_PREFIX AWS_REGION; do
  if [[ -z "${!var:-}" ]]; then
    echo "$ENV_FILE is missing a value for $var" >&2
    exit 1
  fi
done

# These are written into lab.env by lab5_alb.py. The :- fallbacks only apply if
# you run this script before that one, and derive from LAB_PREFIX.
VM_NAMES=("${LAB5_VM1_NAME:-${LAB_PREFIX}-vm1}" "${LAB5_VM2_NAME:-${LAB_PREFIX}-vm2}")
KEY_NAME="${KEY_NAME:-${LAB_PREFIX}-key}"
KEY_PATH="${KEY_PATH:-${HOME}/.ssh/${KEY_NAME}.pem}"
SSH_USER="${SSH_USER:-ubuntu}"      # login user for the Ubuntu AMI

if [[ $EUID -eq 0 ]]; then
  echo "Run this as your normal user, not with sudo." >&2
  exit 1
fi

if [[ ! -f "$KEY_PATH" ]]; then
  echo "Private key not found at $KEY_PATH" >&2
  echo "Run lab5_alb.py first, or copy the .pem into ~/.ssh (not /mnt/c)." >&2
  exit 1
fi

# SSH refuses keys that look world-readable. On the Linux filesystem this sticks;
# on /mnt/c it silently does nothing, which is why the key belongs in ~/.ssh.
chmod 400 "$KEY_PATH" 2>/dev/null || true

SSH_OPTS=(-i "$KEY_PATH"
          -o StrictHostKeyChecking=accept-new
          -o UserKnownHostsFile="${HOME}/.ssh/known_hosts"
          -o ConnectTimeout=8
          -o LogLevel=ERROR)

lookup_ip() {
  aws ec2 describe-instances \
    --region "$AWS_REGION" \
    --filters "Name=tag:Name,Values=$1" "Name=instance-state-name,Values=running" \
    --query 'Reservations[0].Instances[0].PublicIpAddress' \
    --output text 2>/dev/null
}

wait_for_ssh() {
  local ip="$1"
  echo "    waiting for SSH on $ip ..."
  for _ in $(seq 1 30); do
    if ssh "${SSH_OPTS[@]}" "${SSH_USER}@${ip}" true 2>/dev/null; then
      return 0
    fi
    sleep 10
  done
  return 1
}

configure_instance() {
  local name="$1" ip="$2"

  echo "    installing Apache2 on $name ..."
  ssh "${SSH_OPTS[@]}" "${SSH_USER}@${ip}" "INSTANCE_NAME='${name}' bash -s" <<'REMOTE'
set -e
export DEBIAN_FRONTEND=noninteractive

sudo apt-get update -y -qq
sudo apt-get install -y -qq apache2
sudo systemctl enable --now apache2

INDEX=/var/www/html/index.html

if [ -f "$INDEX" ] && grep -q '<title>' "$INDEX"; then
    # Edit the <title> ... </title> tags of the default Apache2 Ubuntu page.
    sudo sed -i "s|<title>.*</title>|<title>${INSTANCE_NAME}</title>|" "$INDEX"
else
    # Fallback: the default page is missing, so write a minimal one.
    sudo tee "$INDEX" >/dev/null <<HTML
<html>
  <head><title>${INSTANCE_NAME}</title></head>
  <body><h1>${INSTANCE_NAME}</h1></body>
</html>
HTML
fi

sudo systemctl restart apache2
echo "    title now: $(grep -o '<title>[^<]*</title>' "$INDEX" | head -1)"
REMOTE
}

echo "Region: $AWS_REGION    Key: $KEY_PATH"
echo

for NAME in "${VM_NAMES[@]}"; do
  IP="$(lookup_ip "$NAME")"

  if [[ -z "$IP" || "$IP" == "None" ]]; then
    echo "[$NAME] no running instance with a public IP - run lab5_alb.py first." >&2
    exit 1
  fi

  echo "[$NAME] $IP"
  if ! wait_for_ssh "$IP"; then
    echo "    could not SSH to $IP after 5 minutes." >&2
    echo "    Check: security group allows tcp/22, instance is running," >&2
    echo "    and the clock is in sync (sudo hwclock -s)." >&2
    exit 1
  fi
  configure_instance "$NAME" "$IP"
  echo "    http://${IP}/"
  echo
done

echo "Done. Both instances are serving a page whose title is the instance name."
echo "Target group health should flip to 'healthy' within about 30 seconds."
echo "Next:  ./lab5_verify.sh"
