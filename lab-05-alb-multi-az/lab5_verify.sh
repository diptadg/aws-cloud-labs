#!/usr/bin/env bash
#
# CITS5503 Lab 5 - read-only rehearsal.
# Checks everything the checkpoints ask for. Creates and deletes nothing.
#
#   Checkpoint 1 : two instances, correct names, t3.micro, two different AZs,
#                  plus a running internet-facing ALB across two subnets
#   Checkpoint 2 : each instance's page title shows its instance name
#   Checkpoint 3 : cleanup - listed here only as a reminder, done in the console
#
# Usage:  ./lab5_verify.sh

set -uo pipefail

# All configuration comes from lab.env - nothing is hard-coded here.
# Override the file location with:  LAB_ENV=/path/to/file ./lab5_verify.sh
ENV_FILE="${LAB_ENV:-${HOME}/cits5503/lab.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Config file not found: $ENV_FILE" >&2
  echo "It must define LAB_PREFIX, AWS_REGION and AWS_AMI." >&2
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

# Written into lab.env by lab5_alb.py; the fallbacks cover running this first.
VM_NAMES=("${LAB5_VM1_NAME:-${LAB_PREFIX}-vm1}" "${LAB5_VM2_NAME:-${LAB_PREFIX}-vm2}")
ALB_NAME="${LAB5_ALB_NAME:-${LAB_PREFIX}-alb}"
TG_NAME="${LAB5_TG_NAME:-${LAB_PREFIX}-tg}"
WANT_TYPE="${LAB5_INSTANCE_TYPE:-t3.micro}"

PASS=0
FAIL=0
ok()   { echo "  [ ok ] $*"; PASS=$((PASS+1)); }
bad()  { echo "  [FAIL] $*"; FAIL=$((FAIL+1)); }
note() { echo "         $*"; }

echo "==================================================================="
echo " Lab 5 verification - region $AWS_REGION"
echo "==================================================================="

# ---------------------------------------------------------------- identity
echo
echo "AWS identity"
ARN="$(aws sts get-caller-identity --query Arn --output text 2>/dev/null)"
if [[ -n "$ARN" && "$ARN" != "None" ]]; then
  ok "$ARN"
else
  bad "aws sts get-caller-identity failed (expired credentials or clock drift?)"
  note "Clock drift fix: sudo hwclock -s"
fi

# ------------------------------------------------------- Checkpoint 1: EC2
echo
echo "Checkpoint 1a - EC2 instances"
declare -a AZS=() IPS=() NAMES=()
for NAME in "${VM_NAMES[@]}"; do
  read -r ID TYPE AZ STATE IP AMI <<<"$(aws ec2 describe-instances \
      --region "$AWS_REGION" \
      --filters "Name=tag:Name,Values=$NAME" \
                "Name=instance-state-name,Values=pending,running" \
      --query 'Reservations[0].Instances[0].[InstanceId,InstanceType,Placement.AvailabilityZone,State.Name,PublicIpAddress,ImageId]' \
      --output text 2>/dev/null)"

  if [[ -z "${ID:-}" || "$ID" == "None" ]]; then
    bad "$NAME not found in a running state"
    continue
  fi

  ok "$NAME  $ID  $TYPE  $AZ  $STATE  ${IP}"
  [[ "$TYPE" == "$WANT_TYPE" ]] || bad "$NAME is $TYPE - the lab requires $WANT_TYPE"
  [[ "$IP" != "None" && -n "$IP" ]] || bad "$NAME has no public IP"
  if [[ -n "${AWS_AMI:-}" && "$AMI" != "$AWS_AMI" ]]; then
    note "$NAME runs $AMI, but AWS_AMI in lab.env is $AWS_AMI"
  fi
  AZS+=("$AZ")
  IPS+=("$IP")
  NAMES+=("$NAME")
done

