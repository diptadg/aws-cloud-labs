#!/usr/bin/env bash
#
# CITS5503 Lab 2 - Create an EC2 instance using awscli
#
# Covers lab sheet steps [1]-[7]:
#   [1] create-security-group      [5] create-tags  (Name=<prefix>-vm1)
#   [2] authorize-security-group-ingress (tcp/22)   [6] describe-instances -> public IP
#   [3] create-key-pair                             [7] ssh
#   [4] run-instances (t3.micro)
#
# Step [8] (list the instance in the AWS Management Console) is manual, and so
# is the cleanup for Checkpoint 3 - both must be done in the browser.
#
# Usage:
#   ./lab2_awscli.sh                 # run steps 1-6, print the ssh command
#   ./lab2_awscli.sh --ssh           # ... and then open the ssh session
#   ./lab2_awscli.sh --status        # show what already exists, create nothing
#   ./lab2_awscli.sh --prefix myname
#
# The script is idempotent: re-running it reuses the existing security group,
# key pair and instance instead of piling up duplicates.
#
set -euo pipefail

# ------------------------------------------------------------------ config --
LAB_DIR="${LAB_DIR:-$HOME/cits5503}"
LAB2_DIR="$LAB_DIR/lab2"
ENV_FILE="$LAB_DIR/lab.env"
STATE_FILE="$LAB2_DIR/lab2_awscli.state"

PREFIX_ARG=""
DO_SSH=0
STATUS_ONLY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix)  PREFIX_ARG="${2:-}"; shift 2 ;;
    --ssh)     DO_SSH=1; shift ;;
    --status)  STATUS_ONLY=1; shift ;;
    -h|--help) awk 'NR>1 && /^#/{sub(/^# ?/,""); print; next} NR>1{exit}' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

# ---------------------------------------------------------------- helpers ---
c_reset=$'\033[0m'; c_blue=$'\033[1;34m'; c_green=$'\033[1;32m'
c_yellow=$'\033[1;33m'; c_red=$'\033[1;31m'; c_dim=$'\033[2m'

log()  { printf '\n%s==> %s%s\n' "$c_blue"   "$1" "$c_reset"; }
ok()   { printf '    %s+%s %s\n'  "$c_green"  "$c_reset" "$1"; }
warn() { printf '    %s!%s %s\n'  "$c_yellow" "$c_reset" "$1"; }
info() { printf '    %s%s%s\n'    "$c_dim"    "$1" "$c_reset"; }
die()  { printf '\n%sERROR:%s %s\n\n' "$c_red" "$c_reset" "$1" >&2; exit 1; }
show() { printf '    %s$ %s%s\n' "$c_dim" "$1" "$c_reset"; }


# --------------------------------------------------------------- preflight --
log "Preflight"

mkdir -p "$LAB2_DIR" "$HOME/.ssh"
chmod 700 "$HOME/.ssh"

# Everything below comes from lab.env via the shared loader - nothing about
# the prefix, region, AMI or resource naming is hardcoded here.
LOADER="$(dirname "$0")/lab_env.sh"
[[ -f "$LOADER" ]] || LOADER="$LAB_DIR/lab2/lab_env.sh"
[[ -f "$LOADER" ]] || die "lab_env.sh not found next to this script."
# shellcheck disable=SC1090
source "$LOADER"
load_lab_env "$PREFIX_ARG" || exit 1
lab_env_report

command -v aws >/dev/null || die "aws CLI not found. Re-run the Lab 1 setup script."

CONFIGURED_REGION="$(aws configure get region 2>/dev/null || true)"
if [[ "$CONFIGURED_REGION" != "$REGION" ]]; then
  warn "Configured region is '${CONFIGURED_REGION:-<unset>}' but yours is '$REGION'."
  warn "Fixing it now: aws configure set region $REGION"
  aws configure set region "$REGION"
fi

# WSL2 clock drift after a Windows sleep makes every AWS call fail with an
# expired-signature error that looks like a credentials problem.
if ! CALLER=$(aws sts get-caller-identity --output text --query 'Arn' 2>&1); then
  echo "$CALLER" >&2
  die "AWS call failed. If it mentions signature/expired, your WSL clock drifted:
     sudo hwclock -s"
fi

ok "Identity: $CALLER"

# Names, key path and instance type all came from lab.env above.
VM_NAME="$VM1_NAME"

# ----------------------------------------------------------- lookup helpers -
sg_id_of()  { aws ec2 describe-security-groups \
                --filters "Name=group-name,Values=$SG_NAME" \
                --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo None; }

key_exists() { aws ec2 describe-key-pairs --key-names "$KEY_NAME" >/dev/null 2>&1; }

instance_id_of() { aws ec2 describe-instances \
                     --filters "Name=tag:Name,Values=$VM_NAME" \
                               "Name=instance-state-name,Values=pending,running,stopping,stopped" \
                     --query 'Reservations[0].Instances[0].InstanceId' --output text 2>/dev/null || echo None; }

