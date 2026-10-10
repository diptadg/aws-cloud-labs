#!/usr/bin/env bash
# CITS5503 Lab 6 - read-only rehearsal of every checkpoint.
# Creates nothing, changes nothing, deletes nothing.
#
#   ./lab6_verify.sh

set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lab6_lib.sh
source "${SCRIPT_DIR}/lab6_lib.sh"

lab_env_load
lab_env_require LAB_PREFIX AWS_REGION AWS_AMI

VM_NAME="${LAB6_VM1_NAME:-${LAB_PREFIX}-vm1}"
TG_NAME="${LAB6_TG_NAME:-${LAB_PREFIX}-tg-lab6}"
ALB_NAME="${LAB6_ALB_NAME:-${LAB_PREFIX}-alb-lab6}"
AWS_DDB_TABLE="${AWS_DDB_TABLE:-UserFiles}"
LOCAL_TABLE="${DDB_TABLE:-CloudFiles}"
DDB_PORT="${DDB_PORT:-8000}"
LOCAL_ENDPOINT="${DDB_ENDPOINT:-http://localhost:${DDB_PORT}}"

pass=0; fail=0
_ok()  { ok  "$*"; pass=$((pass+1)); }
_bad() { bad "$*"; fail=$((fail+1)); }

echo "=================================================="
echo "  CITS5503 Lab 6 - verification"
echo "  ${LAB_PREFIX} · ${AWS_REGION} · $(date '+%Y-%m-%d %H:%M')"
echo "=================================================="

# ------------------------------------------------------------ preflight
hdr "Preflight"
if aws sts get-caller-identity --query Arn --output text >/tmp/lab6_arn 2>/dev/null; then
  _ok "AWS identity $(cat /tmp/lab6_arn)"
else
  _bad "aws sts get-caller-identity failed"
  note "If the error mentions an expired signature, resync the WSL clock: sudo hwclock -s"
fi
rm -f /tmp/lab6_arn

CONFIGURED_REGION="$(aws configure get region 2>/dev/null)"
[[ "${CONFIGURED_REGION}" == "${AWS_REGION}" ]] \
  && _ok "aws configure region is ${CONFIGURED_REGION}" \
  || note "aws configure region is '${CONFIGURED_REGION}', lab.env says ${AWS_REGION} (scripts pass --region explicitly)"

# ================================================ Checkpoint 1: EC2 + Django
hdr "Checkpoint 1 - EC2, Django and nginx"

read -r VM_ID VM_TYPE VM_AZ VM_STATE VM_IP VM_AMI <<<"$(aws ec2 describe-instances \
    --region "${AWS_REGION}" \
    --filters "Name=tag:Name,Values=${VM_NAME}" \
              "Name=instance-state-name,Values=pending,running" \
    --query 'Reservations[0].Instances[0].[InstanceId,InstanceType,Placement.AvailabilityZone,State.Name,PublicIpAddress,ImageId]' \
    --output text 2>/dev/null)"

if [[ -z "${VM_ID:-}" || "${VM_ID}" == "None" ]]; then
  _bad "No running instance named ${VM_NAME} - run: python3 lab6_ec2.py"
  VM_IP=""
else
  _ok "${VM_NAME}  ${VM_ID}  ${VM_TYPE}  ${VM_AZ}  ${VM_STATE}"
  [[ "${VM_TYPE}" == "t3.micro" ]] \
    && _ok "instance type is t3.micro" \
    || _bad "instance type is ${VM_TYPE}, the lab requires t3.micro"
  [[ "${VM_AZ}" == ${AWS_REGION}* ]] \
    && _ok "instance is in the assigned region ${AWS_REGION}" \
    || _bad "instance is in ${VM_AZ}, expected a zone in ${AWS_REGION}"
  [[ -n "${VM_IP}" && "${VM_IP}" != "None" ]] \
    && _ok "public IP ${VM_IP}" \
    || _bad "instance has no public IP"
  [[ "${VM_AMI}" == "${AWS_AMI}" ]] \
    && _ok "AMI ${VM_AMI} matches AWS_AMI in lab.env" \
    || note "AMI is ${VM_AMI}, lab.env says ${AWS_AMI}"
fi