if [[ ${#AZS[@]} -eq 2 ]]; then
  if [[ "${AZS[0]}" != "${AZS[1]}" ]]; then
    ok "Instances are in two different AZs: ${AZS[0]} and ${AZS[1]}"
  else
    bad "Both instances are in ${AZS[0]} - they must be in different AZs"
  fi
fi

# ------------------------------------------------------- Checkpoint 1: ALB
echo
echo "Checkpoint 1b - Application Load Balancer"
read -r LB_ARN LB_DNS LB_STATE LB_SCHEME LB_TYPE <<<"$(aws elbv2 describe-load-balancers \
    --region "$AWS_REGION" --names "$ALB_NAME" \
    --query 'LoadBalancers[0].[LoadBalancerArn,DNSName,State.Code,Scheme,Type]' \
    --output text 2>/dev/null)"

if [[ -z "${LB_ARN:-}" || "$LB_ARN" == "None" ]]; then
  bad "Load balancer $ALB_NAME not found"
else
  ok "$ALB_NAME  state=$LB_STATE  $LB_SCHEME  $LB_TYPE"
  [[ "$LB_STATE"  == "active" ]]          || bad "ALB state is $LB_STATE, expected active"
  [[ "$LB_SCHEME" == "internet-facing" ]] || bad "ALB scheme is $LB_SCHEME, expected internet-facing"

  LB_AZS="$(aws elbv2 describe-load-balancers --region "$AWS_REGION" --names "$ALB_NAME" \
            --query 'LoadBalancers[0].AvailabilityZones[].[ZoneName,SubnetId]' --output text)"
  AZ_COUNT="$(echo "$LB_AZS" | grep -c .)"
  if [[ "$AZ_COUNT" -ge 2 ]]; then
    ok "ALB spans $AZ_COUNT subnets:"
    echo "$LB_AZS" | sed 's/^/         /'
  else
    bad "ALB spans only $AZ_COUNT subnet(s) - it needs at least two"
  fi
  note "DNS: $LB_DNS"
fi

# ------------------------------------------------------------ target group
echo
echo "Target group health"
TG_ARN="$(aws elbv2 describe-target-groups --region "$AWS_REGION" --names "$TG_NAME" \
          --query 'TargetGroups[0].TargetGroupArn' --output text 2>/dev/null)"
if [[ -z "$TG_ARN" || "$TG_ARN" == "None" ]]; then
  bad "Target group $TG_NAME not found"
else
  HEALTH="$(aws elbv2 describe-target-health --region "$AWS_REGION" --target-group-arn "$TG_ARN" \
            --query 'TargetHealthDescriptions[].[Target.Id,TargetHealth.State]' --output text)"
  if [[ -z "$HEALTH" ]]; then
    bad "No targets registered in $TG_NAME"
  else
    echo "$HEALTH" | while read -r tid state; do
      if [[ "$state" == "healthy" ]]; then echo "  [ ok ] $tid $state"
      else echo "  [warn] $tid $state (install Apache2 with ./lab5_web.sh, then wait ~30s)"; fi
    done
  fi
fi

# ------------------------------------------- Checkpoint 2: page titles
echo
echo "Checkpoint 2 - page title per instance"
for i in "${!IPS[@]}"; do
  IP="${IPS[$i]}"
  EXPECT="${NAMES[$i]}"
  [[ "$IP" == "None" || -z "$IP" ]] && continue
  TITLE="$(curl -s --max-time 6 "http://${IP}/" | grep -o '<title>[^<]*</title>' | head -1)"
  if [[ -z "$TITLE" ]]; then
    bad "http://${IP}/ returned nothing"
    note "On the UWA network outbound port 80 is often blocked - retry on another network"
  elif [[ "$TITLE" == *"$EXPECT"* ]]; then
    ok "http://${IP}/  ->  $TITLE"
  else
    bad "http://${IP}/  ->  $TITLE  (expected $EXPECT)"
  fi
done

# ------------------------------------------------------- ALB round robin
if [[ -n "${LB_DNS:-}" && "$LB_DNS" != "None" ]]; then
  echo
  echo "Load balancing sanity check (6 requests through the ALB)"
  for _ in $(seq 1 6); do
    T="$(curl -s --max-time 6 "http://${LB_DNS}/" | grep -o '<title>[^<]*</title>' | head -1)"
    echo "         ${T:-<no response yet>}"
    sleep 1
  done
  note "Both instance names should appear as requests alternate between targets."
fi

# --------------------------------------------------- Checkpoint 3 reminder
echo
echo "Checkpoint 3 - cleanup (do this in the AWS Management Console, in order)"
echo "         1. EC2 > Load Balancers   -> delete $ALB_NAME"
echo "         2. EC2 > Instances        -> terminate ${VM_NAMES[0]} and ${VM_NAMES[1]}"
echo "         3. EC2 > Elastic IPs      -> release any allocated addresses"
ALLOC="$(aws ec2 describe-addresses --region "$AWS_REGION" \
         --query 'Addresses[].[PublicIp,AllocationId]' --output text 2>/dev/null)"
if [[ -n "$ALLOC" ]]; then
  echo "         Elastic IPs currently allocated:"
  echo "$ALLOC" | sed 's/^/           /'
else
  echo "         (no Elastic IPs allocated - nothing to release)"
fi

echo
echo "==================================================================="
echo " passed: $PASS   failed: $FAIL"
echo "==================================================================="
[[ $FAIL -eq 0 ]] || exit 1