# ----------------------------------------------------------------- status ---
if (( STATUS_ONLY )); then
  log "Current state in $REGION"
  SG_ID=$(sg_id_of); IID=$(instance_id_of)
  printf '    %-18s %s\n' "security group:" "$SG_NAME  -> ${SG_ID}"
  printf '    %-18s %s\n' "key pair:"       "$KEY_NAME -> $(key_exists && echo 'exists in AWS' || echo 'not in AWS')"
  printf '    %-18s %s\n' "pem file:"       "$KEY_FILE $( [[ -f $KEY_FILE ]] && echo "(mode $(stat -c '%a' "$KEY_FILE"))" || echo '(missing)')"
  printf '    %-18s %s\n' "instance:"       "$VM_NAME  -> ${IID}"
  if [[ "$IID" != "None" ]]; then
    aws ec2 describe-instances --instance-ids "$IID" --output table \
      --query 'Reservations[0].Instances[0].{ID:InstanceId,Type:InstanceType,State:State.Name,PublicIP:PublicIpAddress,Name:Tags[?Key==`Name`]|[0].Value}'
  fi
  exit 0
fi

# ------------------------------------------------- [1] create security group -
log "[1] Create a security group"

SG_ID=$(sg_id_of)
if [[ "$SG_ID" != "None" && -n "$SG_ID" ]]; then
  ok "Security group $SG_NAME already exists: $SG_ID"
else
  show "aws ec2 create-security-group --group-name $SG_NAME --description \"security group for development environment\""
  SG_ID=$(aws ec2 create-security-group \
            --group-name "$SG_NAME" \
            --description "security group for development environment" \
            --query 'GroupId' --output text)
  ok "Created $SG_NAME -> $SG_ID  (default VPC)"
fi

# ------------------------------------------------ [2] authorise ssh ingress --
log "[2] Authorise inbound traffic for ssh (tcp/22)"

HAS_22=$(aws ec2 describe-security-groups --group-ids "$SG_ID" \
          --query "SecurityGroups[0].IpPermissions[?FromPort==\`22\`] | length(@)" --output text)

if [[ "$HAS_22" != "0" ]]; then
  ok "Port 22 is already open on $SG_NAME"
else
  show "aws ec2 authorize-security-group-ingress --group-id $SG_ID --protocol tcp --port 22 --cidr 0.0.0.0/0"
  aws ec2 authorize-security-group-ingress \
    --group-id "$SG_ID" --protocol tcp --port 22 --cidr 0.0.0.0/0 >/dev/null
  ok "tcp/22 open to 0.0.0.0/0"
fi

# ------------------------------------------------------- [3] create key pair -
log "[3] Create a key pair"

if key_exists && [[ -f "$KEY_FILE" ]]; then
  ok "Key pair $KEY_NAME exists in AWS and $KEY_FILE is present"
elif key_exists && [[ ! -f "$KEY_FILE" ]]; then
  warn "AWS has key pair $KEY_NAME but the private key is missing locally."
  warn "AWS cannot re-issue a private key, so the pair must be recreated."
  read -r -p "    Delete and recreate $KEY_NAME? [y/N] " reply
  [[ "$reply" =~ ^[Yy]$ ]] || die "Cannot ssh without the .pem. Aborting."
  aws ec2 delete-key-pair --key-name "$KEY_NAME"
  key_exists || true
fi

if ! key_exists && [[ -f "$KEY_FILE" ]]; then
  # The .pem survives on disk but AWS no longer knows the key name - usually
  # the key pair was deleted from the console. Creating a fresh pair here would
  # redirect over the .pem and destroy the only key that still opens any
  # instance already running. Re-import the existing public half instead.
  warn "$KEY_FILE exists but AWS has no key pair named $KEY_NAME."
  warn "Importing your existing key rather than generating a new one."
  warn "(Instances already running keep working - their authorized_keys is unchanged.)"

  PUB_TMP="$(mktemp)"
  trap 'rm -f "$PUB_TMP"' EXIT
  if ! ssh-keygen -y -f "$KEY_FILE" > "$PUB_TMP" 2>/dev/null; then
    die "Could not derive a public key from $KEY_FILE.
     The file may be corrupt or not a private key. Inspect it with:
       head -1 $KEY_FILE"
  fi
  show "aws ec2 import-key-pair --key-name $KEY_NAME --public-key-material fileb://<derived>"
  aws ec2 import-key-pair --key-name "$KEY_NAME" \
    --public-key-material "fileb://$PUB_TMP" >/dev/null
  ok "Re-imported $KEY_NAME - your existing $KEY_FILE still works"

elif ! key_exists; then
  show "aws ec2 create-key-pair --key-name $KEY_NAME --query 'KeyMaterial' --output text > $KEY_FILE"
  aws ec2 create-key-pair --key-name "$KEY_NAME" \
    --query 'KeyMaterial' --output text > "$KEY_FILE"
  ok "Private key written to $KEY_FILE"
fi

chmod 400 "$KEY_FILE"
ok "Permissions on $KEY_FILE set to 400"