if [[ -n "${VM_IP}" && "${VM_IP}" != "None" ]]; then
  CODE="$(curl -s -m 20 -o /tmp/lab6_page -w '%{http_code}' "http://${VM_IP}/polls/" || true)"
  if [[ "${CODE}" == "200" ]]; then
    _ok "http://${VM_IP}/polls/ returned 200"
    grep -q "Hello, world." /tmp/lab6_page \
      && _ok "the page contains 'Hello, world.'" \
      || note "the page loaded but 'Hello, world.' is not in it"
  else
    _bad "http://${VM_IP}/polls/ returned ${CODE:-nothing}"
    note "UWA's network commonly blocks outbound port 80 - retest on a hotspot"
    note "If it is genuinely down: ssh in and run  sudo systemctl status django-polls nginx"
  fi
fi

# =============================================== Checkpoint 2: Load balancer
hdr "Checkpoint 2 - Application Load Balancer"

TG_ARN="$(aws elbv2 describe-target-groups --region "${AWS_REGION}" --names "${TG_NAME}" \
          --query 'TargetGroups[0].TargetGroupArn' --output text 2>/dev/null)"

if [[ -z "${TG_ARN}" || "${TG_ARN}" == "None" ]]; then
  _bad "Target group ${TG_NAME} not found - run: python3 lab6_alb.py"
else
  _ok "target group ${TG_NAME}"
  read -r HC_PATH HC_INTERVAL HC_PROTO <<<"$(aws elbv2 describe-target-groups \
      --region "${AWS_REGION}" --target-group-arns "${TG_ARN}" \
      --query 'TargetGroups[0].[HealthCheckPath,HealthCheckIntervalSeconds,HealthCheckProtocol]' \
      --output text 2>/dev/null)"
  [[ "${HC_PATH}" == "/polls/" ]] \
    && _ok "health check path is /polls/" \
    || _bad "health check path is ${HC_PATH}, expected /polls/"
  [[ "${HC_INTERVAL}" == "30" ]] \
    && _ok "health check interval is 30 seconds" \
    || _bad "health check interval is ${HC_INTERVAL}s, expected 30"

  TARGETS="$(aws elbv2 describe-target-health --region "${AWS_REGION}" --target-group-arn "${TG_ARN}" \
             --query 'TargetHealthDescriptions[].[Target.Id,Target.Port,TargetHealth.State]' \
             --output text 2>/dev/null)"
  if [[ -z "${TARGETS}" ]]; then
    _bad "no targets registered in ${TG_NAME}"
  else
    echo "${TARGETS}" | sed 's/^/         /'
    if echo "${TARGETS}" | grep -q "${VM_ID:-__none__}"; then
      _ok "${VM_NAME} is registered as a target"
    else
      _bad "${VM_NAME} is not registered as a target"
    fi
    if echo "${TARGETS}" | grep -qw "healthy"; then
      _ok "target status is healthy"
    else
      _bad "no target is healthy yet"
      note "It takes ~1 minute after the app comes up; re-run this script."
    fi
  fi
fi

read -r LB_ARN LB_DNS LB_STATE LB_SCHEME LB_TYPE <<<"$(aws elbv2 describe-load-balancers \
    --region "${AWS_REGION}" --names "${ALB_NAME}" \
    --query 'LoadBalancers[0].[LoadBalancerArn,DNSName,State.Code,Scheme,Type]' \
    --output text 2>/dev/null)"

if [[ -z "${LB_ARN:-}" || "${LB_ARN}" == "None" ]]; then
  _bad "Load balancer ${ALB_NAME} not found - run: python3 lab6_alb.py"
  LB_DNS=""
else
  _ok "${ALB_NAME}  state=${LB_STATE}  ${LB_SCHEME}  ${LB_TYPE}"
  [[ "${LB_STATE}" == "active" ]] || _bad "ALB state is ${LB_STATE}, expected active"
  [[ "${LB_SCHEME}" == "internet-facing" ]] || _bad "ALB scheme is ${LB_SCHEME}"

  LISTENER="$(aws elbv2 describe-listeners --region "${AWS_REGION}" --load-balancer-arn "${LB_ARN}" \
              --query 'Listeners[?Port==`80`].[Protocol,Port]' --output text 2>/dev/null)"
  [[ -n "${LISTENER}" ]] \
    && _ok "listener ${LISTENER} present" \
    || _bad "no HTTP:80 listener on ${ALB_NAME}"
  note "DNS: ${LB_DNS}"
fi