# Keep the pem on the Linux filesystem. A .pem under /mnt/c inherits Windows
# DrvFs permissions and ssh will refuse it as "too open" no matter what chmod says.
case "$KEY_FILE" in
  /mnt/c/*) warn "This key lives on the Windows drive - ssh will likely reject its permissions." ;;
esac

# -------------------------------------------------- [4] launch the instance --
log "[4] Create the instance"

INSTANCE_ID=$(instance_id_of)
if [[ "$INSTANCE_ID" != "None" && -n "$INSTANCE_ID" ]]; then
  ok "Instance tagged $VM_NAME already exists: $INSTANCE_ID (not launching another)"
else
  # The lab sheet writes  --security-group-ids <prefix>-sg  which fails:
  # that flag wants the sg-xxxxxxxx id, not the group name. We pass $SG_ID.
  show "aws ec2 run-instances --image-id $AMI --security-group-ids $SG_ID --count 1 \\"
  show "    --instance-type $INSTANCE_TYPE --key-name $KEY_NAME --query 'Instances[0].InstanceId'"
  INSTANCE_ID=$(aws ec2 run-instances \
                  --image-id "$AMI" \
                  --security-group-ids "$SG_ID" \
                  --count 1 \
                  --instance-type "$INSTANCE_TYPE" \
                  --key-name "$KEY_NAME" \
                  --query 'Instances[0].InstanceId' --output text)
  ok "Launched $INSTANCE_ID ($INSTANCE_TYPE)"
fi

# --------------------------------------------------------------- [5] tag it --
log "[5] Add a tag to your instance"

show "aws ec2 create-tags --resources $INSTANCE_ID --tags Key=Name,Value=$VM_NAME"
aws ec2 create-tags --resources "$INSTANCE_ID" --tags "Key=Name,Value=$VM_NAME"
ok "Name tag set to $VM_NAME"

# ----------------------------------------------------------- [6] public IP ---
log "[6] Get the public IP address"

info "waiting for the instance to reach 'running' ..."
aws ec2 wait instance-running --instance-ids "$INSTANCE_ID"

show "aws ec2 describe-instances --instance-ids $INSTANCE_ID --query 'Reservations[0].Instances[0].PublicIpAddress'"
PUBLIC_IP=$(aws ec2 describe-instances --instance-ids "$INSTANCE_ID" \
              --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
[[ "$PUBLIC_IP" != "None" && -n "$PUBLIC_IP" ]] || die "No public IP assigned - check the subnet has auto-assign public IP enabled."
ok "Public IP: $PUBLIC_IP"

aws ec2 describe-instances --instance-ids "$INSTANCE_ID" --output table \
  --query 'Reservations[0].Instances[0].{Name:Tags[?Key==`Name`]|[0].Value,ID:InstanceId,Type:InstanceType,State:State.Name,PublicIP:PublicIpAddress,AZ:Placement.AvailabilityZone}'

# ------------------------------------------------------------- save state ----
# Merged back into lab.env so later labs and the verify script can read them.
# Existing keys are updated in place; comments and ordering survive.
lab_env_set SG_NAME  "$SG_NAME"
lab_env_set SG_ID    "$SG_ID"
lab_env_set KEY_NAME "$KEY_NAME"
lab_env_set KEY_FILE "$KEY_FILE"
lab_env_set VM1_NAME "$VM_NAME"
lab_env_set VM1_ID   "$INSTANCE_ID"
lab_env_set VM1_IP   "$PUBLIC_IP"
ok "Saved SG_ID, VM1_ID and VM1_IP into $LAB_ENV_FILE"

# ------------------------------------------------------------------ [7] ssh --
log "[7] Connect to the instance via ssh"

SSH_CMD="ssh -i $KEY_FILE ubuntu@$PUBLIC_IP"
show "$SSH_CMD"

if (( DO_SSH )); then
  info "the instance needs ~30-60s after 'running' before sshd answers"
  for i in $(seq 1 12); do
    if ssh -i "$KEY_FILE" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 \
           -o BatchMode=yes "ubuntu@$PUBLIC_IP" true 2>/dev/null; then
      ok "sshd is up"
      break
    fi
    printf '    %s.%s' "$c_dim" "$c_reset"; sleep 10
  done
  echo
  exec ssh -i "$KEY_FILE" -o StrictHostKeyChecking=accept-new "ubuntu@$PUBLIC_IP"
fi

# ---------------------------------------------------------------- [8] hints --
log "[8] List the instance in the AWS Management Console"

cat <<EOF

  Console (already scoped to your region):
    https://${REGION}.console.aws.amazon.com/ec2/home?region=${REGION}#Instances:

  For the live demo of Checkpoint 1, have the console open showing:
    - the instance named ${VM_NAME}, type ${INSTANCE_TYPE}
    - its Public IPv4 address (${PUBLIC_IP})

  Next:
    python3 lab2_boto3.py        # creates ${VM2_NAME} (activate the venv first: labenv)
    ./lab2_docker.sh up          # httpd container -> http://localhost
    ./lab2_verify.sh             # rehearse every checkpoint before you demo

  Cleanup for Checkpoint 3 must be done in the AWS Management Console.
  Terminating from the CLI does not count.

EOF