# ================================================= Checkpoint 3: DynamoDB
hdr "Checkpoint 3 - AWS DynamoDB and the web page"

AWS_STATUS="$(aws dynamodb describe-table --region "${AWS_REGION}" --table-name "${AWS_DDB_TABLE}" \
              --query 'Table.TableStatus' --output text 2>/dev/null)"
if [[ "${AWS_STATUS}" == "ACTIVE" ]]; then
  _ok "AWS DynamoDB table ${AWS_DDB_TABLE} is ACTIVE"
  AWS_NAMES="$(aws dynamodb scan --region "${AWS_REGION}" --table-name "${AWS_DDB_TABLE}" \
               --query 'Items[].fileName.S' --output text 2>/dev/null)"
  AWS_COUNT="$(echo "${AWS_NAMES}" | wc -w)"
  if [[ "${AWS_COUNT}" -gt 0 ]]; then
    _ok "${AWS_COUNT} record(s): ${AWS_NAMES}"
  else
    _bad "table ${AWS_DDB_TABLE} is empty - run: python3 lab6_ddb_sync.py"
  fi
else
  _bad "AWS DynamoDB table ${AWS_DDB_TABLE} not found - run: python3 lab6_ddb_sync.py"
  AWS_NAMES=""
fi

if curl -s -m 5 "${LOCAL_ENDPOINT}" >/dev/null 2>&1; then
  _ok "DynamoDB Local is reachable at ${LOCAL_ENDPOINT}"
  LOCAL_COUNT="$(aws dynamodb scan --table-name "${LOCAL_TABLE}" --endpoint-url "${LOCAL_ENDPOINT}" \
                 --select COUNT --query Count --output text 2>/dev/null)"
  if [[ -n "${LOCAL_COUNT}" && "${LOCAL_COUNT}" != "None" ]]; then
    _ok "local table ${LOCAL_TABLE} scans to ${LOCAL_COUNT} record(s)"
  else
    _bad "local table ${LOCAL_TABLE} not found on DynamoDB Local"
  fi
else
  _bad "DynamoDB Local not reachable at ${LOCAL_ENDPOINT}"
  note "The live local scan is part of this checkpoint - start it from the Lab 3 directory:"
  note "  ./dynamodb_local.sh start"
fi

if [[ -n "${LB_DNS:-}" && "${LB_DNS}" != "None" ]]; then
  CODE="$(curl -s -m 25 -o /tmp/lab6_alb_page -w '%{http_code}' "http://${LB_DNS}/polls/" || true)"
  if [[ "${CODE}" == "200" ]]; then
    _ok "http://${LB_DNS}/polls/ returned 200"
    missing=""
    for name in ${AWS_NAMES}; do
      grep -q "${name}" /tmp/lab6_alb_page || missing="${missing} ${name}"
    done
    if [[ -n "${AWS_NAMES}" && -z "${missing}" ]]; then
      _ok "the page lists every file name from ${AWS_DDB_TABLE}"
    elif [[ -n "${missing}" ]]; then
      _bad "missing from the page:${missing}"
    fi
    grep -q "Could not read DynamoDB" /tmp/lab6_alb_page \
      && _bad "the page reports a DynamoDB error - re-run ./lab6_deploy.sh to reinstall credentials"
  else
    _bad "http://${LB_DNS}/polls/ returned ${CODE:-nothing}"
    note "A new ALB needs a minute or two, and UWA's network may block port 80"
  fi
  rm -f /tmp/lab6_alb_page
fi
rm -f /tmp/lab6_page

# =================================================== Checkpoint 4: cleanup
hdr "Checkpoint 4 - Cleanup readiness"
note "Done in the AWS Management Console, in this order:"
note "  1. EC2 > Load Balancers  > ${ALB_NAME} > Actions > Delete"
note "     (then EC2 > Target Groups > ${TG_NAME} > Delete)"
note "  2. EC2 > Instances       > ${VM_NAME} > Instance state > Terminate"
note "  3. DynamoDB > Tables     > ${AWS_DDB_TABLE} > Delete"
note "  4. EC2 > Elastic IPs     > release anything allocated"
note "Nothing here allocates an Elastic IP, so step 4 is normally an empty list -"
note "still open the page so it can be seen."

echo
echo "=================================================="
echo "  ${pass} passed, ${fail} failed"
echo "=================================================="
[[ ${fail} -eq 0 ]] || exit 1
